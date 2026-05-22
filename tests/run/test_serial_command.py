import sys
from types import SimpleNamespace

import pytest

import polaris.__main__ as polaris_main
import polaris.run.serial as serial_command


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


class DummyConfig:
    filepath = 'config.cfg'

    @staticmethod
    def get(section, option):
        if section == 'io':
            return 'netcdf4'
        return ''


class DummyComponent:
    name = 'ocean'

    @staticmethod
    def get_available_resources():
        return {'cores': 2}


def test_top_level_serial_dispatch(monkeypatch):
    called = {'serial': False}

    def _main():
        called['serial'] = True

    monkeypatch.setattr(sys, 'argv', ['polaris', 'serial'])
    monkeypatch.setattr(polaris_main.run_serial, 'main', _main)

    polaris_main.main()

    assert called['serial']


def test_serial_help(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['polaris', 'serial', '--help'])

    with pytest.raises(SystemExit) as exc_info:
        serial_command.main()

    assert exc_info.value.code == 0
    assert 'usage: polaris serial' in capsys.readouterr().out


def test_serial_suite_uses_shared_runner_without_scheduler_kwargs(
    tmp_path, monkeypatch
):
    calls = []

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
    ):
        calls.append(
            dict(
                task=task.path,
                task_logger=task_logger,
                log_filename=log_filename,
                is_task=is_task,
                available_resources=available_resources,
            )
        )
        return 'PASS', True, 0.0, False, False

    component = DummyComponent()
    task = SimpleNamespace(
        base_work_dir=str(tmp_path),
        component=component,
        path='ocean/task',
    )
    suite = {'tasks': {'task': task}, 'work_dir': str(tmp_path)}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(serial_command, 'unpickle_suite', lambda name: suite)
    monkeypatch.setattr(serial_command, 'setup_config', lambda *args: object())
    monkeypatch.setattr(
        serial_command, 'set_parallel_systems', lambda *args: None
    )
    monkeypatch.setattr(serial_command, 'LoggingContext', DummyLoggingContext)
    monkeypatch.setattr(serial_command, 'log_and_run_task', _log_and_run_task)
    monkeypatch.setattr(
        serial_command,
        'write_output_for_pull_request',
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        serial_command, 'log_task_runtimes', lambda *args, **kwargs: None
    )

    serial_command.run_tasks('suite')

    assert calls == [
        {
            'task': 'ocean/task',
            'task_logger': None,
            'log_filename': f'{tmp_path}/case_outputs/ocean_task.log',
            'is_task': False,
            'available_resources': {'cores': 2},
        }
    ]


def test_serial_single_step_uses_shared_runner_without_dask(
    tmp_path, monkeypatch
):
    called = {}

    class DummyTask:
        def __init__(self, component, name):
            self.component = component
            self.name = name
            self.path = f'{component.name}/{name}'
            self.steps = {}

        def add_step(self, step):
            self.steps[step.name] = step

    def _run_task(task, available_resources):
        called['task'] = task
        called['available_resources'] = available_resources

    step = SimpleNamespace(
        component=DummyComponent(),
        name='init',
        path='ocean/task/init',
        base_work_dir=str(tmp_path),
        config=SimpleNamespace(filepath='ocean.cfg'),
        run_as_subprocess=True,
    )

    monkeypatch.chdir(tmp_path)
    (tmp_path / 'step.pickle').write_text('')
    monkeypatch.setattr(serial_command.pickle, 'load', lambda handle: step)
    monkeypatch.setattr(serial_command, 'Task', DummyTask)
    monkeypatch.setattr(
        serial_command, 'setup_config', lambda *args: DummyConfig()
    )
    monkeypatch.setattr(
        serial_command, 'set_parallel_systems', lambda *args: None
    )
    monkeypatch.setattr(serial_command, 'LoggingContext', DummyLoggingContext)
    monkeypatch.setattr(serial_command, 'run_task', _run_task)
    monkeypatch.setattr(
        serial_command, 'log_function_call', lambda *args, **kwargs: None
    )

    serial_command.run_single_step(step_is_subprocess=True)

    assert called['task'].steps == {'init': step}
    assert called['available_resources'] == {'cores': 2}
    assert not step.run_as_subprocess
