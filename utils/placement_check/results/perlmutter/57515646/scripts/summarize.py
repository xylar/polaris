#!/usr/bin/env python3
"""
Summarize a placement-check results directory.

Reports, per check: what each launch was given, what it could actually see,
whether concurrent launches genuinely overlapped in time, and whether their
cores and GPUs were disjoint.

Order matters here.  A "cores are disjoint" verdict is meaningless when the
launches never ran at the same time, so overlap is established first and the
disjointness verdict is withheld otherwise.

Stdlib only, so it can run on any machine without the Polaris environment.
"""

import os
import sys

# lines in a launcher's stderr that are worth surfacing in the summary
RETRY_MARKERS = (
    'exceeded memory limit',
    'oom-kill',
    'Out Of Memory',
    'step creation temporarily disabled',
    'Requested nodes are busy',
    'Requested node configuration is not available',
    'error: Unable to create step',
    'launch failed',
    'PMI',
    'pals',
)

# the vendor variables a payload records, in the order they are believed
GPU_VARS = (
    'SLURM_STEP_GPUS',
    'ZE_AFFINITY_MASK',
    'ROCR_VISIBLE_DEVICES',
    'HIP_VISIBLE_DEVICES',
    'CUDA_VISIBLE_DEVICES',
)


class SlotRun:
    """One launch: when it ran, and what it could see."""

    def __init__(self, slot, start, end, cores_by_host, gpus, gpu_source):
        self.slot = slot
        self.start = start
        self.end = end
        self.cores_by_host = cores_by_host
        self.gpus = gpus
        self.gpu_source = gpu_source

    @property
    def cores(self):
        """Every core this launch could see, across all its hosts."""
        found = set()
        for cores in self.cores_by_host.values():
            found |= cores
        return found


def main():
    """Summarize the results directory named on the command line."""
    if len(sys.argv) != 2:
        print(f'usage: {sys.argv[0]} <results-dir>', file=sys.stderr)
        return 1
    root = sys.argv[1]

    meta = parse_kv(os.path.join(root, 'meta.kv'))
    support = meta.get('placement_support', 'unknown')
    print_meta(meta)

    if meta.get('dry_run') == 'true':
        print()
        print('This was a dry run: the commands were rendered but nothing')
        print('was launched, so there is nothing to read back.  The commands')
        print(f'are in {os.path.join(root, "commands.txt")}.')
        return 0

    checks = sorted(
        name
        for name in os.listdir(root)
        if os.path.isdir(os.path.join(root, name)) and name != 'scripts'
    )
    if len(checks) == 0:
        print('no check directories found')
        return 1

    for name in checks:
        path = os.path.join(root, name)
        if name.endswith('memory_limit'):
            report_memory_check(path, name)
        else:
            report_check(path, name, support)

    print()
    print('Reading the results:')
    print('  B/C honored + D/E peak == slots and disjoint')
    print('      -> mache renders placements that work here')
    print('  B/C not honored')
    print('      -> the command renders but the machine ignores it')
    print('  D/E peak == 1')
    print('      -> the launches serialized; the command reserves more than')
    print('         it asked for')
    print('  D/E overlapped but collide')
    print('      -> the command oversubscribes rather than places')
    return 0


def parse_kv(path):
    """Parse a file of ``key=value`` lines into a dict."""
    values: dict[str, str] = {}
    if not os.path.exists(path):
        return values
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if '=' in line:
                key, _, value = line.partition('=')
                values[key] = value
    return values


def parse_expected(path):
    """Parse ``expected.kv``, one space-separated record per slot."""
    expected: dict[str, dict[str, str]] = {}
    if not os.path.exists(path):
        return expected
    with open(path) as handle:
        for line in handle:
            fields = dict(
                part.split('=', 1) for part in line.split() if '=' in part
            )
            slot = fields.get('slot')
            if slot is not None:
                expected[slot] = fields
    return expected


def parse_core_list(text):
    """Parse a ``0-3,8`` core list into a set of core numbers."""
    cores: set[int] = set()
    if text is None or text == '' or text == 'unknown':
        return cores
    for chunk in text.split(','):
        chunk = chunk.strip()
        if chunk == '':
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
    """Parse a payload's ``gpu_env`` field into ``{variable: value}``."""
    values: dict[str, str] = {}
    if text is None:
        return values
    for chunk in text.split(';'):
        if '=' not in chunk:
            continue
        key, _, value = chunk.partition('=')
        key = key.strip()
        if key != '':
            values[key] = value
    return values


