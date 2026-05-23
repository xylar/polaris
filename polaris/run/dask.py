import json
import os
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from tempfile import TemporaryDirectory

from distributed import Client, LocalCluster

from polaris.run.resources import get_local_worker_count

EXISTING_DASK_SCHEDULER_ADDRESS = 'POLARIS_DASK_SCHEDULER_ADDRESS'


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

    scheduler_address : str, optional
        Scheduler address for allocation-scoped backends.

    scheduler_node : int, optional
        Logical allocation node where the scheduler was placed.

    worker_groups : tuple of dict
        Worker placement metadata by logical allocation node.

    total_cores : int, optional
        Total CPU cores represented by the runtime.

    total_gpus : int, optional
        Total GPUs represented by the runtime.

    local_fallback : bool
        Whether this backend is a local fallback from allocation planning.

    fallback_reason : str, optional
        Reason the local backend was selected as a fallback.
    """

    backend: str
    workers: int
    scheduler_address: str | None = None
    scheduler_node: int | None = None
    worker_groups: tuple[dict[str, int], ...] = ()
    total_cores: int | None = None
    total_gpus: int | None = None
    local_fallback: bool = False
    fallback_reason: str | None = None


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

    fallback_reason : str, optional
        Reason the plan falls back to the local backend.
    """

    backend: str
    scheduler_node: int
    worker_groups: tuple[DaskWorkerGroup, ...]
    local_fallback: bool
    total_cores: int
    total_gpus: int
    fallback_reason: str | None = None

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

    def __init__(self, available_resources, logger=None, fallback_reason=None):
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
        self.fallback_reason = fallback_reason

    @property
    def runtime_info(self):
        """
        Return structured metadata for this runtime backend.
        """
        return DaskRuntimeInfo(
            backend=self.name,
            workers=self.worker_count,
            scheduler_node=0,
            worker_groups=(
                dict(
                    node_index=0,
                    workers=self.worker_count,
                    threads_per_worker=1,
                    cores_per_worker=1,
                    gpus=0,
                ),
            ),
            total_cores=self.worker_count,
            total_gpus=0,
            local_fallback=self.fallback_reason is not None,
            fallback_reason=self.fallback_reason,
        )

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


class AllocationDaskRuntimeBackend:
    """
    Allocation-scoped Dask backend for ``polaris run``.
    """

    name = 'allocation'

    def __init__(
        self,
        available_resources,
        logger=None,
        process_launcher=None,
        client_class=None,
    ):
        """
        Create an allocation-scoped Dask runtime backend.

        Parameters
        ----------
        available_resources : dict
            Available CPU, GPU and MPI resources for this run.

        logger : logging.Logger, optional
            Logger used for run-level lifecycle messages.

        process_launcher : DaskProcessLauncher, optional
            Launcher used to start scheduler and worker processes.

        client_class : class, optional
            Dask client class. Defaults to ``distributed.Client``.
        """
        self.available_resources = available_resources
        self.logger = logger
        self.launch_plan = plan_dask_launch(available_resources)
        self.process_launcher = process_launcher
        if self.process_launcher is None:
            self.process_launcher = _get_default_process_launcher(
                available_resources
            )
        if self.process_launcher is None:
            raise ValueError(
                'An allocation-scoped Dask backend requires a process '
                'launcher.'
            )
        self.client_class = Client if client_class is None else client_class

    @property
    def runtime_info(self):
        """
        Return structured metadata for this runtime backend.
        """
        return _runtime_info_from_launch_plan(
            backend=self.name, launch_plan=self.launch_plan
        )

    @contextmanager
    def client_context(self):
        """
        Start and clean up an allocation-scoped Dask client.

        Yields
        ------
        client : distributed.Client
            The Dask client for the run.
        """
        if self.logger is not None:
            self.logger.info(
                f'Starting Dask Distributed backend={self.name} '
                f'workers={self.launch_plan.worker_count}'
            )

        client = None
        processes = []
        dask_log_filename = os.path.abspath('dask_runtime.log')
        if self.logger is not None:
            self.logger.info(f'Writing Dask runtime log: {dask_log_filename}')
        with (
            TemporaryDirectory(
                prefix='.polaris-dask-', dir=os.getcwd()
            ) as runtime_dir,
            open(dask_log_filename, 'w') as dask_log,
        ):
            scheduler_file = os.path.join(runtime_dir, 'scheduler.json')
            dask_log.write(
                f'Dask Distributed backend={self.name} '
                f'workers={self.launch_plan.worker_count}\n'
            )
            self.process_launcher.output_file = dask_log
            try:
                scheduler_process = self._launch_scheduler(scheduler_file)
                processes.append(scheduler_process)
                _wait_for_scheduler_file(
                    scheduler_file=scheduler_file,
                    scheduler_process=scheduler_process,
                )
                worker_process = self._launch_workers(scheduler_file)
                processes.append(worker_process)
                client = self.client_class(scheduler_file=scheduler_file)
                scheduler_address = _read_scheduler_address(scheduler_file)
                _attach_runtime_info(
                    client,
                    _runtime_info_from_launch_plan(
                        backend=self.name,
                        launch_plan=self.launch_plan,
                        scheduler_address=scheduler_address,
                    ),
                )
                yield client
            finally:
                if client is not None:
                    client.close()
                _stop_processes(processes)
                self.process_launcher.output_file = None
                if self.logger is not None:
                    self.logger.info('Stopped Dask Distributed')

    def _launch_scheduler(self, scheduler_file):
        command = [
            'dask',
            'scheduler',
            '--no-dashboard',
            '--port',
            '0',
            '--scheduler-file',
            scheduler_file,
        ]
        return self.process_launcher.launch_scheduler(command, self.logger)

    def _launch_workers(self, scheduler_file):
        command = [
            'dask',
            'worker',
            '--scheduler-file',
            scheduler_file,
            '--nthreads',
            '1',
            '--no-dashboard',
        ]
        return self.process_launcher.launch_workers(
            command, self.launch_plan, self.logger
        )


