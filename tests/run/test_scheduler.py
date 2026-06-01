# HPC Validation (run manually after unit tests pass)
#
# Stage 1 — Chrysalis and Perlmutter CPU:
#   polaris run -s omega_pr
#   polaris serial -s omega_pr   (for baseline comparison)
#   Check: schedule_events.jsonl shows placement_enforced=true for local steps.
#   Check: MPI rank counts unchanged; wall time within 5% or 30 s of serial.
#   Check: schedule_events.jsonl shows dask_phase_start/stop lifecycle events.
#
# Stage 2 (only if Stage 1 passes) — Chrysalis omega_nightly/mpaso_pr and
# Perlmutter or Aurora GPU omega_pr:
#   polaris run -s omega_nightly  (Chrysalis)
#   polaris run -s mpaso_pr       (Chrysalis)
#   polaris run -s omega_pr       (Perlmutter GPU or Aurora)
#   Check: same acceptance criteria as Stage 1.
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

import polaris.run.scheduler as run_scheduler
from polaris.run.scheduler import build_scheduler_graph, run_suite


class DummyStep:
    def __init__(
        self,
        tmp_path,
        name,
        cached=False,
        baseline_status=None,
        property_status=None,
    ):
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
        self.args = None
        self.openmp_threads = 1
        self.is_dependency = False
        self.can_run_concurrently = False
        self.component = SimpleNamespace(name='component')
        self.subdir = name
        self.logger = None
        self.log_filename = None
        self.baseline_status = baseline_status
        self.property_status = property_status
        (tmp_path / name).mkdir()

    def add_dependency(self, step, name=None):
        if name is None:
            name = step.name
        self.dependencies[name] = step
        step.is_dependency = True

    def add_input(self, filename):
        self.inputs.append(filename)

    def add_output(self, filename):
        self.outputs.append(filename)

    def __getstate__(self):
        state = self.__dict__.copy()
        state['logger'] = None
        return state

    def constrain_resources(self, available_resources):
        pass

    def runtime_setup(self):
        pass

    def run(self):
        for output in self.outputs:
            with open(output, 'w') as output_file:
                output_file.write(f'{self.name}\n')

    def run_with_dask(self, client, resources):
        self.run()

    def check_properties(self):
        if self.property_status is None:
            return False, None
        marker = 'passed' if self.property_status else 'failed'
        filename = f'property_check_{marker}.log'
        with open(Path(self.work_dir) / filename, 'w') as log_file:
            log_file.write(f'{self.name}\n')
        return True, self.property_status

    def validate_baselines(self):
        if self.baseline_status is None:
            return False, None
        marker = 'passed' if self.baseline_status else 'failed'
        filename = f'baseline_{marker}.log'
        with open(Path(self.work_dir) / filename, 'w') as log_file:
            log_file.write(f'{self.name}\n')
        return True, self.baseline_status


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


def _available_resources(cores=2):
    return {
        'cores': cores,
        'nodes': 1,
        'cores_per_node': cores,
        'gpus': 0,
        'mpi_allowed': True,
    }


def _read_events(filename):
    return [json.loads(line) for line in filename.read_text().splitlines()]


def _make_task(tmp_path, name, steps):
    work_dir = tmp_path / name
    work_dir.mkdir()
    return SimpleNamespace(
        name=name,
        path=f'ocean/{name}',
        work_dir=str(work_dir),
        base_work_dir=str(tmp_path),
        config=SimpleNamespace(filepath='config.cfg'),
        steps_to_run=list(steps),
        steps=steps,
    )


def _patch_suite_setup(monkeypatch):
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


def _patch_dask_client(monkeypatch):
    """
    Patch dask_client_context with a synchronous in-process mock.

    The mock Dask client executes submitted functions immediately in the
    calling process, so monkeypatched functions (run_step, etc.) are called
    as expected by the test, without starting a real Dask cluster.
    """

    class MockFuture:
        def __init__(self, result_val, exc=None):
            self._result = result_val
            self._exc = exc

        def result(self):
            if self._exc is not None:
                raise self._exc
            return self._result

    class SyncDaskClient:
        def submit(self, fn, *args, resources=None, **kwargs):
            try:
                return MockFuture(fn(*args, **kwargs))
            except Exception as exc:
                return MockFuture(None, exc=exc)

    @contextmanager
    def _mock_dask_client_context(*args, **kwargs):
        yield SyncDaskClient()

    monkeypatch.setattr(
        run_scheduler, 'dask_client_context', _mock_dask_client_context
    )


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


def test_scheduler_deduplicates_repeated_shared_step(tmp_path):
    shared = DummyStep(tmp_path, 'shared')
    first = DummyStep(tmp_path, 'first')
    second = DummyStep(tmp_path, 'second')
    shared.add_output('shared.nc')
    first.add_input(tmp_path / 'shared' / 'shared.nc')
    second.add_input(tmp_path / 'shared' / 'shared.nc')
    task_a = SimpleNamespace(
        steps_to_run=['shared', 'first'],
        steps={'shared': shared, 'first': first},
    )
    task_b = SimpleNamespace(
        steps_to_run=['shared', 'second'],
        steps={'shared': shared, 'second': second},
    )

    graph = build_scheduler_graph(
        {'ocean/task_a': task_a, 'ocean/task_b': task_b}
    )

    assert [node.key for node in graph.ordered_nodes()] == [
        'ocean/task_a:shared',
        'ocean/task_a:first',
        'ocean/task_b:second',
    ]
    assert graph.predecessors['ocean/task_a:first'] == {'ocean/task_a:shared'}
    assert graph.predecessors['ocean/task_b:second'] == {'ocean/task_a:shared'}
    assert [node.key for node in graph.topological_order()] == [
        'ocean/task_a:shared',
        'ocean/task_a:first',
        'ocean/task_b:second',
    ]


