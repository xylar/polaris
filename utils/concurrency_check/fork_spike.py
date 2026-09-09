#!/usr/bin/env python3
"""
What does it cost to start a step by forking rather than by exec?

Phase B starts each step as a fresh ``polaris serial`` subprocess, and that
subprocess spends about 35 s importing Python before it does any work: 11.5 s
for ``import polaris`` and 17.3 s for the first unpickle of ``step.pickle``,
which is itself import time rather than data.  On ``omega_pr`` that consumed
the whole speedup.

The proposal is to fork instead.  A forked child has its own address space,
so a step may still ``chdir``, set library defaults and use ``pyplot``
globals exactly as it does today, while inheriting every module the parent
has already imported.  The scheduler holds live ``Step`` objects, so a
forked child need not read ``step.pickle`` at all -- which is where the
larger half of the cost goes.

That is an argument, not a measurement, and this script is what turns it
into one.  It answers four questions:

1. **What does a forked step cost?**  The same step is run both ways, in
   the same job, and the difference is the startup that fork removes.
2. **Does a forked child run a step correctly?**  Its log, its outputs and
   its completion marker have to be indistinguishable from the exec path's,
   and an MPI step has to reach ``srun`` and come back.
3. **Is the parent safe to fork from?**  Only the forking thread survives a
   fork, so a lock another thread held stays locked forever in the child.
   The parent's threads are counted before anything is forked.
4. **What does a forked child cost in memory?**  Fork is copy-on-write, but
   refcounting dirties pages, and Phase B ran 48 steps at once.  Proportional
   set size across parent and children is what says how much is really
   shared.

Nothing here is part of Polaris.  It is a harness built to answer a
question, and per the rule in the umbrella design document it does not
merge; its findings belong in the Phase B design document.

Usage
-----
Inside an allocation, from a work directory that has been set up::

    ./fork_spike.py -w <work_dir> -s <suite> --steps <path> [<path> ...]

Give it at least one 1-core Python step and at least one MPI step; the MPI
step is the one that answers question 2, and it is the one most likely to
surprise us.
"""

import argparse
import os
import signal
import sys
import threading
import time
from typing import Dict, List, Optional, Tuple

# polaris is deliberately NOT imported here.  Importing it at module level
# would mean the parent had already paid the import cost before the timing
# below started, and the figure this script exists to measure would read as
# zero.  Every polaris import in this file is inside the function that needs
# it, for that reason.
STEP_COMPLETE_LOG = 'polaris_step_complete.log'


def main():
    parser = argparse.ArgumentParser(
        description='Compare forking a step against exec-ing one',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '-w', '--work_dir', required=True, help='the suite work directory'
    )
    parser.add_argument(
        '-s', '--suite', required=True, help='the suite name, e.g. omega_pr'
    )
    parser.add_argument(
        '--steps',
        required=True,
        nargs='+',
        help='step paths to run, as they appear in the suite',
    )
    parser.add_argument(
        '--repeat',
        type=int,
        default=1,
        help='trials of each mechanism per step (default 1)',
    )
    parser.add_argument(
        '--fanout',
        type=int,
        default=0,
        help='children to fork at once for the memory reading, 0 to skip',
    )
    parser.add_argument(
        '--probe',
        action='store_true',
        help='report the parent and stop, without running any step',
    )
    args = parser.parse_args()

    os.chdir(args.work_dir)

    print('=' * 72)
    print('fork spike')
    print('=' * 72)
    print()

    steps, timings = _load(args.suite, args.steps)
    _report_parent(timings)

    if args.fanout:
        _report_fanout(args.fanout)

    if args.probe:
        # everything above is import and accounting, which is what `polaris
        # setup` already does on a login node.  Running a step is not, so
        # --probe stops here.
        print('probe only: no step was run')
        return

    results = _trials(steps, args.steps, args.repeat)
    _report_trials(results)