class SubprocessDaskProcessLauncher:
    """
    Launch Dask scheduler and worker processes with ``subprocess.Popen``.
    """

    def __init__(self):
        """
        Create a launcher.
        """
        self.output_file = None

    def launch_scheduler(self, command, logger=None):
        """
        Launch the Dask scheduler command.
        """
        return self._launch(command, label='scheduler', logger=logger)

    def launch_workers(self, command, launch_plan, logger=None):
        """
        Launch Dask workers with one local worker process per planned worker.
        """
        command = command + [
            '--nworkers',
            str(launch_plan.worker_count),
            '--name',
            'polaris-worker',
        ]
        return self._launch(command, label='workers', logger=logger)

    def _launch(self, command, label, logger=None):
        if logger is not None:
            logger.info(f'Starting Dask {label}: {" ".join(command)}')
        output_file = getattr(self, 'output_file', None)
        if output_file is None:
            return subprocess.Popen(command)
        return subprocess.Popen(
            command, stdout=output_file, stderr=subprocess.STDOUT
        )


class ParallelSystemDaskProcessLauncher(SubprocessDaskProcessLauncher):
    """
    Launch Dask workers through a ``mache`` parallel system.
    """

    def __init__(self, parallel_system):
        """
        Create a launcher from a ``mache`` parallel system.

        Parameters
        ----------
        parallel_system : mache.parallel.ParallelSystem
            The active Polaris parallel system.
        """
        super().__init__()
        self.parallel_system = parallel_system

    def launch_workers(self, command, launch_plan, logger=None):
        """
        Launch one Dask worker process per planned worker.
        """
        command = self.parallel_system.get_parallel_command(
            args=command,
            ntasks=launch_plan.worker_count,
            cpus_per_task=1,
            gpus_per_task=0,
        )
        return self._launch(command, label='workers', logger=logger)


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
    available_resources,
    logger=None,
    backend_name='auto',
    process_launcher=None,
    client_class=None,
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
        The backend name to select. ``auto`` selects an allocation-scoped
        backend when the active resources and launcher support it, otherwise
        it falls back to ``local``.

    process_launcher : DaskProcessLauncher, optional
        Launcher used for the allocation backend.

    client_class : class, optional
        Dask client class used for the allocation backend.

    Returns
    -------
    backend : LocalDaskRuntimeBackend or AllocationDaskRuntimeBackend
        The selected runtime backend.
    """
    launch_plan = plan_dask_launch(available_resources)
    allocation_supported = not launch_plan.local_fallback and (
        process_launcher is not None
        or available_resources.get('parallel_system') is not None
    )

    if backend_name == 'auto':
        if allocation_supported:
            return AllocationDaskRuntimeBackend(
                available_resources,
                logger=logger,
                process_launcher=process_launcher,
                client_class=client_class,
            )
        fallback_reason = launch_plan.fallback_reason
        if fallback_reason is None:
            fallback_reason = 'missing_process_launcher'
        return LocalDaskRuntimeBackend(
            available_resources,
            logger=logger,
            fallback_reason=fallback_reason,
        )

    if backend_name == LocalDaskRuntimeBackend.name:
        return LocalDaskRuntimeBackend(available_resources, logger=logger)

    if backend_name == AllocationDaskRuntimeBackend.name:
        return AllocationDaskRuntimeBackend(
            available_resources,
            logger=logger,
            process_launcher=process_launcher,
            client_class=client_class,
        )

    else:
        raise ValueError(f'Unsupported Dask runtime backend: {backend_name}')


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
    fallback_reason = _get_launch_fallback_reason(nodes, mpi_allowed)
    local_fallback = fallback_reason is not None

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
        fallback_reason=fallback_reason,
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


def get_dask_scheduler_address(client):
    """
    Get the scheduler address for an active Dask client.

    Parameters
    ----------
    client : distributed.Client or object
        The Dask client for the run.

    Returns
    -------
    scheduler_address : str or None
        The scheduler address, if one can be determined.
    """
    runtime_info = get_dask_runtime_info(client)
    if runtime_info is not None and runtime_info.scheduler_address is not None:
        return runtime_info.scheduler_address

    scheduler = getattr(client, 'scheduler', None)
    return getattr(scheduler, 'address', None)


@contextmanager
def dask_client_context(
    available_resources,
    logger=None,
    backend_name='auto',
    process_launcher=None,
    client_class=None,
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
        The Dask runtime backend to use.

    process_launcher : DaskProcessLauncher, optional
        Launcher used for the allocation backend.

    client_class : class, optional
        Dask client class used for the allocation backend.

    Yields
    ------
    client : distributed.Client
        The Dask client for the run.
    """
    backend = select_dask_runtime_backend(
        available_resources,
        logger=logger,
        backend_name=backend_name,
        process_launcher=process_launcher,
        client_class=client_class,
    )
    with backend.client_context() as client:
        yield client


