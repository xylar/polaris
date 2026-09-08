"""
Peak and mean concurrency of a `polaris parallel` run, from its event stream.

    python utils/concurrency_check/concurrency.py <suite>_events.jsonl

A finish record's `seconds` was its duration rather than a timestamp until
`Stop a record from overwriting when it happened`, so the end of a span is
reconstructed from the start plus the duration.  That reads correctly either
way, which is what lets one script answer for runs on both sides of the fix.
"""

import sys

from polaris.run.events import read_events

events = read_events(sys.argv[1])
starts, cores_of, spans = {}, {}, []
for event in events:
    if event['event'] == 'step_started':
        starts[event['step']] = event['seconds']
        cores_of[event['step']] = event['cores']
    elif event['event'] == 'step_finished':
        begin = starts.pop(event['step'], None)
        if begin is None:
            continue
        # 'seconds' on a finish record is the step's duration, not a
        # timestamp, so the end has to be reconstructed
        spans.append((begin, begin + event['seconds'], event['step']))

wall = max(e['seconds'] for e in events if e['event'] == 'run_finished')
moments = sorted(
    [(b, 1) for b, _, _ in spans] + [(e, -1) for _, e, _ in spans]
)
current = peak = 0
for _, delta in moments:
    current += delta
    peak = max(peak, current)

edges = sorted({t for t, _ in moments})
conc_area = core_area = 0.0
for left, right in zip(edges[:-1], edges[1:], strict=True):
    width = right - left
    live = [s for s in spans if s[0] <= left < s[1]]
    conc_area += len(live) * width
    core_area += sum(cores_of[s[2]] for s in live) * width

step_time = sum(e - b for b, e, _ in spans)
print(f'steps             {len(spans)}')
print(f'wall              {wall:.0f}s')
print(f'step time         {step_time:.0f}s  ({step_time / wall:.1f}x wall)')
print(f'peak concurrency  {peak}')
print(f'mean concurrency  {conc_area / wall:.1f}')
print(
    f'mean busy cores   {core_area / wall:.0f} of 192 '
    f'({100 * core_area / wall / 192:.0f}%)'
)
