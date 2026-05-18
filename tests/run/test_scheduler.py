from types import SimpleNamespace

import pytest

import polaris.run.scheduler as run_scheduler
from polaris.run.scheduler import build_scheduler_graph


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