def test_scheduler_deduplicates_symlinked_shared_step(tmp_path):
    shared = DummyStep(tmp_path, 'shared')
    shared.add_output('shared.nc')
    shared_alias = DummyStep(tmp_path, 'shared_alias')
    shared_link = tmp_path / 'shared_link'
    shared_link.symlink_to(tmp_path / 'shared', target_is_directory=True)
    shared_alias.work_dir = str(shared_link)
    shared_alias.add_output('shared.nc')
    consumer = DummyStep(tmp_path, 'consumer')
    consumer.add_input(shared_link / 'shared.nc')
    task_a = SimpleNamespace(
        steps_to_run=['shared'],
        steps={'shared': shared},
    )
    task_b = SimpleNamespace(
        steps_to_run=['shared_alias', 'consumer'],
        steps={'shared_alias': shared_alias, 'consumer': consumer},
    )

    graph = build_scheduler_graph(
        {'ocean/task_a': task_a, 'ocean/task_b': task_b}
    )

    assert [node.key for node in graph.ordered_nodes()] == [
        'ocean/task_a:shared',
        'ocean/task_b:consumer',
    ]
    assert graph.predecessors['ocean/task_b:consumer'] == {
        'ocean/task_a:shared'
    }


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
        timing_breakdown=None,
    ):
        nonlocal active_steps, max_active_steps
        assert dask_client is None
        active_steps += 1
        max_active_steps = max(max_active_steps, active_steps)
        run_order.append(step.name)
        active_steps -= 1

    monkeypatch.setattr(
        run_scheduler, 'setup_config', lambda *args: DummyConfig()
    )
    monkeypatch.setattr(run_scheduler, 'run_step', _run_step)
    _patch_dask_client(monkeypatch)

    run_scheduler.run_task(
        task,
        {
            'cores': 2,
            'nodes': 1,
            'cores_per_node': 2,
            'gpus': 0,
            'mpi_allowed': True,
        },
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
    resource_events = [
        event for event in events if event['event'] == 'resource_feasibility'
    ]
    assert len(resource_events) == 2
    assert all(event['feasible'] for event in resource_events)
    assert all(event['result'] == 'feasible' for event in resource_events)
    assert all(
        event['wait_reason'] == 'resources_available'
        for event in resource_events
    )
    assert all(event['insufficient'] == [] for event in resource_events)
    # R7: Dask phase lifecycle events surround LOCAL step execution.
    assert [event['result'] for event in events if 'result' in event] == [
        'reserved',  # control_plane_reserved
        'starting',  # dask_phase_launch_requested (before init)
        'started',  # dask_phase_start
        'feasible',
        'reserved',
        'running',
        'released',
        'success',  # init
        'feasible',
        'reserved',
        'running',
        'released',
        'success',  # forward
        'stopping',  # dask_phase_shutdown_requested
        'stopped',  # dask_phase_stop
    ]
    timing_events = [
        event for event in events if event['event'] == 'step_timing'
    ]
    assert [event['step'] for event in timing_events] == ['init', 'forward']
    assert all(event['step_duration'] >= 0.0 for event in timing_events)
    assert all(event['total_duration'] >= 0.0 for event in timing_events)
    assert (
        max(
            event.get('active_steps', 0)
            for event in events
            if event['event'] in {'step_start', 'step_finish'}
        )
        == 1
    )
    # Verify Dask lifecycle events are present.
    dask_events = [e['event'] for e in events if e['event'].startswith('dask')]
    assert 'dask_phase_launch_requested' in dask_events
    assert 'dask_phase_start' in dask_events
    assert 'dask_phase_stop' in dask_events


def test_scheduler_run_task_updates_rerun_markers(tmp_path, monkeypatch):
    class DummyLogger:
        def info(self, message):
            pass

    init = DummyStep(tmp_path, 'init')
    forward = DummyStep(
        tmp_path, 'forward', baseline_status=True, property_status=False
    )
    init.add_output('initial_state.nc')
    forward.add_dependency(init)
    forward.add_input(tmp_path / 'init' / 'initial_state.nc')
    forward.add_output('output.nc')
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

    monkeypatch.setattr(
        run_scheduler, 'setup_config', lambda *args: DummyConfig()
    )
    _patch_dask_client(monkeypatch)

    baselines_passed = run_scheduler.run_task(
        task,
        {
            'cores': 2,
            'nodes': 1,
            'cores_per_node': 2,
            'gpus': 0,
            'mpi_allowed': True,
        },
    )

    assert baselines_passed
    assert (tmp_path / 'init' / 'polaris_step_complete.log').exists()
    assert (tmp_path / 'init' / 'step_after_run.pickle').exists()
    assert (tmp_path / 'forward' / 'polaris_step_complete.log').exists()
    assert (tmp_path / 'forward' / 'baseline_passed.log').exists()
    assert (tmp_path / 'forward' / 'property_check_failed.log').exists()


def test_scheduler_run_task_blocks_failed_dependents_and_releases_resources(
    tmp_path, monkeypatch
):
    run_order = []

    class DummyLogger:
        def info(self, message):
            pass

    fail = DummyStep(tmp_path, 'fail')
    dependent = DummyStep(tmp_path, 'dependent')
    independent = DummyStep(tmp_path, 'independent')
    dependent.add_dependency(fail)
    task = SimpleNamespace(
        path='ocean/task',
        work_dir=str(tmp_path),
        logger=DummyLogger(),
        stdout_logger=DummyLogger(),
        log_filename=None,
        new_step_log_file=False,
        steps_to_run=['dependent', 'fail', 'independent'],
        steps={
            'fail': fail,
            'dependent': dependent,
            'independent': independent,
        },
    )

    def _run_step(
        task,
        step,
        new_log_file,
        available_resources,
        step_log_filename,
        dask_client=None,
        timing_breakdown=None,
    ):
        run_order.append(step.name)
        if step.name == 'fail':
            raise RuntimeError('expected failure')

    monkeypatch.setattr(
        run_scheduler, 'setup_config', lambda *args: DummyConfig()
    )
    monkeypatch.setattr(run_scheduler, 'run_step', _run_step)
    _patch_dask_client(monkeypatch)

    with pytest.raises(RuntimeError, match='expected failure'):
        run_scheduler.run_task(
            task,
            {
                'cores': 1,
                'nodes': 1,
                'cores_per_node': 1,
                'gpus': 0,
                'mpi_allowed': True,
            },
        )

    assert run_order == ['fail', 'independent']
    events = _read_events(tmp_path / 'schedule_events.jsonl')
    assert [
        event['step']
        for event in events
        if event['event'] == 'ready_selection'
    ] == ['fail', 'dependent', 'independent']

    failure_events = [
        event for event in events if event['event'] == 'step_failure'
    ]
    blocked_events = [
        event
        for event in events
        if event['event'] == 'step_skipped'
        and event['reason'] == 'blocked_dependency'
    ]
    release_events = [
        event for event in events if event['event'] == 'resource_released'
    ]

    assert failure_events[0]['step'] == 'fail'
    assert failure_events[0]['result'] == 'failure'
    timing_events = [
        event for event in events if event['event'] == 'step_timing'
    ]
    assert [event['step'] for event in timing_events] == [
        'fail',
        'independent',
    ]
    assert blocked_events[0]['step'] == 'dependent'
    assert blocked_events[0]['result'] == 'blocked'
    assert release_events[0]['step'] == 'fail'
    assert release_events[0]['active_steps'] == 0
    assert release_events[1]['step'] == 'independent'


def test_scheduler_records_infeasible_resource_diagnostics(
    tmp_path, monkeypatch
):
    class DummyLogger:
        def info(self, message):
            pass

    step = DummyStep(tmp_path, 'too_large')
    step.ntasks = 4
    step.min_tasks = 4
    task = SimpleNamespace(
        path='ocean/task',
        work_dir=str(tmp_path),
        logger=DummyLogger(),
        stdout_logger=DummyLogger(),
        log_filename=None,
        new_step_log_file=False,
        steps_to_run=['too_large'],
        steps={'too_large': step},
    )

    monkeypatch.setattr(
        run_scheduler, 'setup_config', lambda *args: DummyConfig()
    )

    with pytest.raises(ValueError, match='minimum'):
        run_scheduler.run_task(
            task,
            {
                'cores': 2,
                'nodes': 1,
                'cores_per_node': 2,
                'gpus': 0,
                'mpi_allowed': True,
            },
        )

    events = _read_events(tmp_path / 'schedule_events.jsonl')
    resource_events = [
        event for event in events if event['event'] == 'resource_feasibility'
    ]

    assert len(resource_events) == 1
    assert not resource_events[0]['feasible']
    assert resource_events[0]['result'] == 'infeasible'
    assert resource_events[0]['wait_reason'] == 'insufficient_resources'
    assert resource_events[0]['insufficient'] == ['request']
    assert 'minimum' in resource_events[0]['reason']


def test_scheduler_submits_local_step_to_dask_worker(tmp_path, monkeypatch):
    # R7: LOCAL steps are submitted to the pinned Dask worker via
    # submit_step_to_node, not called directly from the scheduler process.
    submitted_resources: list[dict] = []
    run_order: list[str] = []

    class DummyLogger:
        def info(self, message):
            pass

    step = DummyStep(tmp_path, 'prep')
    task = SimpleNamespace(
        path='ocean/task',
        work_dir=str(tmp_path),
        logger=DummyLogger(),
        stdout_logger=DummyLogger(),
        log_filename=None,
        new_step_log_file=False,
        steps_to_run=['prep'],
        steps={'prep': step},
    )

    def _run_step(
        task,
        step,
        new_log_file,
        available_resources,
        step_log_filename,
        dask_client=None,
        timing_breakdown=None,
    ):
        assert dask_client is None
        run_order.append(step.name)

    original_submit = run_scheduler.submit_step_to_node

    def _capturing_submit(
        client, fn, node_index, cores, gpus, *args, **kwargs
    ):
        submitted_resources.append({f'node_{node_index}_cores': cores})
        return original_submit(
            client, fn, node_index, cores, gpus, *args, **kwargs
        )

    monkeypatch.setattr(
        run_scheduler, 'setup_config', lambda *args: DummyConfig()
    )
    monkeypatch.setattr(run_scheduler, 'run_step', _run_step)
    monkeypatch.setattr(
        run_scheduler, 'submit_step_to_node', _capturing_submit
    )
    _patch_dask_client(monkeypatch)

    run_scheduler.run_task(
        task,
        {
            'cores': 4,
            'nodes': 1,
            'cores_per_node': 4,
            'gpus': 0,
            'mpi_allowed': True,
        },
    )

    assert run_order == ['prep']
    assert len(submitted_resources) == 1
    assert list(submitted_resources[0].keys()) == ['node_0_cores']

    events = _read_events(tmp_path / 'schedule_events.jsonl')
    dask_lifecycle_events = [
        event['event']
        for event in events
        if event['event'].startswith('dask_phase')
    ]
    # Phase starts for LOCAL steps and stops at the end of the task.
    assert 'dask_phase_launch_requested' in dask_lifecycle_events
    assert 'dask_phase_start' in dask_lifecycle_events
    assert 'dask_phase_stop' in dask_lifecycle_events


def test_scheduler_submits_subprocess_step_to_dask_worker(
    tmp_path, monkeypatch
):
    # R7: run_as_subprocess steps are also submitted to the Dask worker,
    # not called directly.
    submitted_resources: list[dict] = []
    subprocess_called: list[str] = []

    class DummyLogger:
        def info(self, message):
            pass

    step = DummyStep(tmp_path, 'subproc')
    step.run_as_subprocess = True
    task = SimpleNamespace(
        path='ocean/task',
        work_dir=str(tmp_path),
        logger=DummyLogger(),
        stdout_logger=DummyLogger(),
        log_filename=None,
        new_step_log_file=False,
        steps_to_run=['subproc'],
        steps={'subproc': step},
    )

    def _run_subprocess(
        logger,
        step,
        new_log_file,
        subprocess_command='serial',
        dask_client=None,
        timing_breakdown=None,
    ):
        subprocess_called.append(step.name)

    original_submit = run_scheduler.submit_step_to_node

    def _capturing_submit(
        client, fn, node_index, cores, gpus, *args, **kwargs
    ):
        submitted_resources.append({f'node_{node_index}_cores': cores})
        return original_submit(
            client, fn, node_index, cores, gpus, *args, **kwargs
        )

    monkeypatch.setattr(
        run_scheduler, 'setup_config', lambda *args: DummyConfig()
    )
    monkeypatch.setattr(
        run_scheduler, 'run_step_as_subprocess', _run_subprocess
    )
    monkeypatch.setattr(
        run_scheduler, 'submit_step_to_node', _capturing_submit
    )
    _patch_dask_client(monkeypatch)

    run_scheduler.run_task(
        task,
        {
            'cores': 2,
            'nodes': 1,
            'cores_per_node': 2,
            'gpus': 0,
            'mpi_allowed': True,
        },
    )

    # Step was called inside the Dask worker (submitted), not directly.
    assert subprocess_called == ['subproc']
    assert len(submitted_resources) == 1


def test_scheduler_no_dask_phase_for_mpi_only_task(tmp_path, monkeypatch):
    # R7: A task containing only MPI steps must not start a Dask phase.
    class DummyLogger:
        def info(self, message):
            pass

    mpi_step = DummyStep(tmp_path, 'mpi_run')
    mpi_step.ntasks = 4
    mpi_step.min_tasks = 4
    task = SimpleNamespace(
        path='ocean/task',
        work_dir=str(tmp_path),
        logger=DummyLogger(),
        stdout_logger=DummyLogger(),
        log_filename=None,
        new_step_log_file=False,
        steps_to_run=['mpi_run'],
        steps={'mpi_run': mpi_step},
    )

    def _run_step(
        task,
        step,
        new_log_file,
        available_resources,
        step_log_filename,
        dask_client=None,
        timing_breakdown=None,
    ):
        pass

    monkeypatch.setattr(
        run_scheduler, 'setup_config', lambda *args: DummyConfig()
    )
    monkeypatch.setattr(run_scheduler, 'run_step', _run_step)
    # dask_client_context must NOT be called for an MPI-only task.
    monkeypatch.setattr(
        run_scheduler,
        'dask_client_context',
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError('dask_client_context must not start for MPI tasks')
        ),
    )

    run_scheduler.run_task(
        task,
        {
            'cores': 4,
            'nodes': 1,
            'cores_per_node': 4,
            'gpus': 0,
            'mpi_allowed': True,
        },
    )

    events = _read_events(tmp_path / 'schedule_events.jsonl')
    dask_events = [e['event'] for e in events if e['event'].startswith('dask')]
    assert dask_events == []


