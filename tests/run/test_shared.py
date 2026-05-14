from configparser import ConfigParser

import pytest

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
