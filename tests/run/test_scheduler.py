import json
from types import SimpleNamespace

import pytest

import polaris.run.scheduler as run_scheduler
from polaris.run.dask import DaskRuntimeInfo
from polaris.run.scheduler import build_scheduler_graph, run_suite


class DummyStep:
    def __init__(self, tmp_path, name, cached=False):
        self.name = name
        self.path = f'ocean/{name}'
        self.work_dir = str(tmp_path / name)
        self.cached = cached
        self.dependencies = {}
        self.inputs = []
        self.outputs = []
        self.base_work_dir = str(tmp_path)
        self.config = SimpleNamespace(filepath='config.cfg')
        self.run_as_subprocess = False
        self.ntasks = 1
        self.min_tasks = 1
        self.cpus_per_task = 1
        self.min_cpus_per_task = 1
        self.gpus_per_task = 0
        self.min_gpus_per_task = 0
        self.max_memory = None
        (tmp_path / name).mkdir()

    def add_dependency(self, step, name=None):
        if name is None:
            name = step.name
        self.dependencies[name] = step

    def add_input(self, filename):
        self.inputs.append(filename)

    def add_output(self, filename):
        self.outputs.append(filename)

    @staticmethod
    def check_properties():
        return False, None

    @staticmethod
    def validate_baselines():
        return False, None


def test_scheduler_inventory_preserves_selected_order_and_status(tmp_path):
    init = DummyStep(tmp_path, 'init', cached=True)
    forward = DummyStep(tmp_path, 'forward')
    validate = DummyStep(tmp_path, 'validate')
    (tmp_path / 'validate' / 'polaris_step_complete.log').write_text(
        'complete\n'
    )
    task = SimpleNamespace(
        steps_to_run=['forward', 'init', 'validate'],
        steps={'init': init, 'forward': forward, 'validate': validate},
    )

    graph = build_scheduler_graph({'ocean/task': task})

    nodes = graph.ordered_nodes()
    assert [node.key for node in nodes] == [
        'ocean/task:forward',
        'ocean/task:init',
        'ocean/task:validate',
    ]
    assert [node.cached for node in nodes] == [False, True, False]
    assert [node.completed for node in nodes] == [False, False, True]
    assert all(node.selected for node in nodes)


def test_scheduler_adds_explicit_dependency_edge(tmp_path):
    init = DummyStep(tmp_path, 'init')
    forward = DummyStep(tmp_path, 'forward')
    forward.add_dependency(init)
    task = SimpleNamespace(
        steps_to_run=['forward', 'init'],
        steps={'init': init, 'forward': forward},
    )

    graph = build_scheduler_graph({'ocean/task': task})

    assert graph.predecessors['ocean/task:forward'] == {'ocean/task:init'}
    assert graph.successors['ocean/task:init'] == {'ocean/task:forward'}
    assert [node.step_name for node in graph.topological_order()] == [
        'init',
        'forward',
    ]


def test_scheduler_rejects_unsatisfied_explicit_dependency(tmp_path):
    init = DummyStep(tmp_path, 'init')
    forward = DummyStep(tmp_path, 'forward')
    forward.add_dependency(init)
    task = SimpleNamespace(
        steps_to_run=['forward'],
        steps={'init': init, 'forward': forward},
    )

    with pytest.raises(ValueError, match='not selected, cached'):
        build_scheduler_graph({'ocean/task': task})


@pytest.mark.parametrize('cached', [False, True])
def test_scheduler_preserves_satisfied_unselected_dependency(tmp_path, cached):
    init = DummyStep(tmp_path, 'init', cached=cached)
    forward = DummyStep(tmp_path, 'forward')
    forward.add_dependency(init)
    if not cached:
        (tmp_path / 'init' / 'polaris_step_complete.log').write_text(
            'complete\n'
        )
    task = SimpleNamespace(
        steps_to_run=['forward'],
        steps={'init': init, 'forward': forward},
    )

    graph = build_scheduler_graph({'ocean/task': task})
    dependency_key = 'satisfied:ocean/init'

    assert dependency_key in graph.nodes
    assert not graph.nodes[dependency_key].selected
    assert graph.predecessors['ocean/task:forward'] == {dependency_key}
    assert graph.successors[dependency_key] == {'ocean/task:forward'}


