import pytest

from polaris.run.dask import (
    AllocationDaskRuntimeBackend,
    LocalDaskRuntimeBackend,
    ParallelSystemDaskProcessLauncher,
    dask_client_context,
    get_dask_runtime_info,
    get_dask_worker_count,
    plan_dask_launch,
    select_dask_runtime_backend,
)


class DummyLogger:
    def __init__(self):
        self.messages = []

    def info(self, message):
        self.messages.append(message)


class FakeProcess:
    def __init__(self, name, events):
        self.name = name
        self.events = events
        self.returncode = None
        events.append((name, 'start'))

    def poll(self):
        return self.returncode

    def terminate(self):
        self.events.append((self.name, 'terminate'))
        self.returncode = 0

    def wait(self, timeout=None):
        self.events.append((self.name, 'wait', timeout))
        self.returncode = 0

    def kill(self):
        self.events.append((self.name, 'kill'))
        self.returncode = -9


class FakeClient:
    def __init__(self, scheduler_file):
        self.scheduler_file = scheduler_file
        self.closed = False

    def close(self):
        self.closed = True


def test_get_dask_worker_count():
    assert get_dask_worker_count({}) == 1
    assert get_dask_worker_count({'cores': None}) == 1
    assert get_dask_worker_count({'cores': 0}) == 1
    assert get_dask_worker_count({'cores': 4}) == 4


def test_select_dask_runtime_backend_defaults_to_local():
    backend = select_dask_runtime_backend({'cores': 2})

    assert isinstance(backend, LocalDaskRuntimeBackend)
    assert backend.runtime_info.backend == 'local'
    assert backend.runtime_info.workers == 2
    assert backend.runtime_info.local_fallback
    assert backend.runtime_info.fallback_reason == 'single_node_allocation'


def test_select_dask_runtime_backend_rejects_unknown():
    with pytest.raises(ValueError, match='Unsupported Dask runtime backend'):
        select_dask_runtime_backend({}, backend_name='unknown')


def test_select_dask_runtime_backend_auto_allocation_with_launcher():
    backend = select_dask_runtime_backend(
        {
            'cores': 64,
            'nodes': 2,
            'cores_per_node': 32,
            'gpus': 0,
            'mpi_allowed': True,
        },
        process_launcher=object(),
        client_class=FakeClient,
    )

    assert isinstance(backend, AllocationDaskRuntimeBackend)
    assert backend.runtime_info.backend == 'allocation'
    assert backend.runtime_info.workers == 64
    assert not backend.runtime_info.local_fallback
    assert backend.runtime_info.worker_groups == (
        {
            'node_index': 0,
            'workers': 32,
            'threads_per_worker': 1,
            'cores_per_worker': 1,
            'gpus': 0,
        },
        {
            'node_index': 1,
            'workers': 32,
            'threads_per_worker': 1,
            'cores_per_worker': 1,
            'gpus': 0,
        },
    )


def test_select_dask_runtime_backend_auto_falls_back_without_launcher():
    backend = select_dask_runtime_backend(
        {
            'cores': 64,
            'nodes': 2,
            'cores_per_node': 32,
            'gpus': 0,
            'mpi_allowed': True,
        }
    )

    assert isinstance(backend, LocalDaskRuntimeBackend)
    assert backend.runtime_info.backend == 'local'
    assert backend.runtime_info.workers == 32
    assert backend.runtime_info.local_fallback
    assert backend.runtime_info.fallback_reason == 'missing_process_launcher'


def test_plan_dask_launch_single_node_fallback():
    plan = plan_dask_launch(
        {
            'cores': 4,
            'nodes': 1,
            'cores_per_node': 8,
            'gpus': 0,
            'mpi_allowed': True,
        }
    )

    assert plan.backend == 'local'
    assert plan.local_fallback
    assert plan.fallback_reason == 'single_node_allocation'
    assert plan.scheduler_node == 0
    assert plan.worker_count == 4
    assert plan.total_cores == 4
    assert plan.total_gpus == 0
    assert plan.worker_groups[0].node_index == 0
    assert plan.worker_groups[0].workers == 4
    assert plan.worker_groups[0].threads_per_worker == 1
    assert plan.worker_groups[0].cores_per_worker == 1