def test_scheduler_dask_workers_stay_alive_across_mpi_step(
    tmp_path, monkeypatch
):
    # R8 (stop_workers_before_mpi=False default): Workers must NOT stop before
    # an MPI step.  A single Dask phase spans the whole task; only one
    # start/stop pair occurs.
    class DummyLogger:
        def info(self, message):
            pass

    local_step = DummyStep(tmp_path, 'local')
    mpi_step = DummyStep(tmp_path, 'mpi_run')
    mpi_step.ntasks = 4
    mpi_step.min_tasks = 4
    local_step2 = DummyStep(tmp_path, 'local2')
    task = SimpleNamespace(
        path='ocean/task',
        work_dir=str(tmp_path),
        logger=DummyLogger(),
        stdout_logger=DummyLogger(),
        log_filename=None,
        new_step_log_file=False,
        steps_to_run=['local', 'mpi_run', 'local2'],
        steps={
            'local': local_step,
            'mpi_run': mpi_step,
            'local2': local_step2,
        },
    )

    def _run_step(
        task,
        step,
        new_log_file,
        available_resources,
        step_log_filename,
        dask_client=None,
        timing_breakdown=None,
    ):
        pass

    monkeypatch.setattr(
        run_scheduler, 'setup_config', lambda *args: DummyConfig()
    )
    monkeypatch.setattr(run_scheduler, 'run_step', _run_step)
    _patch_dask_client(monkeypatch)

    run_scheduler.run_task(
        task,
        {
            'cores': 4,
            'nodes': 1,
            'cores_per_node': 4,
            'gpus': 0,
            'mpi_allowed': True,
        },
    )

    events = _read_events(tmp_path / 'schedule_events.jsonl')
    phase_events = [
        e['event'] for e in events if e['event'].startswith('dask_phase')
    ]
    # R8: single phase per task — workers stay alive through the MPI step.
    assert phase_events.count('dask_phase_start') == 1
    assert phase_events.count('dask_phase_stop') == 1

    # The single stop must come after all steps have finished.
    last_finish_idx = max(
        i
        for i, e in enumerate(events)
        if e['event'] in {'step_finish', 'step_failure'}
    )
    stop_idx = next(
        i for i, e in enumerate(events) if e['event'] == 'dask_phase_stop'
    )
    assert stop_idx > last_finish_idx, (
        'dask_phase_stop must come after all steps complete, not mid-task'
    )

    # All three steps must have been started (mode-batching puts both LOCAL
    # steps before the MPI step when all three are independent).
    step_starts = [e['step'] for e in events if e['event'] == 'step_start']
    assert set(step_starts) == {'local', 'mpi_run', 'local2'}
    assert len(step_starts) == 3


