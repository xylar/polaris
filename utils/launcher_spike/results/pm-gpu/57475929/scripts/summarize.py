#!/usr/bin/env python3
"""
Summarize a launcher-spike results directory.

Reports, per test: sequential launch rate, the concurrency actually
observed (from payload start/end timestamps), whether concurrent launches
landed on disjoint cores, and any launcher retry/error messages.

Stdlib only, so it can run on any machine without the Polaris environment.
"""

import os
import statistics
import sys

RETRY_MARKERS = (
    'step creation temporarily disabled',
    'Requested nodes are busy',
    'Requested node configuration is not available',
    'error: Unable to create step',
    'launch failed',
    'PMI',
    'pals',
)


def parse_kv(path):
    values = {}
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if '=' in line:
                key, _, value = line.partition('=')
                values[key] = value
    return values


def parse_cpu_list(text):
    cores: set[int] = set()
    if not text or text == 'unknown':
        return cores
    for chunk in text.split(','):
        chunk = chunk.strip()
        if not chunk:
            continue
        if '-' in chunk:
            low, _, high = chunk.partition('-')
            try:
                cores.update(range(int(low), int(high) + 1))
            except ValueError:
                continue
        else:
            try:
                cores.add(int(chunk))
            except ValueError:
                continue
    return cores


def parse_gpu_env(text):
    """Devices named by a payload's gpu_env field, however it was written."""
    devices: set[str] = set()
    if not text:
        return devices
    for chunk in text.split(';'):
        chunk = chunk.strip()
        if not chunk:
            continue
        _, _, value = chunk.rpartition('=')
        for device in value.split(','):
            device = device.strip()
            if device:
                devices.add(device)
    return devices


def max_overlap(intervals):
    """Largest number of intervals active at any instant."""
    events = []
    for start, end in intervals:
        events.append((start, 1))
        events.append((end, -1))
    events.sort()
    current = 0
    peak = 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


def report_sequential(test_dir, name):
    timings = os.path.join(test_dir, 'timings.kv')
    if not os.path.exists(timings):
        return False
    seconds = []
    failures = 0
    for line in open(timings):
        fields = dict(
            part.split('=', 1) for part in line.split() if '=' in part
        )
        if fields.get('rc', '0') != '0':
            failures += 1
        try:
            seconds.append(float(fields['seconds']))
        except (KeyError, ValueError):
            continue
    if not seconds:
        print(f'  {name}: no timings recorded')
        return True
    median = statistics.median(seconds)
    rate = 60.0 / median if median > 0 else float('inf')
    print(
        f'  {name}: {len(seconds)} sequential launches, '
        f'median {median:.2f}s, max {max(seconds):.2f}s '
        f'-> ~{rate:.1f} launches/min'
    )
    if failures:
        print(f'      {failures} launch(es) returned nonzero')
    return True


class SlotRun:
    """One concurrent slot: when it ran, and what it was given."""

    def __init__(self, slot, start, end, cores_by_host, gpus):
        self.slot = slot
        self.start = start
        self.end = end
        self.cores_by_host = cores_by_host
        self.gpus = gpus


def collect_slots(test_dir):
    """Group the payload output files in a test directory by slot."""
    slots: dict[str, list[dict[str, str]]] = {}
    for entry in sorted(os.listdir(test_dir)):
        if not entry.endswith('.kv') or entry == 'timings.kv':
            continue
        values = parse_kv(os.path.join(test_dir, entry))
        slot = values.get('slot')
        if slot is not None:
            slots.setdefault(slot, []).append(values)
    return slots


def build_slot_runs(slots):
    runs = []
    for slot, ranks in sorted(slots.items(), key=lambda item: int(item[0])):
        starts = [float(r['t_start']) for r in ranks if 't_start' in r]
        ends = [float(r['t_end']) for r in ranks if 't_end' in r]
        if not starts or not ends:
            continue
        cores_by_host: dict[str, set[int]] = {}
        gpus: set[str] = set()
        for rank in ranks:
            host = rank.get('host', '?')
            cores_by_host.setdefault(host, set()).update(
                parse_cpu_list(rank.get('cpus_allowed', ''))
            )
            gpus |= parse_gpu_env(rank.get('gpu_env', ''))
        runs.append(SlotRun(slot, min(starts), max(ends), cores_by_host, gpus))
    return runs


def overlapping_pairs(runs):
    """Slot pairs that were genuinely running at the same moment.

    Steps that serialized reuse the same cores harmlessly, so comparing
    them would report a collision that hides the far more important fact
    that they never overlapped.
    """
    for index, first in enumerate(runs):
        for second in runs[index + 1 :]:
            if min(first.end, second.end) > max(first.start, second.start):
                yield first, second


def core_collisions(runs):
    found = []
    for first, second in overlapping_pairs(runs):
        for host, cores in first.cores_by_host.items():
            shared = cores & second.cores_by_host.get(host, set())
            if shared:
                found.append(
                    f'slots {first.slot} and {second.slot} overlapped in '
                    f'time and share cores {sorted(shared)[:6]} on {host}'
                )
    return found