def test_plan_dask_launch_multi_node_cpu_allocation():
    plan = plan_dask_launch(
        {
            'cores': 128,
            'nodes': 4,
            'cores_per_node': 32,
            'gpus': 0,
            'mpi_allowed': True,
        }
    )

    assert plan.backend == 'allocation'
    assert not plan.local_fallback
    assert plan.fallback_reason is None
    assert plan.scheduler_node == 0
    assert plan.worker_count == 128
    assert plan.total_cores == 128
    assert [group.node_index for group in plan.worker_groups] == [
        0,
        1,
        2,
        3,
    ]
    assert [group.workers for group in plan.worker_groups] == [
        32,
        32,
        32,
        32,
    ]


def test_plan_dask_launch_distributes_partial_node_cpu_allocation():
    plan = plan_dask_launch(
        {
            'cores': 20,
            'nodes': 3,
            'cores_per_node': 8,
            'gpus': 0,
            'mpi_allowed': True,
        }
    )

    assert plan.backend == 'allocation'
    assert plan.worker_count == 20
    assert [group.workers for group in plan.worker_groups] == [8, 8, 4]


def test_plan_dask_launch_gpu_node_metadata():
    plan = plan_dask_launch(
        {
            'cores': 64,
            'nodes': 2,
            'cores_per_node': 32,
            'gpus': 8,
            'gpus_per_node': 4,
            'mpi_allowed': True,
        }
    )

    assert plan.backend == 'allocation'
    assert plan.worker_count == 64
    assert plan.total_gpus == 8
    assert [group.gpus for group in plan.worker_groups] == [4, 4]


def test_plan_dask_launch_falls_back_without_mpi():
    plan = plan_dask_launch(
        {
            'cores': 64,
            'nodes': 2,
            'cores_per_node': 32,
            'gpus': 0,
            'mpi_allowed': False,
        }
    )

    assert plan.backend == 'local'
    assert plan.local_fallback
    assert plan.fallback_reason == 'mpi_unavailable'
    assert plan.worker_count == 32


def test_parallel_system_process_launcher_builds_worker_command():
    captured = {}

    class FakeParallelSystem:
        def get_parallel_command(
            self, args, ntasks, cpus_per_task, gpus_per_task
        ):
            captured['args'] = args
            captured['ntasks'] = ntasks
            captured['cpus_per_task'] = cpus_per_task
            captured['gpus_per_task'] = gpus_per_task
            return ['srun', '-n', str(ntasks), *args]

    class FakeLauncher(ParallelSystemDaskProcessLauncher):
        @staticmethod
        def _launch(command, label, logger=None):
            captured['command'] = command
            captured['label'] = label
            return 'process'

    launch_plan = plan_dask_launch(
        {
            'cores': 64,
            'nodes': 2,
            'cores_per_node': 32,
            'gpus': 0,
            'mpi_allowed': True,
        }
    )
    launcher = FakeLauncher(FakeParallelSystem())
    process = launcher.launch_workers(
        ['dask', 'worker', '--scheduler-file', 'scheduler.json'],
        launch_plan,
    )

    assert process == 'process'
    assert captured['args'] == [
        'dask',
        'worker',
        '--scheduler-file',
        'scheduler.json',
    ]
    assert captured['ntasks'] == 64
    assert captured['cpus_per_task'] == 1
    assert captured['gpus_per_task'] == 0
    assert captured['command'] == [
        'srun',
        '-n',
        '64',
        'dask',
        'worker',
        '--scheduler-file',
        'scheduler.json',
    ]
    assert captured['label'] == 'workers'


def test_allocation_dask_client_context_lifecycle(tmp_path, monkeypatch):
    events: list[tuple[object, ...]] = []
    launched_commands = {}

    class FakeProcessLauncher:
        def launch_scheduler(self, command, logger=None):
            launched_commands['scheduler'] = command
            scheduler_file = command[command.index('--scheduler-file') + 1]
            (tmp_path / scheduler_file).write_text('{"address": "tcp://x"}')
            return FakeProcess('scheduler', events)

        def launch_workers(self, command, launch_plan, logger=None):
            launched_commands['workers'] = command
            launched_commands['worker_count'] = launch_plan.worker_count
            return FakeProcess('workers', events)

    class RecordingClient(FakeClient):
        def __init__(self, scheduler_file):
            super().__init__(scheduler_file)
            events.append(('client', 'start', scheduler_file))

        def close(self):
            events.append(('client', 'close'))
            super().close()

    monkeypatch.chdir(tmp_path)
    logger = DummyLogger()
    with dask_client_context(
        {
            'cores': 64,
            'nodes': 2,
            'cores_per_node': 32,
            'gpus': 0,
            'mpi_allowed': True,
        },
        logger=logger,
        backend_name='allocation',
        process_launcher=FakeProcessLauncher(),
        client_class=RecordingClient,
    ) as client:
        runtime_info = get_dask_runtime_info(client)
        assert runtime_info.backend == 'allocation'
        assert runtime_info.workers == 64
        assert runtime_info.scheduler_address == 'tcp://x'
        assert runtime_info.scheduler_node == 0
        assert runtime_info.total_cores == 64
        assert runtime_info.worker_groups[0]['workers'] == 32

    assert launched_commands['scheduler'][:3] == [
        'dask',
        'scheduler',
        '--no-dashboard',
    ]
    assert launched_commands['workers'][:2] == ['dask', 'worker']
    assert launched_commands['worker_count'] == 64
    assert events == [
        ('scheduler', 'start'),
        ('workers', 'start'),
        ('client', 'start', launched_commands['scheduler'][-1]),
        ('client', 'close'),
        ('workers', 'terminate'),
        ('workers', 'wait', 10),
        ('scheduler', 'terminate'),
        ('scheduler', 'wait', 10),
    ]
    assert logger.messages == [
        'Starting Dask Distributed backend=allocation workers=64',
        'Stopped Dask Distributed',
    ]