def test_scheduler_workers_stop_before_mpi_when_flag_set(
    tmp_path, monkeypatch
):
    # R9: When stop_workers_before_mpi=True (e.g. Perlmutter), workers must
    # stop before each MPI step and restart for following LOCAL steps.
    # Uses a forced dependency chain local_a → mpi_step → local_b so that
    # mode-batching cannot reorder the steps.
    class DummyLogger:
        def info(self, message):
            pass

    local_a = DummyStep(tmp_path, 'local_a')
    mpi_step = DummyStep(tmp_path, 'mpi_step')
    mpi_step.ntasks = 4
    mpi_step.min_tasks = 4
    local_b = DummyStep(tmp_path, 'local_b')
    mpi_step.add_dependency(local_a)
    local_b.add_dependency(mpi_step)

    task = SimpleNamespace(
        path='ocean/task',
        work_dir=str(tmp_path),
        logger=DummyLogger(),
        stdout_logger=DummyLogger(),
        log_filename=None,
        new_step_log_file=False,
        steps_to_run=['local_a', 'mpi_step', 'local_b'],
        steps={
            'local_a': local_a,
            'mpi_step': mpi_step,
            'local_b': local_b,
        },
    )

    def _run_step(
        task,
        step,
        new_log_file,
        available_resources,
        step_log_filename,
        dask_client=None,
        timing_breakdown=None,
    ):
        pass

    monkeypatch.setattr(
        run_scheduler, 'setup_config', lambda *args: DummyConfig()
    )
    monkeypatch.setattr(run_scheduler, 'run_step', _run_step)
    monkeypatch.setattr(
        run_scheduler, '_get_stop_workers_before_mpi', lambda *_: True
    )
    _patch_dask_client(monkeypatch)

    run_scheduler.run_task(
        task,
        {
            'cores': 4,
            'nodes': 1,
            'cores_per_node': 4,
            'gpus': 0,
            'mpi_allowed': True,
        },
    )

    events = _read_events(tmp_path / 'schedule_events.jsonl')

    # All three steps must have run in dependency order.
    step_starts = [e['step'] for e in events if e['event'] == 'step_start']
    assert step_starts == ['local_a', 'mpi_step', 'local_b']

    # Workers must stop before mpi_step and restart before local_b.
    event_pairs = [(e['event'], e.get('step')) for e in events]
    stop_indices = [
        i for i, e in enumerate(events) if e['event'] == 'dask_phase_stop'
    ]
    start_indices = [
        i for i, e in enumerate(events) if e['event'] == 'dask_phase_start'
    ]
    mpi_start_idx = next(
        i
        for i, e in enumerate(events)
        if e['event'] == 'step_start' and e.get('step') == 'mpi_step'
    )
    local_b_start_idx = next(
        i
        for i, e in enumerate(events)
        if e['event'] == 'step_start' and e.get('step') == 'local_b'
    )
    # A stop must precede mpi_step.
    assert any(idx < mpi_start_idx for idx in stop_indices), (
        'Expected a dask_phase_stop before mpi_step'
    )
    # A start must precede local_b (workers restarted after the MPI step).
    assert any(idx < local_b_start_idx for idx in start_indices[1:]), (
        'Expected a second dask_phase_start before local_b'
    )
    # Sanity: 2 starts and 2 stops (one around the MPI step, one at task end).
    assert len(start_indices) == 2
    assert len(stop_indices) == 2
    _ = event_pairs  # suppress unused-variable lint


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
        timing_breakdown=None,
    ):
        assert dask_client is None
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
    _patch_dask_client(monkeypatch)

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
    )

    assert run_order == [
        ('ocean/task_a', 'task_a_init'),
        ('ocean/task_b', 'task_b_forward'),
    ]
    assert results['failures'] == 0
    assert set(results['task_times']) == {'ocean/task_a', 'ocean/task_b'}
    assert (tmp_path / 'task_a' / 'schedule_events.jsonl').exists()
    assert (tmp_path / 'task_b' / 'schedule_events.jsonl').exists()