def test_scheduler_rejects_explicit_dependency_cycle(tmp_path):
    init = DummyStep(tmp_path, 'init')
    forward = DummyStep(tmp_path, 'forward')
    init.add_dependency(forward)
    forward.add_dependency(init)
    task = SimpleNamespace(
        steps_to_run=['init', 'forward'],
        steps={'init': init, 'forward': forward},
    )

    with pytest.raises(ValueError, match='contains a cycle'):
        build_scheduler_graph({'ocean/task': task})


def test_scheduler_adds_declared_file_dependency_edge(tmp_path):
    init = DummyStep(tmp_path, 'init')
    forward = DummyStep(tmp_path, 'forward')
    init.add_output('initial_state.nc')
    forward.add_input(tmp_path / 'init' / 'initial_state.nc')
    task = SimpleNamespace(
        steps_to_run=['forward', 'init'],
        steps={'init': init, 'forward': forward},
    )

    graph = build_scheduler_graph({'ocean/task': task})

    assert graph.predecessors['ocean/task:forward'] == {'ocean/task:init'}
    assert graph.successors['ocean/task:init'] == {'ocean/task:forward'}
    assert [node.step_name for node in graph.topological_order()] == [
        'init',
        'forward',
    ]


def test_scheduler_treats_existing_input_as_external(tmp_path):
    init = DummyStep(tmp_path, 'init')
    forward = DummyStep(tmp_path, 'forward')
    external_input = tmp_path / 'external.nc'
    external_input.write_text('external\n')
    forward.add_input(external_input)
    task = SimpleNamespace(
        steps_to_run=['forward', 'init'],
        steps={'init': init, 'forward': forward},
    )

    graph = build_scheduler_graph({'ocean/task': task})

    assert graph.predecessors['ocean/task:forward'] == set()
    assert [node.step_name for node in graph.topological_order()] == [
        'forward',
        'init',
    ]


def test_scheduler_rejects_missing_declared_input(tmp_path):
    forward = DummyStep(tmp_path, 'forward')
    forward.add_input(tmp_path / 'missing.nc')
    task = SimpleNamespace(
        steps_to_run=['forward'],
        steps={'forward': forward},
    )

    with pytest.raises(ValueError, match='does not exist'):
        build_scheduler_graph({'ocean/task': task})


@pytest.mark.parametrize('cached', [False, True])
def test_scheduler_preserves_satisfied_file_provider(tmp_path, cached):
    init = DummyStep(tmp_path, 'init', cached=cached)
    forward = DummyStep(tmp_path, 'forward')
    init.add_output('initial_state.nc')
    forward.add_input(tmp_path / 'init' / 'initial_state.nc')
    if not cached:
        (tmp_path / 'init' / 'polaris_step_complete.log').write_text(
            'complete\n'
        )
    task = SimpleNamespace(
        steps_to_run=['forward'],
        steps={'init': init, 'forward': forward},
    )

    graph = build_scheduler_graph({'ocean/task': task})
    provider_key = 'satisfied:ocean/init'

    assert provider_key in graph.nodes
    assert not graph.nodes[provider_key].selected
    assert graph.predecessors['ocean/task:forward'] == {provider_key}


def test_scheduler_does_not_infer_dependencies_from_selected_order(tmp_path):
    init = DummyStep(tmp_path, 'init')
    forward = DummyStep(tmp_path, 'forward')
    task = SimpleNamespace(
        steps_to_run=['forward', 'init'],
        steps={'init': init, 'forward': forward},
    )

    graph = build_scheduler_graph({'ocean/task': task})

    assert graph.predecessors['ocean/task:forward'] == set()
    assert graph.predecessors['ocean/task:init'] == set()
    assert [node.step_name for node in graph.topological_order()] == [
        'forward',
        'init',
    ]


