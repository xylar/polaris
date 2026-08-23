"""
Tests for the placement-check harness in ``utils/placement_check``.

The harness only ever runs inside an allocation, on five machines, driven by
someone else.  A bug in it is therefore expensive: it costs a submission on
each machine to find and another to confirm.  These tests cover the parts
that can be checked without an allocation -- how placements are laid out, and
how the results are read -- so that what does need a machine is only the
thing a machine can tell us.
"""

import argparse
import importlib.util
import os
import shutil
import sys

import pytest

UTILS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'utils',
    'placement_check',
)


def _load(name):
    """Load a module from the placement-check directory by path."""
    if name in sys.modules:
        return sys.modules[name]
    path = os.path.join(UTILS_DIR, f'{name}.py')
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check_placement = _load('check_placement')
summarize = _load('summarize')


class FakeSystem:
    """The little of a mache ParallelSystem that laying out a check uses."""

    def __init__(self, cores_per_node=None, config=None):
        self.cores_per_node = cores_per_node
        self._config = config if config is not None else {}

    def get_config(self, key, default=None):
        return self._config.get(key, default)


def test_parse_core_list_handles_ranges_and_singletons():
    assert check_placement.parse_core_list('0-3') == [0, 1, 2, 3]
    assert check_placement.parse_core_list('5') == [5]
    assert check_placement.parse_core_list('0-1,4,6-7') == [0, 1, 4, 6, 7]
    assert check_placement.parse_core_list('') == []


def test_usable_cores_come_from_a_cpu_bind_list():
    """Aurora reserves core 0 and cores 49-52, and says so in cpu_bind."""
    aurora_bind = (
        'list:1-8:9-16:17-24:25-32:33-40:41-48:53-60:61-68:69-76:77-84:'
        '85-92:93-100'
    )
    system = FakeSystem(cores_per_node=96, config={'cpu_bind': aurora_bind})
    cores = check_placement.get_usable_cores(system)
    assert len(cores) == 96
    assert 0 not in cores
    assert 49 not in cores
    assert 52 not in cores
    assert cores[0] == 1
    assert cores[-1] == 100


def test_usable_cores_fall_back_to_the_core_count():
    system = FakeSystem(cores_per_node=8, config={'cpu_bind': 'cores'})
    assert check_placement.get_usable_cores(system) == list(range(8))


def test_usable_cores_refuse_to_guess():
    """A machine reporting no cores must say so, not silently use zero."""
    system = FakeSystem(cores_per_node=0, config={})
    with pytest.raises(SystemExit):
        check_placement.get_usable_cores(system)


def test_an_override_wins_over_the_machine_config():
    system = FakeSystem(cores_per_node=64, config={})
    assert check_placement.get_usable_cores(system, '2-4') == [2, 3, 4]


def test_gpu_ids_are_named_only_where_nothing_assigns_them():
    slurm = FakeSystem(config={})
    pals = FakeSystem(config={'gpu_visible_devices_var': 'ZE_AFFINITY_MASK'})
    empty = FakeSystem(config={'gpu_visible_devices_var': ''})
    assert not check_placement.needs_explicit_gpu_ids(slurm)
    assert check_placement.needs_explicit_gpu_ids(pals)
    assert not check_placement.needs_explicit_gpu_ids(empty)


def test_planned_placements_do_not_overlap():
    placements = check_placement.plan_placements(
        node='node0001',
        cores=list(range(64)),
        slots=4,
        ntasks=2,
        cpus_per_task=4,
        gpus_per_slot=0,
        gpu_ids_needed=False,
    )
    assert len(placements) == 4
    seen: set[int] = set()
    for placement in placements:
        assert placement.nodes == ('node0001',)
        assert len(placement.cores) == 8
        assert seen.isdisjoint(placement.cores)
        seen.update(placement.cores)
    assert len(seen) == 32


def test_planned_placements_start_from_the_usable_cores():
    """A placement must never name a core the machine reserved."""
    cores = [1, 2, 3, 4, 53, 54, 55, 56]
    placements = check_placement.plan_placements(
        node='node0001',
        cores=cores,
        slots=2,
        ntasks=1,
        cpus_per_task=4,
        gpus_per_slot=0,
        gpu_ids_needed=False,
    )
    assert placements[0].cores == (1, 2, 3, 4)
    assert placements[1].cores == (53, 54, 55, 56)