@contextmanager
def existing_dask_client_context(scheduler_address):
    """
    Connect to an existing Dask scheduler without owning its lifecycle.

    Parameters
    ----------
    scheduler_address : str
        Address of the existing Dask scheduler.

    Yields
    ------
    client : distributed.Client
        A client connected to the existing scheduler.
    """
    client = Client(scheduler_address)
    try:
        _attach_runtime_info(
            client,
            DaskRuntimeInfo(
                backend='existing',
                workers=0,
                scheduler_address=scheduler_address,
            ),
        )
        yield client
    finally:
        client.close()


def _attach_runtime_info(client, runtime_info):
    try:
        client.polaris_dask_runtime_info = runtime_info
    except AttributeError:
        pass


def _runtime_info_from_launch_plan(
    backend, launch_plan, scheduler_address=None
):
    worker_groups = tuple(
        dict(
            node_index=group.node_index,
            workers=group.workers,
            threads_per_worker=group.threads_per_worker,
            cores_per_worker=group.cores_per_worker,
            gpus=group.gpus,
        )
        for group in launch_plan.worker_groups
    )
    return DaskRuntimeInfo(
        backend=backend,
        workers=launch_plan.worker_count,
        scheduler_address=scheduler_address,
        scheduler_node=launch_plan.scheduler_node,
        worker_groups=worker_groups,
        total_cores=launch_plan.total_cores,
        total_gpus=launch_plan.total_gpus,
        local_fallback=launch_plan.local_fallback,
        fallback_reason=launch_plan.fallback_reason,
    )


def _get_launch_fallback_reason(nodes, mpi_allowed):
    if nodes <= 1:
        return 'single_node_allocation'
    if not mpi_allowed:
        return 'mpi_unavailable'
    return None


def _read_scheduler_address(scheduler_file):
    try:
        with open(scheduler_file) as handle:
            metadata = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return metadata.get('address')


def _get_default_process_launcher(available_resources):
    parallel_system = available_resources.get('parallel_system')
    if parallel_system is None:
        return None
    return ParallelSystemDaskProcessLauncher(parallel_system)


def _wait_for_scheduler_file(
    scheduler_file,
    scheduler_process,
    timeout=30.0,
):
    start_time = time.time()
    while not os.path.exists(scheduler_file):
        if _process_returncode(scheduler_process) is not None:
            raise RuntimeError('Dask scheduler exited before it was ready.')
        if time.time() - start_time > timeout:
            raise TimeoutError(
                f'Dask scheduler did not write {scheduler_file} within '
                f'{timeout:g} seconds.'
            )
        time.sleep(0.1)


def _stop_processes(processes):
    for process in reversed(processes):
        if _process_returncode(process) is not None:
            continue
        process.terminate()
        try:
            process.wait(timeout=10)
        except TypeError:
            process.wait()
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _process_returncode(process):
    poll = getattr(process, 'poll', None)
    if poll is None:
        return None
    return poll()


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