def test_scheduler_adds_cross_task_explicit_dependency_edge(tmp_path):
    init = DummyStep(tmp_path, 'task_a_init')
    forward = DummyStep(tmp_path, 'task_b_forward')
    forward.add_dependency(init)
    task_a = SimpleNamespace(
        steps_to_run=['init'],
        steps={'init': init},
    )
    task_b = SimpleNamespace(
        steps_to_run=['forward'],
        steps={'forward': forward},
    )

    graph = build_scheduler_graph(
        {'ocean/task_b': task_b, 'ocean/task_a': task_a}
    )

    assert graph.predecessors['ocean/task_b:forward'] == {'ocean/task_a:init'}
    assert [node.key for node in graph.topological_order()] == [
        'ocean/task_a:init',
        'ocean/task_b:forward',
    ]


def test_scheduler_adds_cross_task_file_dependency_edge(tmp_path):
    init = DummyStep(tmp_path, 'task_a_init')
    forward = DummyStep(tmp_path, 'task_b_forward')
    init.add_output('initial_state.nc')
    forward.add_input(tmp_path / 'task_a_init' / 'initial_state.nc')
    task_a = SimpleNamespace(
        steps_to_run=['init'],
        steps={'init': init},
    )
    task_b = SimpleNamespace(
        steps_to_run=['forward'],
        steps={'forward': forward},
    )

    graph = build_scheduler_graph(
        {'ocean/task_b': task_b, 'ocean/task_a': task_a}
    )

    assert graph.predecessors['ocean/task_b:forward'] == {'ocean/task_a:init'}
    assert [node.key for node in graph.topological_order()] == [
        'ocean/task_a:init',
        'ocean/task_b:forward',
    ]


def test_scheduler_run_task_uses_graph_order_and_single_active_step(
    tmp_path, monkeypatch
):
    active_steps = 0
    max_active_steps = 0
    run_order = []

    class DummyLogger:
        def info(self, message):
            pass

    init = DummyStep(tmp_path, 'init')
    forward = DummyStep(tmp_path, 'forward')
    init.add_output('initial_state.nc')
    forward.add_input(tmp_path / 'init' / 'initial_state.nc')
    task = SimpleNamespace(
        path='ocean/task',
        work_dir=str(tmp_path),
        logger=DummyLogger(),
        stdout_logger=DummyLogger(),
        log_filename=None,
        new_step_log_file=False,
        steps_to_run=['forward', 'init'],
        steps={'init': init, 'forward': forward},
    )

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

    monkeypatch.setattr(run_scheduler, 'setup_config', lambda *args: object())
    monkeypatch.setattr(run_scheduler, 'run_step', _run_step)

    run_scheduler.run_task(
        task,
        {
            'cores': 2,
            'nodes': 1,
            'cores_per_node': 2,
            'gpus': 0,
            'mpi_allowed': True,
        },
        dask_client='client',
    )

    assert run_order == ['init', 'forward']
    assert max_active_steps == 1

    event_filename = tmp_path / 'schedule_events.jsonl'
    events = [
        json.loads(line) for line in event_filename.read_text().splitlines()
    ]
    assert events[0]['event'] == 'graph_constructed'
    assert [
        event['step']
        for event in events
        if event['event'] == 'ready_selection'
    ] == ['init', 'forward']
    assert (
        max(
            event.get('active_steps', 0)
            for event in events
            if event['event'] in {'step_start', 'step_finish'}
        )
        == 1
    )