def _load(suite_name, wanted) -> Tuple[Dict, Dict]:
    """
    Put the parent in the state the scheduler is in: every module imported
    and every ``Step`` object live in memory.

    The imports are timed on the way, because the parent pays once what the
    exec path pays per step, and that asymmetry is the whole finding.
    """
    timings = {}

    started = time.time()
    modules_before = len(sys.modules)
    from polaris.run import unpickle_suite  # noqa: PLC0415

    timings['import polaris.run'] = time.time() - started

    started = time.time()
    suite = unpickle_suite(suite_name)
    timings['unpickle suite'] = time.time() - started

    started = time.time()
    from polaris.parallel import set_parallel_systems  # noqa: PLC0415
    from polaris.run import setup_config  # noqa: PLC0415

    steps: Dict[str, object] = {}
    for task in suite['tasks'].values():
        config = setup_config(task.base_work_dir, task.config.filepath)
        task.config = config
        for step in task.steps.values():
            if step.path not in steps:
                step.config = setup_config(
                    step.base_work_dir, step.config.filepath
                )
                steps[step.path] = step

    # the scheduler does this once, in the parent, and a forked child
    # inherits it.  The exec path does it per step, and constructing a
    # ParallelSystem asks the batch system for the job's node count -- so
    # this is a second per-step cost fork removes, on top of the imports.
    first = next(iter(suite['tasks'].values()))
    set_parallel_systems(suite['tasks'], first.config)
    timings['config and parallel systems'] = time.time() - started
    timings['modules imported'] = len(sys.modules) - modules_before

    missing = [path for path in wanted if path not in steps]
    if missing:
        raise SystemExit(
            f'these steps are not in suite {suite_name}: {missing}\n'
            f'it has: {sorted(steps)}'
        )
    return steps, timings


def _report_parent(timings):
    """
    What the parent paid, and whether it is safe to fork from.

    The thread count is the one that can stop this proposal outright.  Only
    the calling thread survives a fork; a lock held by any other thread at
    that moment is held forever in the child, and the symptom is a step that
    hangs rather than one that fails.
    """
    print('-- what the parent paid, once --')
    for name in (
        'import polaris.run',
        'unpickle suite',
        'config and parallel systems',
    ):
        print(f'  {name}: {timings[name]:.1f} s')
    print(f'  modules imported: {timings["modules imported"]}')
    print()

    print('-- is the parent safe to fork from? --')
    # threading.enumerate() sees Python threads only, and it is not the
    # number that matters.  numpy brings up an OpenBLAS pool of one thread
    # per core at import, and those are the threads a fork would strand
    # holding a lock.  /proc is what counts them.
    python_threads = threading.enumerate()
    os_threads = _os_threads()
    print(f'  OS threads: {len(os_threads)}')
    print(f'  Python threads: {len(python_threads)}')
    for name, number in sorted(_tally(os_threads).items()):
        print(f'    {number} x {name}')
    if len(os_threads) > 1:
        print('  WARNING: this parent is multi-threaded.  Only the forking')
        print('  thread survives a fork, so a lock held by any of the others')
        print('  is held forever in the child.')
    for var in (
        'OMP_NUM_THREADS',
        'OPENBLAS_NUM_THREADS',
        'MKL_NUM_THREADS',
    ):
        print(f'    {var}={os.environ.get(var, "(unset)")}')
    print(f'  resident set size: {_rss_mib(os.getpid()) or 0.0:.0f} MiB')
    print(f'  proportional set size: {_pss_mib(os.getpid()) or 0.0:.0f} MiB')
    print()


def _report_fanout(count):
    """
    Fork many children at once and read what they really cost.

    Resident set size counts a shared page in full against every process
    holding it, so summing it over children would multiply the parent's
    imports by the fan-out and say fork is unaffordable.  Proportional set
    size divides each page by the number of processes sharing it, so summing
    *that* is the honest total.  Both are printed because the gap between
    them is the point.
    """
    print(f'-- what {count} children cost at once --')
    children = []
    for _ in range(count):
        sys.stdout.flush()
        sys.stderr.flush()
        pid = os.fork()
        if pid == 0:
            # a child that touches nothing, so what it costs is what fork
            # itself costs rather than what a step's work adds
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            time.sleep(30)
            os._exit(0)
        children.append(pid)

    time.sleep(3.0)
    rss: List[float] = [
        value
        for value in (_rss_mib(pid) for pid in children)
        if value is not None
    ]
    pss: List[float] = [
        value
        for value in (_pss_mib(pid) for pid in children)
        if value is not None
    ]
    parent_pss = _pss_mib(os.getpid()) or 0.0

    print(f'  read {len(rss)} of {count} children')
    if rss:
        print(f'  child RSS: {sum(rss) / len(rss):.0f} MiB each, naive')
        print(f'             sum {sum(rss):.0f} MiB')
    if pss:
        print(f'  child PSS: {sum(pss) / len(pss):.0f} MiB each, honest')
        print(f'             sum {sum(pss):.0f} MiB')
        print(f'  parent PSS: {parent_pss:.0f} MiB')
        print(f'  total PSS: {sum(pss) + parent_pss:.0f} MiB')

    for pid in children:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for pid in children:
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
    print()


