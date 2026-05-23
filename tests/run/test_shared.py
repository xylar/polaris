from configparser import ConfigParser
from types import SimpleNamespace

import pytest

import polaris.run.shared as run_shared
from polaris.run.shared import (
    accumulate_statuses,
    read_baseline_status_from_logs,
    read_property_status_from_logs,
    run_step_as_subprocess,
    update_steps_to_run,
)
from polaris.step import Step


def test_update_steps_to_run_from_config_with_skip():
    config = ConfigParser()
    config.add_section('task')
    config.set('task', 'steps_to_run', 'init, forward validate')
    steps = {'init': None, 'forward': None, 'validate': None}

    steps_to_run = update_steps_to_run(
        task_name='task',
        steps_to_run=None,
        steps_to_skip=['forward'],
        config=config,
        steps=steps,
    )

    assert steps_to_run == ['init', 'validate']


def test_update_steps_to_run_from_argument():
    config = ConfigParser()
    steps = {'init': None, 'forward': None}

    steps_to_run = update_steps_to_run(
        task_name='task',
        steps_to_run=['forward'],
        steps_to_skip=None,
        config=config,
        steps=steps,
    )

    assert steps_to_run == ['forward']


def test_update_steps_to_run_rejects_unknown_step():
    config = ConfigParser()
    steps = {'init': None}

    with pytest.raises(ValueError, match='was requested'):
        update_steps_to_run(
            task_name='task',
            steps_to_run=['missing'],
            steps_to_skip=None,
            config=config,
            steps=steps,
        )


def test_update_steps_to_run_rejects_unknown_skip_step():
    config = ConfigParser()
    steps = {'init': None}

    with pytest.raises(ValueError, match='flagged not to run'):
        update_steps_to_run(
            task_name='task',
            steps_to_run=['init'],
            steps_to_skip=['missing'],
            config=config,
            steps=steps,
        )


def test_accumulate_statuses():
    assert accumulate_statuses(None, True) is True
    assert accumulate_statuses(None, False) is False
    assert accumulate_statuses(True, True) is True
    assert accumulate_statuses(True, False) is False
    assert accumulate_statuses(False, True) is False
    assert accumulate_statuses(False, False) is False


def test_read_baseline_status_from_logs(tmp_path):
    assert read_baseline_status_from_logs(str(tmp_path)) is None

    (tmp_path / 'baseline_failed.log').write_text('failed\n')
    assert read_baseline_status_from_logs(str(tmp_path)) is False

    (tmp_path / 'baseline_failed.log').unlink()
    (tmp_path / 'baseline_passed.log').write_text('passed\n')
    assert read_baseline_status_from_logs(str(tmp_path)) is True


def test_read_property_status_from_logs(tmp_path):
    assert read_property_status_from_logs(str(tmp_path)) is None

    (tmp_path / 'property_check_failed.log').write_text('failed\n')
    assert read_property_status_from_logs(str(tmp_path)) is False

    (tmp_path / 'property_check_failed.log').unlink()
    (tmp_path / 'property_check_passed.log').write_text('passed\n')
    assert read_property_status_from_logs(str(tmp_path)) is True


def test_run_task_keeps_single_active_step(tmp_path, monkeypatch):
    active_steps = 0
    max_active_steps = 0
    run_order = []

    class DummyLogger:
        def info(self, message):
            pass

    class DummyStep:
        def __init__(self, name):
            self.name = name
            self.work_dir = str(tmp_path / name)
            self.base_work_dir = str(tmp_path)
            self.cached = False
            self.run_as_subprocess = False
            self.config = SimpleNamespace(filepath='config.cfg')
            (tmp_path / name).mkdir()

        @staticmethod
        def check_properties():
            return False, None

        @staticmethod
        def validate_baselines():
            return False, None

    def _run_step(
        task,
        step,
        new_log_file,
        available_resources,
        step_log_filename,
        dask_client=None,
    ):
        nonlocal active_steps, max_active_steps
        assert dask_client == 'client'
        active_steps += 1
        max_active_steps = max(max_active_steps, active_steps)
        run_order.append(step.name)
        active_steps -= 1

    monkeypatch.setattr(run_shared, 'setup_config', lambda *args: object())
    monkeypatch.setattr(run_shared, 'run_step', _run_step)

    task = SimpleNamespace(
        logger=DummyLogger(),
        stdout_logger=DummyLogger(),
        log_filename=None,
        new_step_log_file=False,
        steps_to_run=['step_a', 'step_b'],
        steps={
            'step_a': DummyStep('step_a'),
            'step_b': DummyStep('step_b'),
        },
    )

    run_shared.run_task(task, {'cores': 2}, dask_client='client')

    assert run_order == ['step_a', 'step_b']
    assert max_active_steps == 1


def test_run_step_uses_dask_hook(tmp_path, monkeypatch):
    calls = []

    class DummyLogger:
        def info(self, message):
            pass

    class HookStep(Step):
        def __init__(self):
            super().__init__(
                component=SimpleNamespace(name='ocean'),
                name='hook',
                cpus_per_task=3,
                ntasks=1,
                dask_workers=3,
            )
            self.work_dir = str(tmp_path)
            self.base_work_dir = str(tmp_path)

        def run_with_dask(self, client, resources):
            calls.append((client, resources))

    monkeypatch.chdir(tmp_path)

    task = SimpleNamespace(logger=DummyLogger())
    step = HookStep()
    available_resources = {
        'cores': 8,
        'cores_per_node': 4,
        'nodes': 2,
        'mpi_allowed': True,
    }

    run_shared.run_step(
        task,
        step,
        new_log_file=False,
        available_resources=available_resources,
        step_log_filename=None,
        dask_client='client',
    )

    assert len(calls) == 1
    client, resources = calls[0]
    assert client == 'client'
    assert resources.cores == 3
    assert resources.workers == 3


def test_run_step_as_subprocess_passes_dask_scheduler_address(
    tmp_path, monkeypatch
):
    captured = {}

    class DummyLogger:
        def info(self, message):
            pass

        def error(self, message):
            pass

    class DummyScheduler:
        address = 'tcp://scheduler:8786'

    class DummyClient:
        scheduler = DummyScheduler()

    def _check_call(args, logger=None, **kwargs):
        captured['args'] = args
        captured['env'] = kwargs['env']

    step = SimpleNamespace(
        path='ocean/task/init',
        work_dir=str(tmp_path),
        log_filename=None,
    )
    monkeypatch.setattr(run_shared, 'check_call', _check_call)

    run_step_as_subprocess(
        DummyLogger(),
        step,
        new_log_file=False,
        subprocess_command='run',
        dask_client=DummyClient(),
    )

    assert captured['args'] == ['polaris', 'run', '--step_is_subprocess']
    assert (
        captured['env']['POLARIS_DASK_SCHEDULER_ADDRESS']
        == 'tcp://scheduler:8786'
    )