def gpu_collisions(runs):
    found = []
    for first, second in overlapping_pairs(runs):
        shared = first.gpus & second.gpus
        if shared:
            found.append(
                f'slots {first.slot} and {second.slot} overlapped in time '
                f'and share GPU(s) {sorted(shared)}'
            )
    return found


def print_collisions(label, found, ok_message, concurrent):
    if found:
        print(f'      {label}:')
        for collision in found[:6]:
            print(f'        {collision}')
    elif concurrent:
        print(f'      {ok_message}')
    else:
        print(
            f'      (no {label.split()[0].lower()} verdict -- slots never '
            f'ran at the same time)'
        )


def report_concurrent(test_dir, name):
    slots = collect_slots(test_dir)
    if not slots:
        print(f'  {name}: no payload output -- launches probably failed')
        return

    runs = build_slot_runs(slots)
    peak = max_overlap([(r.start, r.end) for r in runs]) if runs else 0
    hosts = sorted({host for r in runs for host in r.cores_by_host})
    widths = sorted(
        {
            len(parse_cpu_list(r.get('cpus_allowed', '')))
            for ranks in slots.values()
            for r in ranks
        }
    )
    print(
        f'  {name}: {len(slots)} slot(s) reported, peak concurrency {peak}, '
        f'hosts {hosts}, cores/rank {widths}'
    )

    if any(r.gpus for r in runs):
        assigned = ' '.join(
            f'slot{r.slot}={sorted(r.gpus)}' for r in runs if r.gpus
        )
        print(f'      GPUs: {assigned}')
        print_collisions(
            'GPU COLLISIONS',
            gpu_collisions(runs),
            'GPUs are disjoint across slots',
            peak > 1,
        )
    print_collisions(
        'CORE COLLISIONS',
        core_collisions(runs),
        'cores are disjoint across slots',
        peak > 1,
    )


def report_messages(test_dir, name):
    hits: dict[str, int] = {}
    for entry in sorted(os.listdir(test_dir)):
        if not entry.endswith('.err'):
            continue
        path = os.path.join(test_dir, entry)
        try:
            text = open(path, errors='replace').read()
        except OSError:
            continue
        for line in text.splitlines():
            if any(marker.lower() in line.lower() for marker in RETRY_MARKERS):
                hits[line.strip()] = hits.get(line.strip(), 0) + 1
    if hits:
        print('      launcher messages:')
        for line, count in sorted(hits.items(), key=lambda item: -item[1])[:5]:
            print(f'        [{count}x] {line[:140]}')


def main():
    if len(sys.argv) != 2:
        print(f'usage: {sys.argv[0]} <results-dir>', file=sys.stderr)
        return 1
    root = sys.argv[1]

    meta_path = os.path.join(root, 'meta.kv')
    if os.path.exists(meta_path):
        meta = parse_kv(meta_path)
        print(
            '  run: {machine} / {scheduler} job {job_id}, {nodes} node(s), '
            '{cores_on_node} cores/node'.format(
                machine=meta.get('machine', '?'),
                scheduler=meta.get('scheduler', '?'),
                job_id=meta.get('job_id', '?'),
                nodes=meta.get('nodes', '?'),
                cores_on_node=meta.get('cores_on_node', '?'),
            )
        )
        print(
            '       {slots} slot(s) x {ranks} rank(s) x {cpus} cpu(s), '
            'mpi payload {mpi_payload}'.format(
                slots=meta.get('slots', '?'),
                ranks=meta.get('ranks', '?'),
                cpus=meta.get('cpus', '?'),
                mpi_payload=meta.get('mpi_payload', '?'),
            )
        )
        print(f'       {meta.get("scheduler_version", "?")}')
        status = meta.get('status', 'unknown')
        if status != 'complete':
            print(f'       RUN STATUS: {status} -- this run did not finish')
        print()

    tests = sorted(
        name
        for name in os.listdir(root)
        if os.path.isdir(os.path.join(root, name)) and name != 'scripts'
    )
    if not tests:
        print('no test directories found')
        return 1
    for name in tests:
        test_dir = os.path.join(root, name)
        if not report_sequential(test_dir, name):
            report_concurrent(test_dir, name)
        report_messages(test_dir, name)
    print()
    print('Reading the results:')
    print(
        '  A sequential fast + B peak concurrency == slots'
        '  -> Tier A works, use --overlap --exact'
    )
    print(
        '  A sequential fast + B peak concurrency == 1'
        '   -> job-step exclusivity; check B0 control and flags'
    )
    print(
        '  A sequential slow (~1/min)'
        '                   -> genuine throttling; Tier A is out, '
        'evaluate Flux'
    )
    print(
        '  C/D peak < slots or core collisions'
        '            -> placement is not enforced at MPI width'
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
