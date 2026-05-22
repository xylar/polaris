import pytest

from polaris.run.dask import (
    LocalDaskRuntimeBackend,
    dask_client_context,
    get_dask_runtime_info,
    get_dask_worker_count,
    select_dask_runtime_backend,
)


class DummyLogger:
    def __init__(self):
        self.messages = []

    def info(self, message):
        self.messages.append(message)


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


def test_select_dask_runtime_backend_rejects_unknown():
    with pytest.raises(ValueError, match='Unsupported Dask runtime backend'):
        select_dask_runtime_backend({}, backend_name='unknown')


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
