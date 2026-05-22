from contextlib import contextmanager
from dataclasses import dataclass

from distributed import Client, LocalCluster

from polaris.run.resources import get_local_worker_count


@dataclass(frozen=True)
class DaskRuntimeInfo:
    """
    Metadata describing the active Dask runtime backend.

    Attributes
    ----------
    backend : str
        The selected backend name.

    workers : int
        The number of Dask workers requested for this runtime.
    """

    backend: str
    workers: int


class LocalDaskRuntimeBackend:
    """
    Local ``distributed.LocalCluster`` backend for ``polaris run``.
    """

    name = 'local'

    def __init__(self, available_resources, logger=None):
        """
        Create the local Dask runtime backend.

        Parameters
        ----------
        available_resources : dict
            Available CPU, GPU and MPI resources for this run.

        logger : logging.Logger, optional
            Logger used for run-level lifecycle messages.
        """
        self.available_resources = available_resources
        self.logger = logger
        self.worker_count = get_dask_worker_count(available_resources)

    @property
    def runtime_info(self):
        """
        Return structured metadata for this runtime backend.
        """
        return DaskRuntimeInfo(backend=self.name, workers=self.worker_count)

    @contextmanager
    def client_context(self):
        """
        Start and clean up the Dask client for this backend.

        Yields
        ------
        client : distributed.Client
            The Dask client for the run.
        """
        if self.logger is not None:
            self.logger.info(
                f'Starting Dask Distributed backend={self.name} '
                f'workers={self.worker_count}'
            )

        cluster = LocalCluster(
            n_workers=self.worker_count,
            threads_per_worker=1,
            processes=True,
            dashboard_address=None,
        )
        client = None
        try:
            client = Client(cluster)
            _attach_runtime_info(client, self.runtime_info)
            yield client
        finally:
            if client is not None:
                client.close()
            cluster.close()
            if self.logger is not None:
                self.logger.info('Stopped Dask Distributed')


def get_dask_worker_count(available_resources):
    """
    Determine the number of local single-threaded Dask workers for this run.

    The current lifecycle uses ``distributed.LocalCluster``, so workers run on
    the orchestration node only. If the active parallel system reports a
    multi-node allocation, cap this local worker count to one node to avoid
    oversubscribing the launch node.

    Parameters
    ----------
    available_resources : dict
        Available CPU, GPU and MPI resources for this run.

    Returns
    -------
    worker_count : int
        The number of Dask workers to start.
    """
    return get_local_worker_count(available_resources)


def select_dask_runtime_backend(
    available_resources, logger=None, backend_name='local'
):
    """
    Select a Dask runtime backend for ``polaris run``.

    Parameters
    ----------
    available_resources : dict
        Available CPU, GPU and MPI resources for this run.

    logger : logging.Logger, optional
        Logger used for run-level lifecycle messages.

    backend_name : str, optional
        The backend name to select. Currently only ``local`` is supported.

    Returns
    -------
    backend : LocalDaskRuntimeBackend
        The selected runtime backend.
    """
    if backend_name != LocalDaskRuntimeBackend.name:
        raise ValueError(f'Unsupported Dask runtime backend: {backend_name}')
    return LocalDaskRuntimeBackend(available_resources, logger=logger)


def get_dask_runtime_info(client):
    """
    Get Dask runtime metadata attached to a client, if available.

    Parameters
    ----------
    client : distributed.Client or object
        The Dask client for the run.

    Returns
    -------
    runtime_info : DaskRuntimeInfo or None
        Metadata for the active Dask runtime, or ``None`` if the client did
        not come from a Polaris runtime backend.
    """
    return getattr(client, 'polaris_dask_runtime_info', None)


@contextmanager
def dask_client_context(
    available_resources, logger=None, backend_name='local'
):
    """
    Start and clean up a Dask Distributed client for ``polaris run``.

    Parameters
    ----------
    available_resources : dict
        Available CPU, GPU and MPI resources for this run.

    logger : logging.Logger, optional
        Logger used for run-level lifecycle messages.

    backend_name : str, optional
        The Dask runtime backend to use. Currently only ``local`` is
        supported.

    Yields
    ------
    client : distributed.Client
        The Dask client for the run.
    """
    backend = select_dask_runtime_backend(
        available_resources, logger=logger, backend_name=backend_name
    )
    with backend.client_context() as client:
        yield client


def _attach_runtime_info(client, runtime_info):
    try:
        client.polaris_dask_runtime_info = runtime_info
    except AttributeError:
        pass