def test_scheduler_records_dask_runtime_info(tmp_path, monkeypatch):
    class DummyLogger:
        def info(self, message):
            pass

    init = DummyStep(tmp_path, 'init')
    (tmp_path / 'init' / 'polaris_step_complete.log').write_text('complete\n')
    task = SimpleNamespace(
        path='ocean/task',
        work_dir=str(tmp_path),
        logger=DummyLogger(),
        stdout_logger=DummyLogger(),
        log_filename=None,
        new_step_log_file=False,
        steps_to_run=['init'],
        steps={'init': init},
    )
    dask_client = SimpleNamespace(
        polaris_dask_runtime_info=DaskRuntimeInfo(backend='local', workers=4)
    )

    monkeypatch.setattr(run_scheduler, 'setup_config', lambda *args: object())

    run_scheduler.run_task(
        task,
        {
            'cores': 4,
            'nodes': 1,
            'cores_per_node': 4,
            'gpus': 0,
            'mpi_allowed': True,
        },
        dask_client=dask_client,
    )

    event_filename = tmp_path / 'schedule_events.jsonl'
    events = [
        json.loads(line) for line in event_filename.read_text().splitlines()
    ]
    dask_events = [
        event for event in events if event['event'] == 'dask_runtime'
    ]

    assert dask_events == [
        {
            'backend': 'local',
            'event': 'dask_runtime',
            'time': dask_events[0]['time'],
            'workers': 4,
        }
    ]


def test_scheduler_run_suite_uses_suite_wide_graph(tmp_path, monkeypatch):
    run_order = []

    class DummyConfig:
        filepath = 'config.cfg'

        @staticmethod
        def get(section, option):
            if section == 'io':
                return 'netcdf4'
            return ''

    class DummyLogger:
        def __init__(self):
            self.messages = []

        def info(self, message):
            self.messages.append(('info', message))

        def error(self, message):
            self.messages.append(('error', message))

    init = DummyStep(tmp_path, 'task_a_init')
    forward = DummyStep(tmp_path, 'task_b_forward')
    init.add_output('initial_state.nc')
    forward.add_input(tmp_path / 'task_a_init' / 'initial_state.nc')

    task_a = SimpleNamespace(
        name='task_a',
        path='ocean/task_a',
        work_dir=str(tmp_path / 'task_a'),
        base_work_dir=str(tmp_path),
        config=SimpleNamespace(filepath='config.cfg'),
        steps_to_run=['init'],
        steps={'init': init},
    )
    task_b = SimpleNamespace(
        name='task_b',
        path='ocean/task_b',
        work_dir=str(tmp_path / 'task_b'),
        base_work_dir=str(tmp_path),
        config=SimpleNamespace(filepath='config.cfg'),
        steps_to_run=['forward'],
        steps={'forward': forward},
    )
    (tmp_path / 'task_a').mkdir()
    (tmp_path / 'task_b').mkdir()
    log_dir = tmp_path / 'case_outputs'
    log_dir.mkdir()

    def _run_step(
        task,
        step,
        new_log_file,
        available_resources,
        step_log_filename,
        dask_client=None,
    ):
        assert dask_client == 'client'
        run_order.append((task.path, step.name))
        if step.outputs:
            (tmp_path / step.name / step.outputs[0]).write_text('output\n')

    monkeypatch.setattr(
        run_scheduler, 'setup_config', lambda *args: DummyConfig()
    )
    monkeypatch.setattr(
        run_scheduler,
        'update_steps_to_run',
        lambda task_name, steps_to_run, steps_to_skip, config, steps: list(
            steps
        ),
    )
    monkeypatch.setattr(run_scheduler, 'run_step', _run_step)

    results = run_suite(
        suite={'tasks': {'task_b': task_b, 'task_a': task_a}},
        stdout_logger=DummyLogger(),
        quiet=False,
        log_dir=str(log_dir),
        available_resources={
            'cores': 2,
            'nodes': 1,
            'cores_per_node': 2,
            'gpus': 0,
            'mpi_allowed': True,
        },
        dask_client='client',
    )

    assert run_order == [
        ('ocean/task_a', 'task_a_init'),
        ('ocean/task_b', 'task_b_forward'),
    ]
    assert results['failures'] == 0
    assert set(results['task_times']) == {'ocean/task_a', 'ocean/task_b'}
    assert (tmp_path / 'task_a' / 'schedule_events.jsonl').exists()
    assert (tmp_path / 'task_b' / 'schedule_events.jsonl').exists()
