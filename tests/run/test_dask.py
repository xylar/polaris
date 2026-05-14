from polaris.run.dask import dask_client_context, get_dask_worker_count


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
        'Starting Dask Distributed with 3 workers',
        'Stopped Dask Distributed',
    ]