def test_gpu_ids_are_disjoint_when_they_are_named():
    placements = check_placement.plan_placements(
        node='node0001',
        cores=list(range(64)),
        slots=4,
        ntasks=2,
        cpus_per_task=4,
        gpus_per_slot=3,
        gpu_ids_needed=True,
    )
    seen: set[int] = set()
    for placement in placements:
        assert placement.gpus == 3
        assert placement.gpu_ids is not None
        assert seen.isdisjoint(placement.gpu_ids)
        seen.update(placement.gpu_ids)
    assert sorted(seen) == list(range(12))


def test_gpu_ids_are_left_to_the_scheduler_otherwise():
    placements = check_placement.plan_placements(
        node='node0001',
        cores=list(range(64)),
        slots=4,
        ntasks=2,
        cpus_per_task=4,
        gpus_per_slot=1,
        gpu_ids_needed=False,
    )
    for placement in placements:
        assert placement.gpus == 1
        assert placement.gpu_ids is None


def test_too_few_cores_is_an_error_not_an_overlap():
    with pytest.raises(SystemExit):
        check_placement.plan_placements(
            node='node0001',
            cores=list(range(8)),
            slots=4,
            ntasks=2,
            cpus_per_task=4,
            gpus_per_slot=0,
            gpu_ids_needed=False,
        )


def test_checks_skip_the_gpu_cases_without_gpus():
    checks = check_placement.build_checks(
        node='node0001',
        cores=list(range(64)),
        slots=4,
        ntasks=2,
        cpus_per_task=4,
        gpus_per_slot=0,
        gpu_ids_needed=False,
    )
    names = [check.name for check in checks]
    assert names == ['A_unplaced', 'B_placed_alone', 'D_concurrent']
    assert checks[0].placements == [None]


def test_checks_include_the_gpu_cases_with_gpus():
    checks = check_placement.build_checks(
        node='node0001',
        cores=list(range(64)),
        slots=4,
        ntasks=2,
        cpus_per_task=4,
        gpus_per_slot=2,
        gpu_ids_needed=False,
    )
    names = [check.name for check in checks]
    assert names == [
        'A_unplaced',
        'B_placed_alone',
        'C_placed_alone_gpu',
        'D_concurrent',
        'E_concurrent_gpu',
    ]
    # the CPU-only checks must still ask for no GPUs, since silence is read
    # as a claim on all of them
    assert all(p.gpus == 0 for p in checks[3].placements)
    assert all(p.gpus == 2 for p in checks[4].placements)


def test_gpus_seen_prefers_the_global_slurm_ids():
    """CUDA renumbers per launch, so every launch would report device 0."""
    rank = {'gpu_env': 'CUDA_VISIBLE_DEVICES=0;', 'step_gpus': '2,3'}
    devices, source = summarize.gpus_seen(rank)
    assert devices == {'2', '3'}
    assert source == 'SLURM_STEP_GPUS'


def test_gpus_seen_reports_an_explicitly_empty_variable():
    rank = {'gpu_env': 'ZE_AFFINITY_MASK=;', 'step_gpus': ''}
    devices, source = summarize.gpus_seen(rank)
    assert devices == set()
    assert source == 'ZE_AFFINITY_MASK set but empty'


def test_gpus_seen_distinguishes_unset_from_empty():
    devices, source = summarize.gpus_seen({'gpu_env': '', 'step_gpus': ''})
    assert devices == set()
    assert source == 'no GPU variable set'


def test_peak_concurrency_counts_simultaneous_launches():
    runs = [
        summarize.SlotRun('1', 0.0, 10.0, {}, set(), ''),
        summarize.SlotRun('2', 1.0, 11.0, {}, set(), ''),
        summarize.SlotRun('3', 20.0, 30.0, {}, set(), ''),
    ]
    assert summarize.peak_concurrency(runs) == 2


def test_serialized_launches_are_not_compared():
    """Launches that never overlapped reuse cores harmlessly."""
    runs = [
        summarize.SlotRun('1', 0.0, 10.0, {'n': {0, 1}}, set(), ''),
        summarize.SlotRun('2', 11.0, 20.0, {'n': {0, 1}}, set(), ''),
    ]
    assert list(summarize.overlapping_pairs(runs)) == []