def gpus_seen(rank):
    """
    Get the GPUs a payload could see, and where that was read from.

    ``CUDA_VISIBLE_DEVICES`` is renumbered relative to each launch's own
    allocation, so launches on different GPUs all report ``0``.  Slurm's
    global ids are preferred wherever they exist.  A variable that is present
    but empty is an explicit "no devices" and is reported as such, since
    whether that is what actually reaches the payload is one of the things
    this check exists to find out.
    """
    candidates = dict(parse_gpu_env(rank.get('gpu_env')))
    step_gpus = rank.get('step_gpus')
    if step_gpus is not None and step_gpus != '':
        candidates['SLURM_STEP_GPUS'] = step_gpus

    for variable in GPU_VARS:
        if variable not in candidates:
            continue
        value = candidates[variable]
        if value == '':
            return set(), f'{variable} set but empty'
        devices = {item.strip() for item in value.split(',') if item.strip()}
        return devices, variable
    return set(), 'no GPU variable set'


def collect_runs(test_dir):
    """Group the payload output in a check directory into one run per slot."""
    slots: dict[str, list[dict[str, str]]] = {}
    for entry in sorted(os.listdir(test_dir)):
        if not entry.endswith('.kv') or entry in ('expected.kv',):
            continue
        values = parse_kv(os.path.join(test_dir, entry))
        slot = values.get('slot')
        if slot is not None:
            slots.setdefault(slot, []).append(values)

    runs = []
    for slot, ranks in sorted(slots.items(), key=lambda item: int(item[0])):
        starts = [float(r['t_start']) for r in ranks if 't_start' in r]
        ends = [float(r['t_end']) for r in ranks if 't_end' in r]
        if len(starts) == 0 or len(ends) == 0:
            continue
        cores_by_host: dict[str, set[int]] = {}
        gpus: set[str] = set()
        sources: set[str] = set()
        for rank in ranks:
            host = rank.get('host', '?')
            cores_by_host.setdefault(host, set()).update(
                parse_core_list(rank.get('cpus_allowed'))
            )
            devices, source = gpus_seen(rank)
            gpus |= devices
            sources.add(source)
        runs.append(
            SlotRun(
                slot=slot,
                start=min(starts),
                end=max(ends),
                cores_by_host=cores_by_host,
                gpus=gpus,
                gpu_source=', '.join(sorted(sources)),
            )
        )
    return runs


def peak_concurrency(runs):
    """The largest number of launches running at any one instant."""
    events = []
    for run in runs:
        events.append((run.start, 1))
        events.append((run.end, -1))
    events.sort()
    current = 0
    peak = 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


def overlapping_pairs(runs):
    """
    Pairs of launches that were genuinely running at the same moment.

    Launches that serialized reuse the same cores harmlessly, so comparing
    them would report a collision that hides the far more important fact that
    they never overlapped.
    """
    for index, first in enumerate(runs):
        for second in runs[index + 1 :]:
            if min(first.end, second.end) > max(first.start, second.start):
                yield first, second