def test_scheduler_run_suite_runs_shared_step_once(tmp_path, monkeypatch):
    run_order = []
    step_durations = {'shared': 2.0, 'first': 3.0, 'second': 5.0}
    clock = SimpleNamespace(current=100.0)

    shared = DummyStep(tmp_path, 'shared')
    first = DummyStep(tmp_path, 'first')
    second = DummyStep(tmp_path, 'second')
    shared.add_output('shared.nc')
    first.add_input(tmp_path / 'shared' / 'shared.nc')
    second.add_input(tmp_path / 'shared' / 'shared.nc')

    task_a = _make_task(tmp_path, 'task_a', {'shared': shared, 'first': first})
    task_b = _make_task(
        tmp_path, 'task_b', {'shared': shared, 'second': second}
    )
    log_dir = tmp_path / 'case_outputs'
    log_dir.mkdir()

    def _run_step(
        task,
        step,
        new_log_file,
        available_resources,
        step_log_filename,
        dask_client=None,
        timing_breakdown=None,
    ):
        run_order.append((task.path, step.name))
        clock.current += step_durations[step.name]
        if step.outputs:
            (tmp_path / step.name / step.outputs[0]).write_text('output\n')

    _patch_suite_setup(monkeypatch)
    monkeypatch.setattr(run_scheduler, 'run_step', _run_step)
    monkeypatch.setattr(run_scheduler.time, 'time', lambda: clock.current)
    _patch_dask_client(monkeypatch)

    stdout_logger = DummyLogger()
    results = run_suite(
        suite={'tasks': {'task_a': task_a, 'task_b': task_b}},
        stdout_logger=stdout_logger,
        quiet=False,
        log_dir=str(log_dir),
        available_resources=_available_resources(),
    )

    assert run_order == [
        ('ocean/task_a', 'shared'),
        ('ocean/task_a', 'first'),
        ('ocean/task_b', 'second'),
    ]
    assert results['failures'] == 0
    assert results['task_times'] == {
        'ocean/task_a': 5.0,
        'ocean/task_b': 7.0,
    }
    selected_order_messages = [
        message for level, message in stdout_logger.messages if level == 'info'
    ]
    assert (
        '    1. ocean/task_a/shared [pending; wait: no_dependencies]'
        in selected_order_messages
    )
    assert (
        '    2. ocean/task_a/first '
        '[pending; wait: waiting_for_dependencies]' in selected_order_messages
    )
    assert (
        '    1. ocean/task_b/second '
        '[pending; wait: waiting_for_dependencies]' in selected_order_messages
    )

    task_a_events = _read_events(tmp_path / 'task_a' / 'schedule_events.jsonl')
    task_b_events = _read_events(tmp_path / 'task_b' / 'schedule_events.jsonl')
    assert [
        event['step']
        for event in task_a_events + task_b_events
        if event['event'] == 'step_start'
    ] == ['shared', 'first', 'second']


