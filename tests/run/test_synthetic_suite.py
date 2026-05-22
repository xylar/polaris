import os
from pathlib import Path
from types import SimpleNamespace

import polaris.run.scheduler as run_scheduler
import polaris.run.shared as run_shared
from polaris.run.dask import DaskRuntimeInfo
from polaris.run.validation import (
    read_schedule_events,
    validate_phase1_schedule_event_files,
)


class SyntheticStep:
    def __init__(
        self,
        base_work_dir,
        name,
        cached=False,
        completed=False,
        baseline_status=None,
        property_status=None,
        fail=False,
        ntasks=1,
    ):
        self.name = name
        self.path = f'synthetic/{name}'
        self.work_dir = str(base_work_dir / name)
        self.base_work_dir = str(base_work_dir)
        self.cached = cached
        self.dependencies = {}
        self.inputs = []
        self.outputs = []
        self.config = SimpleNamespace(filepath='config.cfg')
        self.run_as_subprocess = False
        self.ntasks = ntasks
        self.min_tasks = 1
        self.cpus_per_task = 1
        self.min_cpus_per_task = 1
        self.gpus_per_task = 0
        self.min_gpus_per_task = 0
        self.max_memory = None
        self.dask_workers = None
        self.min_dask_workers = None
        self.args = None
        self.openmp_threads = 1
        self.is_dependency = False
        self.component = SimpleNamespace(name='synthetic')
        self.subdir = name
        self.logger = None
        self.log_filename = None
        self.baseline_status = baseline_status
        self.property_status = property_status
        self.fail = fail
        Path(self.work_dir).mkdir(parents=True)

        if cached:
            self.add_output(f'{name}.txt')
            Path(self.work_dir, f'{name}.txt').write_text(
                _output_text(self.name, (), ())
            )
        if completed:
            self.add_output(f'{name}.txt')
            Path(self.work_dir, f'{name}.txt').write_text(
                _output_text(self.name, (), ())
            )
            Path(self.work_dir, 'polaris_step_complete.log').write_text(
                f'{self.path} finished successfully.'
            )
            if baseline_status is not None:
                marker = 'passed' if baseline_status else 'failed'
                Path(self.work_dir, f'baseline_{marker}.log').write_text(
                    f'{self.name}\n'
                )
            if property_status is not None:
                marker = 'passed' if property_status else 'failed'
                Path(self.work_dir, f'property_check_{marker}.log').write_text(
                    f'{self.name}\n'
                )

    def add_dependency(self, step, name=None):
        if name is None:
            name = step.name
        self.dependencies[name] = step
        step.is_dependency = True

    def add_input(self, filename):
        self.inputs.append(str(filename))

    def add_output(self, filename):
        if filename not in self.outputs:
            self.outputs.append(filename)

    def __getstate__(self):
        state = self.__dict__.copy()
        state['logger'] = None
        return state

    def constrain_resources(self, available_resources):
        pass

    def runtime_setup(self):
        pass

    def run(self):
        if self.fail:
            raise RuntimeError(f'{self.name} failed intentionally')

        input_text = tuple(
            f'{Path(filename).name}:{Path(filename).read_text()}'
            for filename in self.inputs
        )
        dependency_names = tuple(self.dependencies)
        for output in self.outputs:
            Path(output).write_text(
                _output_text(self.name, input_text, dependency_names)
            )

    def run_with_dask(self, client, resources):
        self.run()

    def check_properties(self):
        if self.property_status is None:
            return False, None
        marker = 'passed' if self.property_status else 'failed'
        Path(self.work_dir, f'property_check_{marker}.log').write_text(
            f'{self.name}\n'
        )
        return True, self.property_status

    def validate_baselines(self):
        if self.baseline_status is None:
            return False, None
        marker = 'passed' if self.baseline_status else 'failed'
        Path(self.work_dir, f'baseline_{marker}.log').write_text(
            f'{self.name}\n'
        )
        return True, self.baseline_status