def report_memory_check(test_dir, name):
    """
    Say whether a memory allowance was enforced.

    Read from what the payload managed to write rather than from whether it
    finished: a process killed for exceeding a limit dies without warning,
    so the last figure it flushed is the whole of the evidence.
    """
    print()
    print(f'{name}:')

    records = [
        parse_kv(os.path.join(test_dir, entry))
        for entry in sorted(os.listdir(test_dir))
        if entry.endswith('.kv') and entry != 'expected.kv'
    ]
    if len(records) == 0:
        print('  no payload output -- the launch probably failed to start')
        _report_messages(test_dir)
        return

    record = records[0]
    allowance = record.get('allowance_mb', '?')
    target = record.get('target_mb', '?')
    reached = record.get('reached_mb', '0')
    completed = record.get('completed') == 'true'
    returncode = _read_rc(test_dir, record.get('slot', '1'))

    print(
        f'  allowed {allowance} MB, tried to allocate {target} MB, '
        f'reached {reached} MB'
    )

    stopped_short = _as_int(reached) < _as_int(target)

    if completed and returncode == 0:
        print('  NOT ENFORCED: the launch allocated several times its')
        print('    allowance and was left alone.  A memory request does')
        print('    nothing here, so Polaris keeping its own budget is the')
        print('    whole of the answer.')
    elif not completed and stopped_short:
        print(
            f'  ENFORCED: the launch stopped at {reached} MB, short of the '
            f'{target} MB it tried for, having been allowed {allowance} MB'
        )
        print(f'    (exit code {returncode})')
        print('    Two things follow: a step can be capped on this machine,')
        print('    and -- by the same argument that applies to GPUs -- a')
        print('    launch that says nothing about memory may be read as')
        print('    claiming all of it.  Worth chasing.')
    else:
        print(
            f'  UNCLEAR: reached {reached} of {target} MB, '
            f'completed={completed}, exit code {returncode}.'
        )
        print('    Not a clean answer either way; read the .err file before')
        print('    concluding anything.')

    _report_messages(test_dir)


def report_check(test_dir, name, support):
    """Print everything worth saying about one check."""
    print()
    print(f'{name}:')

    render_error = os.path.join(test_dir, 'render_error.txt')
    if os.path.exists(render_error):
        with open(render_error) as handle:
            message = handle.read().strip()
        print(f'  NOT RENDERED: {message}')
        return

    expected = parse_expected(os.path.join(test_dir, 'expected.kv'))
    runs = collect_runs(test_dir)
    if len(runs) == 0:
        print('  no payload output -- the launches probably failed')
        _report_messages(test_dir)
        return

    peak = peak_concurrency(runs)
    print(f'  {len(runs)} launch(es), peak concurrency {peak}')

    for run in runs:
        _report_slot(run, expected.get(run.slot), support)

    if peak > 1:
        _report_collisions(runs)
    elif len(runs) > 1:
        print('  no disjointness verdict -- the launches never overlapped')

    _report_messages(test_dir)


def print_meta(meta):
    """Print the header describing the run."""
    if len(meta) == 0:
        return
    print(
        '  run: {machine} / {scheduler} job {job_id}, {nodes} node(s), '
        '{usable} usable core(s)/node, {gpus_on_node} gpu(s)/node'.format(
            machine=meta.get('machine', '?'),
            scheduler=meta.get('scheduler', '?'),
            job_id=meta.get('job_id', '?'),
            nodes=meta.get('nodes', '?'),
            usable=meta.get('usable_cores', meta.get('cores_on_node', '?')),
            gpus_on_node=meta.get('gpus_on_node', '?'),
        )
    )
    print(
        '       {slots} slot(s) x {ntasks} task(s) x {cpus} core(s), '
        '{gpus} gpu(s) per slot, {payload} payload'.format(
            slots=meta.get('slots', '?'),
            ntasks=meta.get('ntasks', '?'),
            cpus=meta.get('cpus_per_task', '?'),
            gpus=meta.get('gpus_per_slot', '?'),
            payload=meta.get('payload', '?'),
        )
    )
    print(
        '       launcher {launcher}, placement support {support}'.format(
            launcher=meta.get('parallel_executable', '?'),
            support=meta.get('placement_support', '?'),
        )
    )
    print(
        '       mache {version} from {path}'.format(
            version=meta.get('mache_version', '?'),
            path=meta.get('mache_path', '?'),
        )
    )
    status = meta.get('status', 'unknown')
    if status != 'complete':
        print(f'       RUN STATUS: {status} -- this run did not finish')


