import sys
from contextlib import contextmanager
from types import SimpleNamespace

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


def test_run_tasks_uses_one_dask_lifecycle(tmp_path, monkeypatch):
    events = []

    class DummyComponent:
        name = 'ocean'

        @staticmethod
        def get_available_resources():
            return {'cores': 2}

    class DummyLogger:
        def info(self, message):
            events.append(('info', message))

        def error(self, message):
            events.append(('error', message))

    class DummyLoggingContext:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return DummyLogger()

        def __exit__(self, exc_type, exc_value, traceback):
            return False

    @contextmanager
    def _dask_client_context(available_resources, logger=None):
        assert available_resources == {'cores': 2}
        events.append(('dask', 'start'))
        yield 'client'
        events.append(('dask', 'stop'))

    def _log_and_run_task(
        task,
        stdout_logger,
        task_logger,
        quiet,
        log_filename,
        is_task,
        steps_to_run,
        steps_to_skip,
        available_resources,
        subprocess_command='serial',
        dask_client=None,
        task_runner=None,
    ):
        assert dask_client == 'client'
        assert subprocess_command == 'run'
        assert task_runner == run_command.run_task_with_scheduler
        events.append(('task', task.path))
        return 'PASS', True, 0.0, False, False

    component = DummyComponent()
    tasks = {
        'task_a': SimpleNamespace(
            base_work_dir=str(tmp_path),
            component=component,
            path='component/task_a',
        ),
        'task_b': SimpleNamespace(
            base_work_dir=str(tmp_path),
            component=component,
            path='component/task_b',
        ),
    }
    suite = {'tasks': tasks, 'work_dir': str(tmp_path)}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run_command, 'unpickle_suite', lambda name: suite)
    monkeypatch.setattr(run_command, 'setup_config', lambda *args: object())
    monkeypatch.setattr(
        run_command, 'set_parallel_systems', lambda *args: None
    )
    monkeypatch.setattr(run_command, 'LoggingContext', DummyLoggingContext)
    monkeypatch.setattr(
        run_command, 'dask_client_context', _dask_client_context
    )
    monkeypatch.setattr(run_command, 'log_and_run_task', _log_and_run_task)
    monkeypatch.setattr(
        run_command,
        'write_output_for_pull_request',
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        run_command, 'log_task_runtimes', lambda *args, **kwargs: None
    )

    run_command.run_tasks('suite')

    assert events.count(('dask', 'start')) == 1
    assert events.count(('dask', 'stop')) == 1
    assert events.index(('dask', 'start')) < events.index(
        ('task', 'component/task_a')
    )
    assert events.index(('task', 'component/task_b')) < events.index(
        ('dask', 'stop')
    )


def test_run_task_scope_uses_scheduler_runner(tmp_path, monkeypatch):
    called = {}

    class DummyComponent:
        name = 'ocean'

        @staticmethod
        def get_available_resources():
            return {'cores': 2}

    class DummyLogger:
        def info(self, message):
            pass

        def error(self, message):
            pass

    class DummyLoggingContext:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return DummyLogger()

        def __exit__(self, exc_type, exc_value, traceback):
            return False

    @contextmanager
    def _dask_client_context(available_resources, logger=None):
        yield 'client'

    def _log_and_run_task(
        task,
        stdout_logger,
        task_logger,
        quiet,
        log_filename,
        is_task,
        steps_to_run,
        steps_to_skip,
        available_resources,
        subprocess_command='serial',
        dask_client=None,
        task_runner=None,
    ):
        called['is_task'] = is_task
        called['task_runner'] = task_runner
        called['log_filename'] = log_filename
        return 'PASS', True, 0.0, False, False

    component = DummyComponent()
    task = SimpleNamespace(
        base_work_dir=str(tmp_path),
        component=component,
        path='component/task',
    )
    suite = {'tasks': {'task': task}, 'work_dir': str(tmp_path)}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run_command, 'unpickle_suite', lambda name: suite)
    monkeypatch.setattr(run_command, 'setup_config', lambda *args: object())
    monkeypatch.setattr(
        run_command, 'set_parallel_systems', lambda *args: None
    )
    monkeypatch.setattr(run_command, 'LoggingContext', DummyLoggingContext)
    monkeypatch.setattr(
        run_command, 'dask_client_context', _dask_client_context
    )
    monkeypatch.setattr(run_command, 'log_and_run_task', _log_and_run_task)
    monkeypatch.setattr(
        run_command,
        'write_output_for_pull_request',
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        run_command, 'log_task_runtimes', lambda *args, **kwargs: None
    )

    run_command.run_tasks('task', is_task=True)

    assert called == {
        'is_task': True,
        'task_runner': run_command.run_task_with_scheduler,
        'log_filename': None,
    }
