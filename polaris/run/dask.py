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


@dataclass(frozen=True)
class DaskWorkerGroup:
    """
    Planned Dask workers on one allocation node.

    Attributes
    ----------
    node_index : int
        Zero-based logical node index in the allocation.

    workers : int
        Number of Dask worker processes to launch on this node.

    threads_per_worker : int
        Number of threads per Dask worker.

    cores_per_worker : int
        Number of CPU cores represented by each worker.

    gpus : int
        Number of GPUs available on this node.
    """

    node_index: int
    workers: int
    threads_per_worker: int = 1
    cores_per_worker: int = 1
    gpus: int = 0


@dataclass(frozen=True)
class DaskLaunchPlan:
    """
    Allocation-scoped Dask launch plan.

    Attributes
    ----------
    backend : str
        Backend expected to consume this plan.

    scheduler_node : int
        Logical node index for the Dask scheduler.

    worker_groups : tuple of DaskWorkerGroup
        Planned worker placement by node.

    local_fallback : bool
        Whether this plan should use the local backend instead of an
        allocation-scoped launch.

    total_cores : int
        Total CPU cores represented by the plan.

    total_gpus : int
        Total GPUs represented by the plan.
    """

    backend: str
    scheduler_node: int
    worker_groups: tuple[DaskWorkerGroup, ...]
    local_fallback: bool
    total_cores: int
    total_gpus: int

    @property
    def worker_count(self):
        """
        Total Dask worker processes in this plan.
        """
        return sum(group.workers for group in self.worker_groups)


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


def plan_dask_launch(available_resources):
    """
    Plan an allocation-scoped Dask scheduler and worker layout.

    This function only validates and describes the intended launch. The
    current ``polaris run`` lifecycle still uses the local runtime backend.

    Parameters
    ----------
    available_resources : dict
        Available CPU, GPU and MPI resources for this run.

    Returns
    -------
    launch_plan : DaskLaunchPlan
        Planned scheduler placement and worker groups.
    """
    nodes = _resource_count(available_resources.get('nodes'), default=1)
    mpi_allowed = available_resources.get('mpi_allowed', True)
    local_fallback = nodes <= 1 or not mpi_allowed

    total_cores = _get_total_cores(available_resources, nodes)
    cores_per_node = _get_cores_per_node(available_resources, total_cores)

    total_gpus = _resource_count(available_resources.get('gpus'), default=0)
    gpus_per_node = available_resources.get('gpus_per_node')
    if gpus_per_node is not None:
        gpus_per_node = _resource_count(gpus_per_node, default=0)

    if local_fallback:
        worker_groups = (
            DaskWorkerGroup(
                node_index=0,
                workers=get_local_worker_count(available_resources),
                gpus=_get_node_gpus(
                    node_index=0,
                    total_gpus=total_gpus,
                    gpus_per_node=gpus_per_node,
                ),
            ),
        )
        backend = 'local'
        total_cores = worker_groups[0].workers
    else:
        worker_groups = _plan_worker_groups(
            nodes=nodes,
            total_cores=total_cores,
            cores_per_node=cores_per_node,
            total_gpus=total_gpus,
            gpus_per_node=gpus_per_node,
        )
        backend = 'allocation'

    return DaskLaunchPlan(
        backend=backend,
        scheduler_node=0,
        worker_groups=worker_groups,
        local_fallback=local_fallback,
        total_cores=total_cores,
        total_gpus=total_gpus,
    )


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


def _plan_worker_groups(
    nodes,
    total_cores,
    cores_per_node,
    total_gpus,
    gpus_per_node,
):
    worker_groups = []
    remaining_cores = total_cores
    for node_index in range(nodes):
        workers = min(cores_per_node, remaining_cores)
        if workers <= 0:
            break
        worker_groups.append(
            DaskWorkerGroup(
                node_index=node_index,
                workers=workers,
                gpus=_get_node_gpus(
                    node_index=node_index,
                    total_gpus=total_gpus,
                    gpus_per_node=gpus_per_node,
                ),
            )
        )
        remaining_cores -= workers

    if len(worker_groups) == 0:
        worker_groups.append(DaskWorkerGroup(node_index=0, workers=1))

    return tuple(worker_groups)


def _get_total_cores(available_resources, nodes):
    cores = available_resources.get('cores')
    if cores is not None:
        return max(1, int(cores))

    cores_per_node = available_resources.get('cores_per_node')
    if cores_per_node is not None:
        return max(1, nodes * int(cores_per_node))

    return 1


def _get_cores_per_node(available_resources, total_cores):
    cores_per_node = available_resources.get('cores_per_node')
    if cores_per_node is not None:
        return max(1, int(cores_per_node))
    return max(1, total_cores)


def _get_node_gpus(node_index, total_gpus, gpus_per_node):
    if total_gpus <= 0:
        return 0
    if gpus_per_node is None:
        return total_gpus if node_index == 0 else 0
    assigned_before = node_index * gpus_per_node
    remaining = total_gpus - assigned_before
    return max(0, min(gpus_per_node, remaining))


def _resource_count(value, default):
    if value is None:
        value = default
    return max(0, int(value))
