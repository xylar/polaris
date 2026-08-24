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
        mem_allowance_mb=1024,
        mem_target_mb=4096,
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


class MemorySystem(FakeSystem):
    """A fake system that reports which batch system it is."""

    def __init__(self, system):
        super().__init__(cores_per_node=64, config={'system': system})


def test_the_memory_check_asks_for_an_allowance_mache_will_not_render():
    """
    mache deliberately carries no memory, so this one flag is added here.

    The question is what the batch system does, not what mache emits, and
    that is the only reason a check may reach past the renderer.
    """
    check = check_placement.build_memory_check(
        node='node0001',
        cores=list(range(64)),
        cpus_per_task=4,
        parallel_system=MemorySystem('slurm'),
        payload='mem_payload.sh',
        allowance_mb=1024,
        target_mb=4096,
    )
    assert check is not None
    assert list(check.extra_args) == ['--mem=1024M']
    assert check.ntasks == 1
    assert check.placements[0].gpus == 0
    assert len(check.placements[0].cores) == 4


def test_being_killed_is_a_result_not_a_failed_run():
    """A launch stopped for exceeding its allowance is the finding."""
    check = check_placement.build_memory_check(
        node='node0001',
        cores=list(range(64)),
        cpus_per_task=4,
        parallel_system=MemorySystem('slurm'),
        payload='mem_payload.sh',
        allowance_mb=1024,
        target_mb=4096,
    )
    assert check.failure_is_a_result


def test_no_memory_check_where_a_launch_cannot_ask_for_memory():
    """PALS takes no per-launch memory request, so there is nothing to ask."""
    for system in ('pbs', 'single_node'):
        check = check_placement.build_memory_check(
            node='node0001',
            cores=list(range(64)),
            cpus_per_task=4,
            parallel_system=MemorySystem(system),
            payload='mem_payload.sh',
            allowance_mb=1024,
            target_mb=4096,
        )
        assert check is None, system


def test_a_completed_allocation_reads_as_not_enforced(tmp_path, capsys):
    _write_memory_run(tmp_path, reached=4096, completed=True, returncode=0)
    summarize.report_memory_check(
        str(tmp_path / 'F_memory_limit'), 'F_memory_limit'
    )
    out = capsys.readouterr().out
    assert 'NOT ENFORCED' in out


def test_stopping_short_reads_as_enforced(tmp_path, capsys):
    _write_memory_run(tmp_path, reached=1024, completed=False, returncode=137)
    summarize.report_memory_check(
        str(tmp_path / 'F_memory_limit'), 'F_memory_limit'
    )
    out = capsys.readouterr().out
    assert 'ENFORCED' in out
    assert 'NOT ENFORCED' not in out


def test_reaching_the_target_without_finishing_is_not_called_enforced(
    tmp_path, capsys
):
    """Killed after allocating everything it asked for proves nothing."""
    _write_memory_run(tmp_path, reached=4096, completed=False, returncode=1)
    summarize.report_memory_check(
        str(tmp_path / 'F_memory_limit'), 'F_memory_limit'
    )
    out = capsys.readouterr().out
    assert 'UNCLEAR' in out
    assert 'ENFORCED' not in out.replace('NOT ENFORCED', '')


def _write_memory_run(tmp_path, reached, completed, returncode):
    """Write a memory check directory as a real run would leave it."""
    test_dir = tmp_path / 'F_memory_limit'
    test_dir.mkdir()
    lines = [
        'test=F_memory_limit',
        'slot=1',
        'rank=0',
        'host=node0001',
        'payload=memory',
        'allowance_mb=1024',
        'target_mb=4096',
        't_start=100.0',
        f'reached_mb={reached}',
    ]
    if completed:
        lines.append('completed=true')
    (test_dir / 'slot1_rank0.kv').write_text('\n'.join(lines) + '\n')
    (test_dir / 'slot1.rc').write_text(f'{returncode}\n')
    (test_dir / 'slot1.err').write_text('')