def test_overlapping_launches_are_compared():
    runs = [
        summarize.SlotRun('1', 0.0, 10.0, {'n': {0, 1}}, set(), ''),
        summarize.SlotRun('2', 5.0, 15.0, {'n': {0, 1}}, set(), ''),
    ]
    assert len(list(summarize.overlapping_pairs(runs))) == 1


def test_core_ranges_are_formatted_compactly():
    assert summarize._format_cores({0, 1, 2, 3, 8}) == '0-3,8'
    assert summarize._format_cores(set()) == '(none)'


def test_summary_reports_an_honored_placement(tmp_path, capsys):
    _write_run(tmp_path, support='cpu_binding', granted='0-7')
    summarize.report_check(
        str(tmp_path / 'B_placed_alone'), 'B_placed_alone', 'cpu_binding'
    )
    out = capsys.readouterr().out
    assert 'HONORED: exactly the cores asked for' in out


def test_summary_reports_a_placement_that_was_ignored(tmp_path, capsys):
    _write_run(tmp_path, support='cpu_binding', granted='0-63')
    summarize.report_check(
        str(tmp_path / 'B_placed_alone'), 'B_placed_alone', 'cpu_binding'
    )
    out = capsys.readouterr().out
    assert 'NOT HONORED' in out


def test_a_scheduler_placement_is_judged_on_the_count(tmp_path, capsys):
    """Slurm picks which cores satisfy a request, so only the count is ours."""
    _write_run(tmp_path, support='scheduler', granted='16-23')
    summarize.report_check(
        str(tmp_path / 'B_placed_alone'), 'B_placed_alone', 'scheduler'
    )
    out = capsys.readouterr().out
    assert 'HONORED' in out
    assert 'NOT HONORED' not in out


def _write_run(tmp_path, support, granted):
    """Write a one-slot check directory as a real run would leave it."""
    test_dir = tmp_path / 'B_placed_alone'
    test_dir.mkdir()
    (test_dir / 'expected.kv').write_text(
        'slot=1 nodes=node0001 cores=0,1,2,3,4,5,6,7 gpus=0 gpu_ids=\n'
    )
    (test_dir / 'slot1_rank0.kv').write_text(
        'test=B_placed_alone\n'
        'slot=1\n'
        'rank=0\n'
        'host=node0001\n'
        f'cpus_allowed={granted}\n'
        'gpu_env=\n'
        'step_gpus=\n'
        't_start=100.0\n'
        't_end=115.0\n'
    )


class TasksetSystem:
    """
    A stand-in launcher that places with ``taskset`` and nothing else.

    Enough of a mache ParallelSystem to drive the whole harness in-process,
    which is the only way to exercise starting launches together and reading
    the result without an allocation on one of five machines.
    """

    def get_parallel_command(
        self, args, ntasks, cpus_per_task=0, gpus_per_task=0, placement=None
    ):
        if placement is None:
            return ['/bin/bash'] + list(args)
        cores = ','.join(f'{core}' for core in placement.cores)
        return ['taskset', '-c', cores, '/bin/bash'] + list(args)


def test_the_whole_harness_runs_and_reads_back(tmp_path, capsys):
    """Start four placed launches together and read the result back."""
    if shutil.which('taskset') is None:
        pytest.skip('taskset is not available here')

    payload = os.path.join(UTILS_DIR, 'payload.sh')
    outdir = str(tmp_path / 'results')
    os.makedirs(outdir)
    args = argparse.Namespace(
        outdir=outdir,
        payload=payload,
        slots=4,
        ntasks=1,
        cpus_per_task=2,
        sleep=1,
        dry_run=False,
    )
    checks = check_placement.build_checks(
        node='localhost',
        cores=list(range(8)),
        slots=4,
        ntasks=1,
        cpus_per_task=2,
        gpus_per_slot=0,
        gpu_ids_needed=False,
    )
    concurrent = [check for check in checks if check.name == 'D_concurrent'][0]

    failures = check_placement.run_check(TasksetSystem(), concurrent, args)
    assert failures == 0

    summarize.report_check(
        os.path.join(outdir, 'D_concurrent'), 'D_concurrent', 'cpu_binding'
    )
    out = capsys.readouterr().out
    assert '4 launch(es), peak concurrency 4' in out
    assert 'HONORED: exactly the cores asked for' in out
    assert 'cores are disjoint across overlapping launches' in out
    assert 'COLLISIONS' not in out