class SyntheticConfig:
    filepath = 'config.cfg'

    @staticmethod
    def get(section, option):
        if section == 'io':
            if option == 'format':
                return 'netcdf4'
            if option == 'engine':
                return 'netcdf4'
        return ''


class SyntheticLogger:
    def __init__(self):
        self.messages = []

    def info(self, message):
        self.messages.append(('info', message))

    def error(self, message):
        self.messages.append(('error', message))

    def exception(self, message):
        self.messages.append(('exception', message))


def test_synthetic_suite_matches_serial_outputs_and_markers(
    tmp_path, monkeypatch
):
    _patch_runtime_setup(monkeypatch)
    serial_suite = _make_success_suite(tmp_path / 'serial')
    scheduler_suite = _make_success_suite(tmp_path / 'scheduler')
    serial_log_dir = tmp_path / 'serial_logs'
    scheduler_log_dir = tmp_path / 'scheduler_logs'

    _run_serial_suite(serial_suite, serial_log_dir)
    results = _run_scheduler_suite(scheduler_suite, scheduler_log_dir)

    assert results['failures'] == 0
    _assert_suite_outputs_match(serial_suite, scheduler_suite)
    _assert_marker_equivalence(serial_suite, scheduler_suite)
    _assert_task_logs_cover_same_steps(
        serial_suite, scheduler_suite, serial_log_dir, scheduler_log_dir
    )
    _assert_success_event_coverage(scheduler_suite)


def test_synthetic_suite_records_failed_and_blocked_steps(
    tmp_path, monkeypatch
):
    _patch_runtime_setup(monkeypatch)
    suite = _make_failure_suite(tmp_path / 'failure')

    results = _run_scheduler_suite(suite, tmp_path / 'logs')

    assert results['failures'] == 1
    assert results['exec_fail_tasks'] == ['synthetic/failure_task']
    task = suite['tasks']['failure_task']
    assert Path(task.steps['independent'].work_dir, 'independent.txt').exists()
    assert not Path(task.steps['blocked'].work_dir, 'blocked.txt').exists()

    events = read_schedule_events(Path(task.work_dir, 'schedule_events.jsonl'))
    blocked = _events_for(events, 'step_skipped', 'blocked')
    failed = _events_for(events, 'step_failure', 'failure')
    assert [event['step'] for event in failed] == ['failing']
    assert [event['step'] for event in blocked] == ['blocked']


def _output_text(name, input_text, dependency_names):
    return (
        f'step={name}\n'
        f'inputs={list(input_text)}\n'
        f'dependencies={list(dependency_names)}\n'
    )


def _make_success_suite(base_work_dir):
    base_work_dir.mkdir()
    task_a_dir = base_work_dir / 'task_a'
    task_b_dir = base_work_dir / 'task_b'
    task_a_dir.mkdir()
    task_b_dir.mkdir()

    shared = SyntheticStep(base_work_dir, 'shared')
    shared.add_output('shared.txt')
    cached = SyntheticStep(base_work_dir, 'cached', cached=True)
    completed = SyntheticStep(
        base_work_dir,
        'completed',
        completed=True,
        baseline_status=True,
        property_status=True,
    )
    branch_a = SyntheticStep(
        base_work_dir,
        'branch_a',
        baseline_status=True,
        property_status=True,
        ntasks=2,
    )
    branch_a.add_dependency(shared)
    branch_a.add_input(Path(shared.work_dir, 'shared.txt'))
    branch_a.add_output('branch_a.txt')
    from_cached = SyntheticStep(base_work_dir, 'from_cached')
    from_cached.add_input(Path(cached.work_dir, 'cached.txt'))
    from_cached.add_output('from_cached.txt')
    branch_b = SyntheticStep(base_work_dir, 'branch_b')
    branch_b.add_dependency(shared)
    branch_b.add_input(Path(shared.work_dir, 'shared.txt'))
    branch_b.add_input(Path(completed.work_dir, 'completed.txt'))
    branch_b.add_output('branch_b.txt')
    independent = SyntheticStep(base_work_dir, 'independent')
    independent.add_output('independent.txt')

    task_a = _make_task(
        'task_a',
        'synthetic/task_a',
        task_a_dir,
        base_work_dir,
        [shared, cached, branch_a, from_cached],
    )
    task_b = _make_task(
        'task_b',
        'synthetic/task_b',
        task_b_dir,
        base_work_dir,
        [shared, completed, branch_b, independent],
    )
    return {'tasks': {'task_a': task_a, 'task_b': task_b}}