def test_the_memory_payload_is_told_the_same_allowance_the_flag_asked_for(
    tmp_path,
):
    """
    A regression: the driver rendered --mem=1024M and the payload recorded
    an allowance of 0, because the two numbers travelled by different routes
    and only one of them moved.  Evidence that disagrees with the command is
    worse than no evidence.
    """
    payload = os.path.join(UTILS_DIR, 'mem_payload.sh')
    outdir = str(tmp_path / 'results')
    os.makedirs(outdir)
    args = argparse.Namespace(
        outdir=outdir,
        payload=payload,
        slots=1,
        ntasks=1,
        cpus_per_task=1,
        sleep=0,
        dry_run=False,
        mem_allowance_mb=1024,
        mem_target_mb=8,
    )
    check = check_placement.Check(
        name='F_memory_limit',
        description='allowance reaches the payload',
        placements=[None],
        payload=payload,
        ntasks=1,
        failure_is_a_result=True,
    )
    check_placement.run_check(_BashSystem(), check, args)

    record = summarize.parse_kv(
        os.path.join(outdir, 'F_memory_limit', 'slot1_rank0.kv')
    )
    assert record['allowance_mb'] == '1024'
    assert record['target_mb'] == '8'
    assert record.get('completed') == 'true'


class _BashSystem:
    """A launcher that just runs the payload, with no placement at all."""

    def get_parallel_command(
        self, args, ntasks, cpus_per_task=0, gpus_per_task=0, placement=None
    ):
        return ['/bin/bash']


def test_no_gpu_verdict_when_launches_got_no_gpus(tmp_path, capsys):
    """
    A real pm-gpu run had three of four launches receive no GPU, and the
    summary called them disjoint.  Empty sets are disjoint for the wrong
    reason, the same mistake as calling cores disjoint when nothing
    overlapped.
    """
    test_dir = tmp_path / 'E_concurrent_gpu'
    test_dir.mkdir()
    expected = []
    for slot in (1, 2, 3, 4):
        expected.append(f'slot={slot} nodes=n1 cores=0,1 gpus=1 gpu_ids=')
        gpus = '0' if slot == 1 else ''
        (test_dir / f'slot{slot}_rank0.kv').write_text(
            f'test=E_concurrent_gpu\nslot={slot}\nrank=0\nhost=n1\n'
            f'cpus_allowed={2 * slot}-{2 * slot + 1}\n'
            f'gpu_env=\nstep_gpus={gpus}\n'
            f't_start=100.0\nt_end=115.0\n'
        )
        (test_dir / f'slot{slot}.err').write_text('')
    (test_dir / 'expected.kv').write_text('\n'.join(expected) + '\n')

    summarize.report_check(str(test_dir), 'E_concurrent_gpu', 'scheduler')
    out = capsys.readouterr().out
    assert 'GPUS NOT GRANTED: slot(s) 2, 3, 4' in out
    assert 'no GPU disjointness verdict' in out
    assert 'GPUs are disjoint' not in out


def test_the_ceiling_check_says_nothing_about_memory_to_the_launcher():
    """
    The whole point: the allowance check always passes --mem, so the case
    where Polaris says nothing is the one still untested.
    """
    check = check_placement.build_placement_memory_check(
        node='node0001',
        cores=list(range(64)),
        parallel_system=MemorySystem('slurm'),
        payload='mem_payload.sh',
        memory_mb=253000,
        cores_per_node=64,
    )
    assert check is not None
    assert list(check.extra_args) == []
    # one placed launch and one unplaced control
    assert check.placements[0] is not None
    assert check.placements[1] is None
    # twice a single core's share, so a step held to its share dies
    assert check.extra_env['PLACE_MEM_TARGET_MB'] == f'{2 * (253000 // 64)}'
    assert check.extra_env['PLACE_MEM_ALLOWANCE_MB'] == '0'
    assert check.failure_is_a_result


def test_no_ceiling_check_without_a_memory_figure():
    """The target is derived from the node's memory; a guess is worse."""
    assert (
        check_placement.build_placement_memory_check(
            node='n1',
            cores=[0],
            parallel_system=MemorySystem('slurm'),
            payload='p',
            memory_mb=None,
            cores_per_node=64,
        )
        is None
    )


def test_a_ceiling_is_only_reported_against_a_surviving_control(
    tmp_path, capsys
):
    _write_ceiling_run(tmp_path, placed_ok=False, unplaced_ok=True)
    summarize.report_placement_memory_check(
        str(tmp_path / 'G_placement_memory'), 'G_placement_memory'
    )
    assert 'PLACEMENT IMPOSES A CEILING' in capsys.readouterr().out


def test_both_stopped_is_blamed_on_the_job_not_placement(tmp_path, capsys):
    _write_ceiling_run(tmp_path, placed_ok=False, unplaced_ok=False)
    summarize.report_placement_memory_check(
        str(tmp_path / 'G_placement_memory'), 'G_placement_memory'
    )
    out = capsys.readouterr().out
    assert 'NOT PLACEMENT' in out
    assert 'PLACEMENT IMPOSES A CEILING' not in out


