from contextlib import contextmanager

from distributed import Client, LocalCluster

from polaris.run.resources import get_local_worker_count


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


@contextmanager
def dask_client_context(available_resources, logger=None):
    """
    Start and clean up a local Dask Distributed client for ``polaris run``.

    Parameters
    ----------
    available_resources : dict
        Available CPU, GPU and MPI resources for this run.

    logger : logging.Logger, optional
        Logger used for run-level lifecycle messages.

    Yields
    ------
    client : distributed.Client
        The Dask client for the run.
    """
    worker_count = get_dask_worker_count(available_resources)
    if logger is not None:
        logger.info(f'Starting Dask Distributed with {worker_count} workers')

    cluster = LocalCluster(
        n_workers=worker_count,
        threads_per_worker=1,
        processes=True,
        dashboard_address=None,
    )
    client = None
    try:
        client = Client(cluster)
        yield client
    finally:
        if client is not None:
            client.close()
        cluster.close()
        if logger is not None:
            logger.info('Stopped Dask Distributed')
