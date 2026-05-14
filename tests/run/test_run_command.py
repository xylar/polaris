import sys

import pytest

import polaris.__main__ as polaris_main
import polaris.run.parallel as run_command


def test_top_level_run_dispatch(monkeypatch):
    called = {'run': False}

    def _main():
        called['run'] = True

    monkeypatch.setattr(sys, 'argv', ['polaris', 'run'])
    monkeypatch.setattr(polaris_main.run_parallel, 'main', _main)

    polaris_main.main()

    assert called['run']


def test_run_help(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['polaris', 'run', '--help'])

    with pytest.raises(SystemExit) as exc_info:
        run_command.main()

    assert exc_info.value.code == 0
    assert 'usage: polaris run' in capsys.readouterr().out


def test_run_explicit_suite_dispatch(monkeypatch):
    called = {}

    def _run_tasks(suite_name, quiet=False, **kwargs):
        called['suite_name'] = suite_name
        called['quiet'] = quiet
        called['kwargs'] = kwargs

    monkeypatch.setattr(sys, 'argv', ['polaris', 'run', 'omega_pr', '-q'])
    monkeypatch.setattr(run_command, 'run_tasks', _run_tasks)

    run_command.main()

    assert called == {
        'suite_name': 'omega_pr',
        'quiet': True,
        'kwargs': {},
    }


def test_run_task_workdir_dispatch(tmp_path, monkeypatch):
    called = {}

    def _run_tasks(
        suite_name,
        quiet=False,
        is_task=False,
        steps_to_run=None,
        steps_to_skip=None,
    ):
        called['suite_name'] = suite_name
        called['quiet'] = quiet
        called['is_task'] = is_task
        called['steps_to_run'] = steps_to_run
        called['steps_to_skip'] = steps_to_skip

    monkeypatch.chdir(tmp_path)
    (tmp_path / 'task.pickle').write_text('')
    monkeypatch.setattr(
        sys,
        'argv',
        [
            'polaris',
            'run',
            '--steps',
            'init',
            'forward',
            '--skip_steps',
            'viz',
        ],
    )
    monkeypatch.setattr(run_command, 'run_tasks', _run_tasks)

    run_command.main()

    assert called == {
        'suite_name': 'task',
        'quiet': False,
        'is_task': True,
        'steps_to_run': ['init', 'forward'],
        'steps_to_skip': ['viz'],
    }


def test_run_step_workdir_dispatch(tmp_path, monkeypatch):
    called = {}

    def _run_single_step(step_is_subprocess=False, quiet=False):
        called['step_is_subprocess'] = step_is_subprocess
        called['quiet'] = quiet

    monkeypatch.chdir(tmp_path)
    (tmp_path / 'step.pickle').write_text('')
    monkeypatch.setattr(
        sys, 'argv', ['polaris', 'run', '--step_is_subprocess']
    )
    monkeypatch.setattr(run_command, 'run_single_step', _run_single_step)

    run_command.main()

    assert called == {
        'step_is_subprocess': True,
        'quiet': False,
    }


def test_run_auto_suite_dispatch(tmp_path, monkeypatch):
    called = {}

    def _run_tasks(suite_name, quiet=False, **kwargs):
        called['suite_name'] = suite_name
        called['quiet'] = quiet
        called['kwargs'] = kwargs

    monkeypatch.chdir(tmp_path)
    (tmp_path / 'omega_pr.pickle').write_text('')
    monkeypatch.setattr(sys, 'argv', ['polaris', 'run', '-q'])
    monkeypatch.setattr(run_command, 'run_tasks', _run_tasks)

    run_command.main()

    assert called == {
        'suite_name': 'omega_pr',
        'quiet': True,
        'kwargs': {},
    }


def test_run_auto_suite_requires_unique_pickle(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'omega_pr.pickle').write_text('')
    (tmp_path / 'mpaso_pr.pickle').write_text('')
    monkeypatch.setattr(sys, 'argv', ['polaris', 'run'])

    with pytest.raises(ValueError, match='polaris run <suite>'):
        run_command.main()