def _make_failure_suite(base_work_dir):
    base_work_dir.mkdir()
    task_dir = base_work_dir / 'failure_task'
    task_dir.mkdir()

    failing = SyntheticStep(base_work_dir, 'failing', fail=True)
    failing.add_output('failing.txt')
    blocked = SyntheticStep(base_work_dir, 'blocked')
    blocked.add_dependency(failing)
    blocked.add_input(Path(failing.work_dir, 'failing.txt'))
    blocked.add_output('blocked.txt')
    independent = SyntheticStep(base_work_dir, 'independent')
    independent.add_output('independent.txt')

    task = _make_task(
        'failure_task',
        'synthetic/failure_task',
        task_dir,
        base_work_dir,
        [failing, blocked, independent],
    )
    return {'tasks': {'failure_task': task}}


def _make_task(name, path, work_dir, base_work_dir, step_list):
    steps = {step.name: step for step in step_list}
    return SimpleNamespace(
        name=name,
        path=path,
        work_dir=str(work_dir),
        base_work_dir=str(base_work_dir),
        config=SimpleNamespace(filepath='config.cfg'),
        steps=steps,
        steps_to_run=list(steps),
        logger=None,
        stdout_logger=None,
        log_filename=None,
        new_step_log_file=False,
    )


def _patch_runtime_setup(monkeypatch):
    monkeypatch.setattr(
        run_shared, 'setup_config', lambda *args: SyntheticConfig()
    )
    monkeypatch.setattr(
        run_shared,
        'update_steps_to_run',
        lambda task_name, steps_to_run, steps_to_skip, config, steps: list(
            steps
        ),
    )
    monkeypatch.setattr(
        run_scheduler, 'setup_config', lambda *args: SyntheticConfig()
    )
    monkeypatch.setattr(
        run_scheduler,
        'update_steps_to_run',
        lambda task_name, steps_to_run, steps_to_skip, config, steps: list(
            steps
        ),
    )


def _available_resources():
    return {
        'cores': 2,
        'nodes': 1,
        'cores_per_node': 2,
        'gpus': 0,
        'mpi_allowed': True,
    }


def _run_serial_suite(suite, log_dir):
    log_dir.mkdir()
    cwd = os.getcwd()
    for task in suite['tasks'].values():
        task_name = task.path.replace('/', '_')
        try:
            result, success, *_ = run_shared.log_and_run_task(
                task=task,
                stdout_logger=SyntheticLogger(),
                task_logger=None,
                quiet=False,
                log_filename=str(log_dir / f'{task_name}.log'),
                is_task=False,
                steps_to_run=None,
                steps_to_skip=None,
                available_resources=_available_resources(),
                task_runner=run_shared.run_task,
            )
        finally:
            os.chdir(cwd)
        assert success, result


def _run_scheduler_suite(suite, log_dir):
    log_dir.mkdir()
    dask_client = SimpleNamespace(
        polaris_dask_runtime_info=DaskRuntimeInfo(
            backend='local',
            workers=2,
            scheduler_address='tcp://127.0.0.1:8786',
            total_cores=2,
            total_gpus=0,
        )
    )
    return run_scheduler.run_suite(
        suite=suite,
        stdout_logger=SyntheticLogger(),
        quiet=False,
        log_dir=str(log_dir),
        available_resources=_available_resources(),
        dask_client=dask_client,
    )