def _report_slot(run, expected, support):
    """Print what one launch was given and what it could see."""
    hosts = ','.join(sorted(run.cores_by_host))
    granted = run.cores
    line = (
        f'    slot {run.slot}: host {hosts}, {len(granted)} core(s) '
        f'{_format_cores(granted)}'
    )
    if len(run.gpus) > 0:
        line += f', gpu(s) {sorted(run.gpus)} (from {run.gpu_source})'
    else:
        line += f', no gpu(s) ({run.gpu_source})'
    print(line)

    if expected is None or expected.get('placement') == 'none':
        print('      no placement was requested; this is the control')
        return

    wanted = parse_core_list(expected.get('cores'))
    wanted_gpus = int(expected.get('gpus', '0'))
    if support == 'scheduler':
        # Slurm reserves a count and picks which cores satisfy it, so the
        # exact set is its choice and only the count is ours to check.
        if len(granted) == len(wanted):
            print(
                f'      HONORED: {len(wanted)} core(s) asked for, '
                f'{len(granted)} granted (Slurm chose which)'
            )
        else:
            print(
                f'      NOT HONORED: asked for {len(wanted)} core(s), '
                f'can see {len(granted)}'
            )
    else:
        missing = wanted - granted
        extra = granted - wanted
        if len(missing) == 0 and len(extra) == 0:
            print('      HONORED: exactly the cores asked for')
        else:
            print(
                f'      NOT HONORED: asked for {_format_cores(wanted)}, '
                f'can see {_format_cores(granted)}'
            )

    if wanted_gpus == 0:
        if len(run.gpus) == 0:
            print('      GPUs: none asked for, none visible')
        else:
            print(
                f'      GPUs: none asked for, but {sorted(run.gpus)} are '
                f'visible'
            )
    elif len(run.gpus) == wanted_gpus:
        print(
            f'      GPUs: {wanted_gpus} asked for, {sorted(run.gpus)} visible'
        )
    else:
        print(
            f'      GPUs: asked for {wanted_gpus}, can see '
            f'{len(run.gpus)} {sorted(run.gpus)}'
        )


def _report_collisions(runs):
    """Print whether overlapping launches shared cores or GPUs."""
    core_hits = []
    gpu_hits = []
    for first, second in overlapping_pairs(runs):
        for host, cores in first.cores_by_host.items():
            shared = cores & second.cores_by_host.get(host, set())
            if len(shared) > 0:
                core_hits.append(
                    f'slots {first.slot} and {second.slot} overlapped in '
                    f'time and share cores {_format_cores(shared)} on {host}'
                )
        shared_gpus = first.gpus & second.gpus
        if len(shared_gpus) > 0:
            gpu_hits.append(
                f'slots {first.slot} and {second.slot} overlapped in time '
                f'and share GPU(s) {sorted(shared_gpus)}'
            )

    if len(core_hits) > 0:
        print('  CORE COLLISIONS:')
        for hit in core_hits[:6]:
            print(f'    {hit}')
    else:
        print('  cores are disjoint across overlapping launches')

    if any(len(run.gpus) > 0 for run in runs):
        if len(gpu_hits) > 0:
            print('  GPU COLLISIONS:')
            for hit in gpu_hits[:6]:
                print(f'    {hit}')
        else:
            print('  GPUs are disjoint across overlapping launches')


def _as_int(value):
    """Parse an integer that may be missing or malformed, as -1."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _read_rc(test_dir, slot):
    """Read a launch's recorded exit code, or None."""
    path = os.path.join(test_dir, f'slot{slot}.rc')
    try:
        with open(path) as handle:
            return int(handle.read().strip())
    except (OSError, ValueError):
        return None


def _report_messages(test_dir):
    """Surface launcher retry and error messages from the stderr files."""
    hits: dict[str, int] = {}
    for entry in sorted(os.listdir(test_dir)):
        if not entry.endswith('.err'):
            continue
        try:
            with open(os.path.join(test_dir, entry), errors='replace') as f:
                text = f.read()
        except OSError:
            continue
        for line in text.splitlines():
            if any(marker.lower() in line.lower() for marker in RETRY_MARKERS):
                hits[line.strip()] = hits.get(line.strip(), 0) + 1
    if len(hits) > 0:
        print('  launcher messages:')
        for line, count in sorted(hits.items(), key=lambda item: -item[1])[:5]:
            print(f'    [{count}x] {line[:140]}')


def _format_cores(cores):
    """Format a set of cores as a compact ``0-3,8`` list."""
    ranges: list[str] = []
    for core in sorted(cores):
        if len(ranges) > 0:
            first, _, last = ranges[-1].partition('-')
            end = int(last) if last != '' else int(first)
            if core == end + 1:
                ranges[-1] = f'{first}-{core}'
                continue
        ranges.append(f'{core}')
    return ','.join(ranges) if len(ranges) > 0 else '(none)'


if __name__ == '__main__':
    sys.exit(main())