def test_scheduler_run_suite_blocks_failed_dependents(tmp_path, monkeypatch):
    run_order = []

    fail = DummyStep(tmp_path, 'task_a_fail')
    dependent = DummyStep(tmp_path, 'task_b_dependent')
    independent = DummyStep(tmp_path, 'task_c_independent')
    dependent.add_dependency(fail)

    task_a = _make_task(tmp_path, 'task_a', {'fail': fail})
    task_b = _make_task(tmp_path, 'task_b', {'dependent': dependent})
    task_c = _make_task(tmp_path, 'task_c', {'independent': independent})
    log_dir = tmp_path / 'case_outputs'
    log_dir.mkdir()

    def _run_step(
        task,
        step,
        new_log_file,
        available_resources,
        step_log_filename,
        dask_client=None,
        timing_breakdown=None,
    ):
        run_order.append((task.path, step.name))
        if step.name == 'task_a_fail':
            raise RuntimeError('expected failure')

    _patch_suite_setup(monkeypatch)
    monkeypatch.setattr(run_scheduler, 'run_step', _run_step)
    _patch_dask_client(monkeypatch)

    results = run_suite(
        suite={
            'tasks': {'task_b': task_b, 'task_a': task_a, 'task_c': task_c}
        },
        stdout_logger=DummyLogger(),
        quiet=False,
        log_dir=str(log_dir),
        available_resources=_available_resources(),
    )

    assert run_order == [
        ('ocean/task_a', 'task_a_fail'),
        ('ocean/task_c', 'task_c_independent'),
    ]
    assert results['failures'] == 2
    assert results['exec_fail_tasks'] == ['ocean/task_b', 'ocean/task_a']

    events = _read_events(tmp_path / 'task_b' / 'schedule_events.jsonl')
    blocked_events = [
        event
        for event in events
        if event['event'] == 'step_skipped'
        and event['reason'] == 'blocked_dependency'
    ]

    assert len(blocked_events) == 1
    assert blocked_events[0]['blocked_dependencies'] == ['ocean/task_a:fail']
    assert blocked_events[0]['result'] == 'blocked'
    task_a_events = _read_events(tmp_path / 'task_a' / 'schedule_events.jsonl')
    assert [
        event['result']
        for event in task_a_events
        if event['event'] in {'step_failure', 'resource_released'}
    ] == ['failure', 'released']
    assert [
        event['active_steps']
        for event in task_a_events
        if event['event'] == 'resource_released'
    ] == [0]


def test_scheduler_run_suite_records_completed_and_cached_skips(
    tmp_path, monkeypatch
):
    run_order = []
    completed = DummyStep(tmp_path, 'completed')
    cached = DummyStep(tmp_path, 'cached', cached=True)
    after_completed = DummyStep(tmp_path, 'after_completed')
    after_cached = DummyStep(tmp_path, 'after_cached')
    completed.add_output('completed.nc')
    cached.add_output('cached.nc')
    after_completed.add_input(tmp_path / 'completed' / 'completed.nc')
    after_cached.add_input(tmp_path / 'cached' / 'cached.nc')
    (tmp_path / 'completed' / 'polaris_step_complete.log').write_text(
        'complete\n'
    )
    (tmp_path / 'completed' / 'completed.nc').write_text('completed\n')
    (tmp_path / 'completed' / 'baseline_passed.log').write_text('pass\n')
    (tmp_path / 'completed' / 'property_check_failed.log').write_text('fail\n')

    task = _make_task(
        tmp_path,
        'task',
        {
            'completed': completed,
            'cached': cached,
            'after_completed': after_completed,
            'after_cached': after_cached,
        },
    )
    log_dir = tmp_path / 'case_outputs'
    log_dir.mkdir()

    def _run_step(
        task,
        step,
        new_log_file,
        available_resources,
        step_log_filename,
        dask_client=None,
        timing_breakdown=None,
    ):
        run_order.append(step.name)

    _patch_suite_setup(monkeypatch)
    monkeypatch.setattr(run_scheduler, 'run_step', _run_step)
    _patch_dask_client(monkeypatch)

    results = run_suite(
        suite={'tasks': {'task': task}},
        stdout_logger=DummyLogger(),
        quiet=False,
        log_dir=str(log_dir),
        available_resources=_available_resources(),
    )

    assert results['failures'] == 0
    assert run_order == ['after_completed', 'after_cached']
    events = _read_events(tmp_path / 'task' / 'schedule_events.jsonl')
    skip_events = [
        event for event in events if event['event'] == 'step_skipped'
    ]

    assert [
        (event['step'], event['reason'], event['result'])
        for event in skip_events
    ] == [
        ('completed', 'already completed', 'skipped'),
        ('cached', 'cached', 'skipped'),
    ]
    assert all(event['satisfies_dependencies'] for event in skip_events)
    completed_event = skip_events[0]
    assert completed_event['baseline_status']
    assert not completed_event['property_status']
    assert {
        event['wait_reason']
        for event in events
        if event['event'] == 'ready_selection'
    } == {'no_dependencies', 'dependencies_satisfied'}