def _trials(steps, wanted, repeat) -> List[Dict]:
    """
    Run each step both ways, alternating which goes first.

    The first run of a step warms the filesystem cache for the second, and
    on a parallel filesystem importing 2,880 modules is exactly the kind of
    work that warming flatters.  Alternating the order across steps means
    that bias falls on both mechanisms rather than on one, and printing
    which went first lets a reader see whether it mattered.
    """
    results = []
    for index, path in enumerate(wanted):
        step = steps[path]
        # even-numbered steps run fork first, odd ones exec first
        order = ('fork', 'exec') if index % 2 == 0 else ('exec', 'fork')
        for trial in range(repeat):
            for mechanism in order:
                seconds, status = _one_trial(step, mechanism)
                results.append(
                    dict(
                        path=path,
                        mechanism=mechanism,
                        trial=trial,
                        first=order[0],
                        seconds=seconds,
                        status=status,
                        cores=step.cores,
                    )
                )
                print(
                    f'  {path} [{mechanism}] trial {trial}: '
                    f'{seconds:.1f} s, status {status}'
                )
    print()
    return results


def _one_trial(step, mechanism) -> Tuple[float, int]:
    """
    One run of one step by one mechanism, timed from start to reaped.

    The completion marker is removed first.  A step that finds one reports
    "already completed" and does nothing, which would time an empty run and
    read as a spectacular result.
    """
    marker = os.path.join(step.work_dir, STEP_COMPLETE_LOG)
    if os.path.exists(marker):
        os.remove(marker)

    log = os.path.join(step.work_dir, f'spike_{mechanism}.log')
    started = time.time()
    if mechanism == 'fork':
        status = _run_forked(step, log)
    else:
        status = _run_exec(step, log)
    return time.time() - started, status


def _run_forked(step, log_filename) -> int:
    """
    Run a step in a forked child.

    The child inherits every imported module and the live ``Step`` object,
    so it does none of the work the exec path does before it starts: no
    import, no unpickle, no config parse, no parallel system.  What it does
    do is everything that makes the process the step's own -- its working
    directory, its file descriptors, its signal handlers -- because those
    are what process isolation actually consists of.
    """
    # anything buffered in the parent would otherwise be written twice, once
    # by the parent and once by a child that inherited the buffer
    sys.stdout.flush()
    sys.stderr.flush()

    pid = os.fork()
    if pid != 0:
        _, status = os.waitpid(pid, 0)
        return _exit_code(status)

    # ---- child ----
    try:
        # a child that inherited the parent's handlers would run the
        # scheduler's shutdown on a signal meant for the step
        for name in ('SIGINT', 'SIGTERM', 'SIGHUP'):
            signal.signal(getattr(signal, name), signal.SIG_DFL)

        os.chdir(step.work_dir)

        # dup2 rather than reassigning sys.stdout, so that output from the
        # model an MPI step launches lands in the log too.  That is what
        # `run_as_subprocess` exists for on the serial path, and a forked
        # child satisfies it the same way.
        handle = os.open(
            log_filename, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644
        )
        os.dup2(handle, 1)
        os.dup2(handle, 2)
        os.close(handle)

        _run_step_here(step)
        code = 0
    except BaseException:  # noqa: BLE001
        import traceback  # noqa: PLC0415

        traceback.print_exc()
        code = 1
    finally:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:  # noqa: BLE001
            pass
    # _exit rather than exit: the child must not run the parent's atexit
    # handlers or flush buffers the parent still owns
    os._exit(code)