def test_both_surviving_bounds_the_answer_rather_than_settling_it(
    tmp_path, capsys
):
    _write_ceiling_run(tmp_path, placed_ok=True, unplaced_ok=True)
    out_dir = str(tmp_path / 'G_placement_memory')
    summarize.report_placement_memory_check(out_dir, 'G_placement_memory')
    out = capsys.readouterr().out
    assert 'NO CEILING FROM PLACEMENT' in out
    assert 'not that none' in out


def _write_ceiling_run(tmp_path, placed_ok, unplaced_ok):
    """Write a G_placement_memory directory as a real run would leave it."""
    test_dir = tmp_path / 'G_placement_memory'
    test_dir.mkdir()
    (test_dir / 'expected.kv').write_text(
        'slot=1 nodes=n1 cores=0 gpus=0 gpu_ids=\nslot=2 placement=none\n'
    )
    for slot, ok in ((1, placed_ok), (2, unplaced_ok)):
        lines = [
            f'test=G_placement_memory\nslot={slot}\nrank=0\nhost=n1\n'
            'payload=memory\nallowance_mb=0\ntarget_mb=7906\n'
            't_start=100.0\n'
        ]
        lines.append('reached_mb=7906\n' if ok else 'reached_mb=1024\n')
        if ok:
            lines.append('completed=true\n')
        (test_dir / f'slot{slot}_rank0.kv').write_text(''.join(lines))
        (test_dir / f'slot{slot}.rc').write_text('0\n' if ok else '1\n')
        (test_dir / f'slot{slot}.err').write_text('')


@pytest.mark.parametrize('machine', ['chrysalis', 'pm-cpu', 'frontier'])
def test_every_check_renders_through_the_driver(machine, tmp_path):
    """
    Render each check the way a run does, not by calling mache by hand.

    G_placement_memory failed on two machines because it built a one-core
    placement while the driver rendered it with the run's four cores per
    task, and mache rightly refused.  Rendering it by hand with the right
    number had passed.  This drives `run_check`, which is where the two
    numbers have to agree.
    """
    import os as _os
    from unittest import mock

    from mache.parallel.slurm import SlurmSystem

    config = check_placement.build_config(machine)
    outdir = str(tmp_path / 'results')
    _os.makedirs(outdir)
    args = argparse.Namespace(
        outdir=outdir,
        payload='PAYLOAD',
        mem_payload='MEM',
        slots=4,
        ntasks=2,
        cpus_per_task=4,
        sleep=15,
        dry_run=True,
        mem_allowance_mb=1024,
        mem_target_mb=4096,
    )

    with (
        mock.patch.dict(_os.environ, {'SLURM_JOB_ID': '1'}),
        mock.patch('mache.parallel.slurm._get_subprocess_int', lambda a: 1),
        mock.patch('mache.parallel.slurm.get_slurm_version', lambda: (25, 11)),
    ):
        system = SlurmSystem(config)
        cores = check_placement.get_usable_cores(system)
        checks = check_placement.build_checks(
            node='n1',
            cores=cores,
            slots=args.slots,
            ntasks=args.ntasks,
            cpus_per_task=args.cpus_per_task,
            gpus_per_slot=(system.gpus_per_node or 0) // args.slots,
            gpu_ids_needed=check_placement.needs_explicit_gpu_ids(system),
        )
        memory_mb = system.get_config_int('memory_per_node')
        for builder in (
            check_placement.build_memory_check(
                node='n1',
                cores=cores,
                cpus_per_task=args.cpus_per_task,
                parallel_system=system,
                payload='MEM',
                allowance_mb=args.mem_allowance_mb,
                target_mb=args.mem_target_mb,
            ),
            check_placement.build_placement_memory_check(
                node='n1',
                cores=cores,
                parallel_system=system,
                payload='MEM',
                memory_mb=memory_mb,
                cores_per_node=system.cores_per_node,
            ),
        ):
            if builder is not None:
                checks.append(builder)

        for check in checks:
            check_placement.run_check(system, check, args)

    for check in checks:
        failed = _os.path.join(outdir, check.name, 'render_error.txt')
        assert not _os.path.exists(failed), (
            f'{machine} {check.name}: {open(failed).read().strip()}'
        )