def test_scheduler_run_suite_records_single_suite_active_step(
    tmp_path, monkeypatch
):
    run_order = []
    tasks = {}
    for index in range(3):
        step = DummyStep(tmp_path, f'task_{index}_step')
        task = _make_task(tmp_path, f'task_{index}', {'step': step})
        tasks[task.name] = task

    log_dir = tmp_path / 'case_outputs'
    log_dir.mkdir()

    def _run_step(
        task,
        step,
        new_log_file,
        available_resources,
        step_log_filename,
        dask_client=None,
        timing_breakdown=None,
    ):
        run_order.append((task.path, step.name))

    _patch_suite_setup(monkeypatch)
    monkeypatch.setattr(run_scheduler, 'run_step', _run_step)
    _patch_dask_client(monkeypatch)

    results = run_suite(
        suite={'tasks': tasks},
        stdout_logger=DummyLogger(),
        quiet=False,
        log_dir=str(log_dir),
        available_resources=_available_resources(),
    )

    assert results['failures'] == 0
    assert run_order == [
        ('ocean/task_0', 'task_0_step'),
        ('ocean/task_1', 'task_1_step'),
        ('ocean/task_2', 'task_2_step'),
    ]

    suite_active_steps: list[int] = []
    for task_name in tasks:
        events = _read_events(tmp_path / task_name / 'schedule_events.jsonl')
        suite_active_steps.extend(
            event['suite_active_steps']
            for event in events
            if event.get('suite_active_steps') is not None
        )

    assert max(suite_active_steps) == 1


def test_scheduler_passes_placement_correct_resources_to_run_step(
    tmp_path, monkeypatch
):
    # LOCAL (non-MPI) step sees nodes=1; MPI step sees the full allocation.
    received: dict[str, dict] = {}

    class DummyLogger:
        def info(self, message):
            pass

    local_step = DummyStep(tmp_path, 'local')  # ntasks=1, no args → LOCAL
    mpi_step = DummyStep(tmp_path, 'mpi')
    mpi_step.ntasks = 4
    mpi_step.min_tasks = 2

    task = SimpleNamespace(
        path='ocean/task',
        work_dir=str(tmp_path),
        logger=DummyLogger(),
        stdout_logger=DummyLogger(),
        log_filename=None,
        new_step_log_file=False,
        steps_to_run=['local', 'mpi'],
        steps={'local': local_step, 'mpi': mpi_step},
    )

    def _run_step(
        task,
        step,
        new_log_file,
        available_resources,
        step_log_filename,
        dask_client=None,
        timing_breakdown=None,
    ):
        received[step.name] = dict(available_resources)

    monkeypatch.setattr(
        run_scheduler, 'setup_config', lambda *args: DummyConfig()
    )
    monkeypatch.setattr(run_scheduler, 'run_step', _run_step)
    _patch_dask_client(monkeypatch)

    run_scheduler.run_task(
        task,
        {
            'cores': 192,
            'nodes': 3,
            'cores_per_node': 64,
            'node_core_counts': (64, 64, 64),
            'gpus': 0,
            'node_gpu_counts': (0, 0, 0),
            'gpus_per_node': 0,
            'mpi_allowed': True,
        },
    )

    # Local step receives a single-node view.
    local_res = received['local']
    assert local_res['nodes'] == 1
    assert local_res['node_core_counts'] == (local_res['cores'],)

    # MPI step receives the full allocation view.
    mpi_res = received['mpi']
    assert mpi_res['cores'] == 192
    assert mpi_res['nodes'] == 3
    assert mpi_res['node_core_counts'] == (64, 64, 64)

    # Neither view subtracts a control-plane core.
    assert local_res['cores'] > 0
    assert mpi_res['cores'] == 192


def test_resource_reserved_local_step_fields(tmp_path, monkeypatch):
    # resource_reserved events for LOCAL (non-MPI) steps must carry
    # placement_enforced=True and resource_scope='single_node' so that
    # Phase 2 can verify pinned-worker placement at the event level.
    class DummyLogger:
        def info(self, message):
            pass

    step = DummyStep(tmp_path, 'local')
    task = SimpleNamespace(
        path='ocean/task',
        work_dir=str(tmp_path),
        logger=DummyLogger(),
        stdout_logger=DummyLogger(),
        log_filename=None,
        new_step_log_file=False,
        steps_to_run=['local'],
        steps={'local': step},
    )

    monkeypatch.setattr(
        run_scheduler, 'setup_config', lambda *args: DummyConfig()
    )
    monkeypatch.setattr(
        run_scheduler,
        'run_step',
        lambda *args, **kwargs: None,
    )
    _patch_dask_client(monkeypatch)

    run_scheduler.run_task(
        task,
        {
            'cores': 4,
            'nodes': 1,
            'cores_per_node': 4,
            'gpus': 0,
            'mpi_allowed': True,
        },
    )

    events = _read_events(tmp_path / 'schedule_events.jsonl')
    reserved = [e for e in events if e['event'] == 'resource_reserved']
    assert len(reserved) >= 1
    step_event = next(e for e in reserved if e['step'] == 'local')
    assert step_event['execution_kind'] == 'local'
    assert step_event['placement_enforced'] is True
    assert step_event['resource_scope'] == 'single_node'
    assert 'node_indices' in step_event
    assert isinstance(step_event['node_indices'], list)