def _run_step_here(step):
    """
    The child half of running a step, which is what Phase B would share
    between a locally forked child and, later, one forked by a resident
    process on another node.

    This mirrors ``polaris.run.serial.run_single_step`` with the two things
    a fork makes unnecessary removed: the unpickle, and rebuilding config
    and parallel systems that the parent already built.
    """
    import mpas_tools.io  # noqa: PLC0415
    from mpas_tools.logging import LoggingContext  # noqa: PLC0415

    from polaris import Task  # noqa: PLC0415
    from polaris.run.lifecycle import run_step  # noqa: PLC0415

    task = Task(component=step.component, name='spike_task')
    task.add_step(step)
    task.new_step_log_file = False
    task.config = step.config
    step.run_as_subprocess = False

    available_resources = step.component.get_available_resources(
        step.placement
    )

    mpas_tools.io.default_format = step.config.get('io', 'format')
    mpas_tools.io.default_engine = step.config.get('io', 'engine')

    logger_name = step.path.replace('/', '_')
    with LoggingContext(name=logger_name) as stdout_logger:
        task.logger = stdout_logger
        task.stdout_logger = stdout_logger
        run_step(
            task=task,
            step=step,
            new_log_file=False,
            available_resources=available_resources,
            step_log_filename=None,
        )


def _run_exec(step, log_filename) -> int:
    """
    Run a step the way Phase B does today, as the control.

    Same step, same job, same allocation, minutes apart -- which is what
    makes the difference between the two attributable to the mechanism
    rather than to the machine.
    """
    import subprocess  # noqa: PLC0415

    with open(log_filename, 'w') as handle:
        process = subprocess.Popen(
            ['polaris', 'serial', '--step_is_subprocess'],
            cwd=step.work_dir,
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
        return process.wait()


def _report_trials(results):
    """Put the two mechanisms side by side, per step."""
    print('-- what each mechanism cost --')
    header = f'{"step":<44} {"cores":>5} {"fork":>8} {"exec":>8} {"saved":>8}'
    print(header)
    print('-' * len(header))

    by_step: Dict[str, Dict[str, List[float]]] = {}
    for row in results:
        by_step.setdefault(row['path'], {}).setdefault(
            row['mechanism'], []
        ).append(row['seconds'])

    saved = []
    for path, mechanisms in by_step.items():
        fork = _mean(mechanisms.get('fork'))
        execed = _mean(mechanisms.get('exec'))
        cores = next(r['cores'] for r in results if r['path'] == path)
        if fork is None or execed is None:
            continue
        saved.append(execed - fork)
        name = path if len(path) <= 44 else '...' + path[-41:]
        print(
            f'{name:<44} {cores:>5} {fork:>8.1f} {execed:>8.1f} '
            f'{execed - fork:>8.1f}'
        )
    print()

    failures = [row for row in results if row['status'] != 0]
    if failures:
        print(f'  {len(failures)} trial(s) did not exit cleanly:')
        for row in failures:
            print(f'    {row["path"]} [{row["mechanism"]}]: {row["status"]}')
    else:
        print('  every trial exited cleanly, both ways')

    if saved:
        print(
            f'  fork saved {sum(saved) / len(saved):.1f} s per step on '
            f'average, over {len(saved)} step(s)'
        )
    print()
    print('Compare the per-step saving against the 36.6 s the scheduler')
    print('measured as overhead per step on job 1283412.')


def _mean(values) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def _exit_code(status) -> int:
    """Turn a ``waitpid`` status into what a return code would have been."""
    if os.WIFSIGNALED(status):
        return -os.WTERMSIG(status)
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    return status


def _os_threads() -> List[str]:
    """
    Every thread the kernel says this process has, by name.

    This is the count CPython's own fork warning uses, and it is the one
    that decides whether a fork is safe.  A pool brought up by a library at
    import time never appears in ``threading.enumerate()``.
    """
    names = []
    try:
        task = f'/proc/{os.getpid()}/task'
        for tid in os.listdir(task):
            try:
                with open(f'{task}/{tid}/comm') as handle:
                    names.append(handle.read().strip())
            except OSError:
                pass
    except OSError:
        pass
    return names


def _tally(names) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    return counts


def _rss_mib(pid) -> Optional[float]:
    return _smaps_field(pid, 'Rss:')


def _pss_mib(pid) -> Optional[float]:
    return _smaps_field(pid, 'Pss:')


def _smaps_field(pid, field) -> Optional[float]:
    """
    Read one figure from a process's ``smaps_rollup``.

    ``Pss:`` is the one worth having when processes share pages: a page held
    by four processes counts a quarter against each, so a sum over them is
    the memory really used rather than four times the shared part.
    """
    try:
        with open(f'/proc/{pid}/smaps_rollup') as handle:
            for line in handle:
                if line.startswith(field):
                    return float(line.split()[1]) / 1024.0
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    return None


if __name__ == '__main__':
    main()