def _assert_suite_outputs_match(serial_suite, scheduler_suite):
    for task_name, serial_task in serial_suite['tasks'].items():
        scheduler_task = scheduler_suite['tasks'][task_name]
        for step_name, serial_step in serial_task.steps.items():
            scheduler_step = scheduler_task.steps[step_name]
            for output in serial_step.outputs:
                serial_output = Path(serial_step.work_dir, output)
                scheduler_output = Path(scheduler_step.work_dir, output)
                assert (
                    scheduler_output.read_text() == serial_output.read_text()
                )


def _assert_marker_equivalence(serial_suite, scheduler_suite):
    marker_names = [
        'baseline_passed.log',
        'polaris_step_complete.log',
        'property_check_passed.log',
        'step_after_run.pickle',
    ]
    for task_name, serial_task in serial_suite['tasks'].items():
        scheduler_task = scheduler_suite['tasks'][task_name]
        for step_name, serial_step in serial_task.steps.items():
            scheduler_step = scheduler_task.steps[step_name]
            for marker_name in marker_names:
                serial_marker = Path(serial_step.work_dir, marker_name)
                scheduler_marker = Path(scheduler_step.work_dir, marker_name)
                assert scheduler_marker.exists() == serial_marker.exists()


def _assert_task_logs_cover_same_steps(
    serial_suite, scheduler_suite, serial_log_dir, scheduler_log_dir
):
    scheduler_log_text = []
    for task_name, serial_task in serial_suite['tasks'].items():
        scheduler_task = scheduler_suite['tasks'][task_name]
        serial_log = _task_log(serial_task, serial_log_dir)
        scheduler_log = _task_log(scheduler_task, scheduler_log_dir)
        assert serial_log.exists()
        assert scheduler_log.exists()
        serial_text = serial_log.read_text()
        scheduler_text = scheduler_log.read_text()
        scheduler_log_text.append(scheduler_text)
        assert 'Running steps:' in serial_text
        assert 'Running steps:' in scheduler_text
        for step_name in serial_task.steps:
            assert f'* step: {step_name}' in serial_text
            assert step_name in scheduler_text

    combined_scheduler_log = '\n'.join(scheduler_log_text)
    for task in scheduler_suite['tasks'].values():
        for step_name in task.steps:
            assert f'* step: {step_name}' in combined_scheduler_log


def _task_log(task, log_dir):
    task_name = task.path.replace('/', '_')
    return Path(log_dir, f'{task_name}.log')


def _assert_success_event_coverage(suite):
    event_filenames = [
        Path(task.work_dir, 'schedule_events.jsonl')
        for task in suite['tasks'].values()
    ]
    summary = validate_phase1_schedule_event_files(
        event_filenames, require_dask_runtime=True
    )
    assert summary.scheduler_path_used
    assert summary.dask_runtime_used
    assert summary.single_active_step
    task_a_events = read_schedule_events(event_filenames[0])
    task_b_events = read_schedule_events(event_filenames[1])

    shared_starts = [
        event
        for event in task_a_events + task_b_events
        if event.get('event') == 'step_start' and event.get('step') == 'shared'
    ]
    assert len(shared_starts) == 1

    cached_skips = _events_for(task_a_events, 'step_skipped', 'skipped')
    completed_skips = _events_for(task_b_events, 'step_skipped', 'skipped')
    assert any(event.get('reason') == 'cached' for event in cached_skips)
    assert any(
        event.get('reason') == 'already completed' for event in completed_skips
    )
    assert any(
        event.get('event') == 'resource_reserved'
        and event.get('step') == 'branch_a'
        and event.get('cores') == 2
        for event in task_a_events
    )


def _events_for(events, event_name, result):
    return [
        event
        for event in events
        if event.get('event') == event_name and event.get('result') == result
    ]