def test_allocation_dask_client_context_cleans_up_after_failure(
    tmp_path, monkeypatch
):
    events: list[tuple[object, ...]] = []

    class FakeProcessLauncher:
        def launch_scheduler(self, command, logger=None):
            scheduler_file = command[command.index('--scheduler-file') + 1]
            (tmp_path / scheduler_file).write_text('{"address": "tcp://x"}')
            return FakeProcess('scheduler', events)

        def launch_workers(self, command, launch_plan, logger=None):
            return FakeProcess('workers', events)

    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError, match='boom'):
        with dask_client_context(
            {
                'cores': 64,
                'nodes': 2,
                'cores_per_node': 32,
                'gpus': 0,
                'mpi_allowed': True,
            },
            backend_name='allocation',
            process_launcher=FakeProcessLauncher(),
            client_class=FakeClient,
        ):
            raise RuntimeError('boom')

    assert events == [
        ('scheduler', 'start'),
        ('workers', 'start'),
        ('workers', 'terminate'),
        ('workers', 'wait', 10),
        ('scheduler', 'terminate'),
        ('scheduler', 'wait', 10),
    ]


def test_dask_client_context_closes_client_and_cluster(monkeypatch):
    events = []

    class FakeCluster:
        kwargs = None

        def __init__(self, **kwargs):
            FakeCluster.kwargs = kwargs
            events.append('cluster.start')

        def close(self):
            events.append('cluster.close')

    class FakeClient:
        def __init__(self, cluster):
            self.cluster = cluster
            events.append('client.start')

        def close(self):
            events.append('client.close')

    monkeypatch.setattr('polaris.run.dask.LocalCluster', FakeCluster)
    monkeypatch.setattr('polaris.run.dask.Client', FakeClient)

    logger = DummyLogger()
    with dask_client_context({'cores': 3}, logger=logger) as client:
        assert isinstance(client, FakeClient)
        runtime_info = get_dask_runtime_info(client)
        assert runtime_info.backend == 'local'
        assert runtime_info.workers == 3

    assert FakeCluster.kwargs == {
        'n_workers': 3,
        'threads_per_worker': 1,
        'processes': True,
        'dashboard_address': None,
    }
    assert events == [
        'cluster.start',
        'client.start',
        'client.close',
        'cluster.close',
    ]
    assert logger.messages == [
        'Starting Dask Distributed backend=local workers=3',
        'Stopped Dask Distributed',
    ]


def test_dask_client_context_cleans_up_after_failure(monkeypatch):
    events = []

    class FakeCluster:
        def __init__(self, **kwargs):
            events.append('cluster.start')

        def close(self):
            events.append('cluster.close')

    class FakeClient:
        def __init__(self, cluster):
            events.append('client.start')

        def close(self):
            events.append('client.close')

    monkeypatch.setattr('polaris.run.dask.LocalCluster', FakeCluster)
    monkeypatch.setattr('polaris.run.dask.Client', FakeClient)

    logger = DummyLogger()
    with pytest.raises(RuntimeError, match='boom'):
        with dask_client_context({'cores': 1}, logger=logger):
            raise RuntimeError('boom')

    assert events == [
        'cluster.start',
        'client.start',
        'client.close',
        'cluster.close',
    ]
    assert logger.messages == [
        'Starting Dask Distributed backend=local workers=1',
        'Stopped Dask Distributed',
    ]
