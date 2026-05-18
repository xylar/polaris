import sys

import pytest

import polaris.setup as setup
import polaris.suite as suite


def test_job_script_run_command_defaults_to_serial():
    assert (
        setup._get_job_script_run_command('serial')
        == 'source load_polaris_env.sh\npolaris serial'
    )


def test_job_script_run_command_can_use_run():
    assert (
        setup._get_job_script_run_command('run', suite='omega_pr')
        == 'source load_polaris_env.sh\npolaris run omega_pr'
    )


def test_job_script_run_command_rejects_invalid_command():
    with pytest.raises(ValueError, match='Invalid run_command'):
        setup._get_job_script_run_command('parallel')


def test_setup_cli_run_command_default(monkeypatch):
    called = {}

    def _setup_tasks(**kwargs):
        called.update(kwargs)

    monkeypatch.setattr(
        sys,
        'argv',
        ['polaris', 'setup', '-w', 'work', '-t', 'ocean/task'],
    )
    monkeypatch.setattr(setup, 'setup_tasks', _setup_tasks)

    setup.main()

    assert called['run_command'] == 'serial'


def test_setup_cli_run_command_opt_in(monkeypatch):
    called = {}

    def _setup_tasks(**kwargs):
        called.update(kwargs)

    monkeypatch.setattr(
        sys,
        'argv',
        [
            'polaris',
            'setup',
            '-w',
            'work',
            '-t',
            'ocean/task',
            '--run_command',
            'run',
        ],
    )
    monkeypatch.setattr(setup, 'setup_tasks', _setup_tasks)

    setup.main()

    assert called['run_command'] == 'run'


def test_suite_cli_run_command_default(monkeypatch):
    called = {}

    def _setup_suite(**kwargs):
        called.update(kwargs)

    monkeypatch.setattr(
        sys,
        'argv',
        [
            'polaris',
            'suite',
            '-c',
            'ocean',
            '-t',
            'omega_pr',
            '-w',
            'work',
        ],
    )
    monkeypatch.setattr(suite, 'setup_suite', _setup_suite)

    suite.main()

    assert called['run_command'] == 'serial'


def test_suite_cli_run_command_opt_in(monkeypatch):
    called = {}

    def _setup_suite(**kwargs):
        called.update(kwargs)

    monkeypatch.setattr(
        sys,
        'argv',
        [
            'polaris',
            'suite',
            '-c',
            'ocean',
            '-t',
            'omega_pr',
            '-w',
            'work',
            '--run_command',
            'run',
        ],
    )
    monkeypatch.setattr(suite, 'setup_suite', _setup_suite)

    suite.main()

    assert called['run_command'] == 'run'