def test_resource_reserved_mpi_step_fields(tmp_path, monkeypatch):
    # resource_reserved events for MPI steps must carry
    # placement_enforced=False and resource_scope='allocation' because
    # the MPI launcher — not the scheduler — handles process placement.
    class DummyLogger:
        def info(self, message):
            pass

    step = DummyStep(tmp_path, 'mpi')
    step.ntasks = 4
    step.min_tasks = 2
    task = SimpleNamespace(
        path='ocean/task',
        work_dir=str(tmp_path),
        logger=DummyLogger(),
        stdout_logger=DummyLogger(),
        log_filename=None,
        new_step_log_file=False,
        steps_to_run=['mpi'],
        steps={'mpi': step},
    )

    monkeypatch.setattr(
        run_scheduler, 'setup_config', lambda *args: DummyConfig()
    )
    monkeypatch.setattr(
        run_scheduler,
        'run_step',
        lambda *args, **kwargs: None,
    )
    _patch_dask_client(monkeypatch)

    run_scheduler.run_task(
        task,
        {
            'cores': 4,
            'nodes': 1,
            'cores_per_node': 4,
            'gpus': 0,
            'mpi_allowed': True,
        },
    )

    events = _read_events(tmp_path / 'schedule_events.jsonl')
    reserved = [e for e in events if e['event'] == 'resource_reserved']
    assert len(reserved) >= 1
    step_event = next(e for e in reserved if e['step'] == 'mpi')
    assert step_event['execution_kind'] == 'mpi'
    assert step_event['placement_enforced'] is False
    assert step_event['resource_scope'] == 'allocation'
    assert 'node_indices' in step_event


def test_resource_released_includes_placement_fields(tmp_path, monkeypatch):
    # resource_released events must carry execution_kind and node_indices so
    # that post-run analysis tools can join them with resource_reserved events
    # without needing a separate lookup.
    class DummyLogger:
        def info(self, message):
            pass

    local_step = DummyStep(tmp_path, 'local')
    mpi_step = DummyStep(tmp_path, 'mpi')
    mpi_step.ntasks = 4
    mpi_step.min_tasks = 2
    task = SimpleNamespace(
        path='ocean/task',
        work_dir=str(tmp_path),
        logger=DummyLogger(),
        stdout_logger=DummyLogger(),
        log_filename=None,
        new_step_log_file=False,
        steps_to_run=['local', 'mpi'],
        steps={'local': local_step, 'mpi': mpi_step},
    )

    monkeypatch.setattr(
        run_scheduler, 'setup_config', lambda *args: DummyConfig()
    )
    monkeypatch.setattr(
        run_scheduler,
        'run_step',
        lambda *args, **kwargs: None,
    )
    _patch_dask_client(monkeypatch)

    run_scheduler.run_task(
        task,
        {
            'cores': 4,
            'nodes': 1,
            'cores_per_node': 4,
            'gpus': 0,
            'mpi_allowed': True,
        },
    )

    events = _read_events(tmp_path / 'schedule_events.jsonl')
    released = [e for e in events if e['event'] == 'resource_released']
    assert len(released) == 2

    local_rel = next(e for e in released if e['step'] == 'local')
    assert local_rel['execution_kind'] == 'local'
    assert isinstance(local_rel['node_indices'], list)

    mpi_rel = next(e for e in released if e['step'] == 'mpi')
    assert mpi_rel['execution_kind'] == 'mpi'
    assert isinstance(mpi_rel['node_indices'], list)


def test_placement_fields_consistent_across_mixed_task(tmp_path, monkeypatch):
    # For a task with both local and MPI steps, verify that placement_enforced
    # and resource_scope in resource_reserved events are consistent with
    # execution_kind for every step, and that resource_released events always
    # carry execution_kind and node_indices.
    class DummyLogger:
        def info(self, message):
            pass

    local_step = DummyStep(tmp_path, 'local')
    mpi_step = DummyStep(tmp_path, 'mpi')
    mpi_step.ntasks = 4
    mpi_step.min_tasks = 2
    task = SimpleNamespace(
        path='ocean/task',
        work_dir=str(tmp_path),
        logger=DummyLogger(),
        stdout_logger=DummyLogger(),
        log_filename=None,
        new_step_log_file=False,
        steps_to_run=['local', 'mpi'],
        steps={'local': local_step, 'mpi': mpi_step},
    )

    monkeypatch.setattr(
        run_scheduler, 'setup_config', lambda *args: DummyConfig()
    )
    monkeypatch.setattr(
        run_scheduler,
        'run_step',
        lambda *args, **kwargs: None,
    )
    _patch_dask_client(monkeypatch)

    run_scheduler.run_task(
        task,
        {
            'cores': 4,
            'nodes': 1,
            'cores_per_node': 4,
            'gpus': 0,
            'mpi_allowed': True,
        },
    )

    events = _read_events(tmp_path / 'schedule_events.jsonl')
    for event in events:
        if event['event'] != 'resource_reserved':
            continue
        kind = event['execution_kind']
        if kind == 'local':
            assert event['placement_enforced'] is True, (
                f'local step {event["step"]} missing placement_enforced'
            )
            assert event['resource_scope'] == 'single_node', (
                f'local step {event["step"]} wrong resource_scope'
            )
        else:
            assert event['placement_enforced'] is False, (
                f'mpi step {event["step"]} missing placement_enforced'
            )
            assert event['resource_scope'] == 'allocation', (
                f'mpi step {event["step"]} wrong resource_scope'
            )

    for event in events:
        if event['event'] != 'resource_released':
            continue
        assert 'execution_kind' in event, (
            f'resource_released for {event["step"]} missing execution_kind'
        )
        assert 'node_indices' in event, (
            f'resource_released for {event["step"]} missing node_indices'
        )
