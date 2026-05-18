from types import SimpleNamespace

import pytest

from polaris.run.scheduler import build_scheduler_graph


class DummyStep:
    def __init__(self, tmp_path, name, cached=False):
        self.name = name
        self.path = f'ocean/{name}'
        self.work_dir = str(tmp_path / name)
        self.cached = cached
        self.dependencies = {}
        (tmp_path / name).mkdir()

    def add_dependency(self, step, name=None):
        if name is None:
            name = step.name
        self.dependencies[name] = step


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
