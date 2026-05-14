from configparser import ConfigParser
from types import SimpleNamespace

import pytest

import polaris.run.shared as run_shared
from polaris.run.shared import (
    accumulate_statuses,
    read_baseline_status_from_logs,
    read_property_status_from_logs,
    update_steps_to_run,
)


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
