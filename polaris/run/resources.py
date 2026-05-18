from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class StepResourceRequest:
    """
    Scheduler resource request for a step.

    Attributes
    ----------
    cores : int
        CPU cores to reserve for the step.

    min_cores : int
        Minimum CPU cores required by the step.

    nodes : int
        Nodes to reserve for the step.

    min_nodes : int
        Minimum nodes required by the step.

    gpus : int
        GPUs to reserve for the step.

    min_gpus : int
        Minimum GPUs required by the step.

    memory : int, optional
        Memory requested by the step in MB.
    """

    cores: int
    min_cores: int
    nodes: int
    min_nodes: int
    gpus: int
    min_gpus: int
    memory: Optional[int] = None


@dataclass(frozen=True)
class ResourceReservation:
    """
    Active scheduler reservation from a resource pool.

    Attributes
    ----------
    reservation_id : int
        Unique identifier assigned by the resource pool.

    step_name : str
        The step holding the reservation.

    cores : int
        CPU cores reserved for the step.

    nodes : int
        Nodes reserved for the step.

    gpus : int
        GPUs reserved for the step.

    memory : int, optional
        Memory requested by the step in MB.
    """

    reservation_id: int
    step_name: str
    cores: int
    nodes: int
    gpus: int
    memory: Optional[int] = None


@dataclass(frozen=True)
class StepResourceLease:
    """
    Lightweight resource assignment for a running step.

    Attributes
    ----------
    cores : int
        CPU cores assigned to the step.

    workers : int
        Dask workers assigned to the step.

    nodes : int, optional
        Nodes assigned to the step.

    gpus : int, optional
        GPUs assigned to the step.

    memory : int, optional
        Memory assigned to the step in MB.
    """

    cores: int
    workers: int
    nodes: Optional[int] = None
    gpus: Optional[int] = None
    memory: Optional[int] = None


class ResourcePool:
    """
    Reusable scheduler resource pool.

    The pool tracks logical reservations for nodes, CPU cores and GPUs. It
    does not pin processes or replace ``Step.constrain_resources()``; it
    validates and reserves the resources the scheduler intends to make
    available to a step.
    """

    def __init__(self, available_resources):
        """
        Create a resource pool from Polaris available-resource metadata.

        Parameters
        ----------
        available_resources : dict
            Available CPU, GPU and MPI resources for this run.
        """
        self.total_cores = _resource_count(
            available_resources.get('cores'), default=1
        )
        self.total_nodes = _resource_count(
            available_resources.get('nodes'), default=1
        )
        self.total_gpus = _resource_count(
            available_resources.get('gpus'), default=0
        )

        self.free_cores = self.total_cores
        self.free_nodes = self.total_nodes
        self.free_gpus = self.total_gpus

        self._reservations: dict[int, ResourceReservation] = {}
        self._next_reservation_id = 1

    def reserve_step(self, step, request: StepResourceRequest):
        """
        Reserve resources for a step.

        Parameters
        ----------
        step : polaris.Step
            The step requesting resources.

        request : StepResourceRequest
            The resources requested by the scheduler.

        Returns
        -------
        reservation : ResourceReservation
            The active reservation.

        Raises
        ------
        ValueError
            If the request exceeds currently free resources.
        """
        self._validate_available(step.name, request)

        reservation = ResourceReservation(
            reservation_id=self._next_reservation_id,
            step_name=step.name,
            cores=request.cores,
            nodes=request.nodes,
            gpus=request.gpus,
            memory=request.memory,
        )
        self._next_reservation_id += 1

        self.free_cores -= reservation.cores
        self.free_nodes -= reservation.nodes
        self.free_gpus -= reservation.gpus
        self._reservations[reservation.reservation_id] = reservation
        return reservation

    def release(self, reservation: ResourceReservation):
        """
        Release an active reservation.

        Parameters
        ----------
        reservation : ResourceReservation
            The reservation to release.

        Raises
        ------
        ValueError
            If the reservation is not active in this pool.
        """
        active = self._reservations.get(reservation.reservation_id)
        if active != reservation:
            raise ValueError(
                f'Reservation {reservation.reservation_id} for step '
                f'{reservation.step_name} is not active in this resource pool.'
            )

        self._reservations.pop(reservation.reservation_id)
        self.free_cores += reservation.cores
        self.free_nodes += reservation.nodes
        self.free_gpus += reservation.gpus

    def _validate_available(self, step_name, request):
        if request.cores > self.free_cores:
            raise ValueError(
                f'Step {step_name} requests {request.cores} CPU cores but '
                f'only {self.free_cores} are free.'
            )
        if request.nodes > self.free_nodes:
            raise ValueError(
                f'Step {step_name} requests {request.nodes} nodes but only '
                f'{self.free_nodes} are free.'
            )
        if request.gpus > self.free_gpus:
            raise ValueError(
                f'Step {step_name} requests {request.gpus} GPUs but only '
                f'{self.free_gpus} are free.'
            )


def get_local_worker_count(available_resources):
    """
    Determine the number of local single-threaded workers for this run.

    Parameters
    ----------
    available_resources : dict
        Available CPU, GPU and MPI resources for this run.

    Returns
    -------
    worker_count : int
        The number of local workers available to the run.
    """
    cores = available_resources.get('cores', 1)
    if cores is None:
        cores = 1
    cores_per_node = available_resources.get('cores_per_node', None)
    if cores_per_node is not None:
        cores = min(cores, cores_per_node)
    return max(1, int(cores))


def get_step_resource_request(step, available_resources):
    """
    Build a scheduler resource request for a step.

    This mirrors the feasibility logic in ``Step.constrain_resources()``
    without mutating step resource attributes.

    Parameters
    ----------
    step : polaris.Step
        The step being scheduled.

    available_resources : dict
        Available CPU, GPU and MPI resources for this run.

    Returns
    -------
    request : StepResourceRequest
        The scheduler resource request for the step.

    Raises
    ------
    ValueError
        If the step cannot meet its minimum CPU, GPU or MPI requirements.
    """
    mpi_allowed = available_resources.get('mpi_allowed', True)
    ntasks = _step_count(step.ntasks, default=1)
    if not mpi_allowed and ntasks > 1:
        raise ValueError(
            f'Step {step.name} requests {ntasks} MPI tasks but MPI is not '
            'available.'
        )

    available_cores = _resource_count(
        available_resources.get('cores'), default=1
    )
    cores_per_node = _resource_count(
        available_resources.get('cores_per_node'), default=available_cores
    )
    nodes = _resource_count(available_resources.get('nodes'), default=1)

    cpus_per_task = _step_count(step.cpus_per_task, default=1)
    min_cpus_per_task = _step_count(step.min_cpus_per_task, default=1)
    min_tasks = _step_count(step.min_tasks, default=1)

    cpus_per_task = min(cpus_per_task, min(available_cores, cores_per_node))
    if cpus_per_task < min_cpus_per_task:
        raise ValueError(
            f'Available cpus_per_task ({cpus_per_task}) is below the minimum '
            f'of {min_cpus_per_task} for step {step.name}'
        )

    schedulable_tasks = available_cores // cpus_per_task
    ntasks = min(ntasks, schedulable_tasks)
    if ntasks < min_tasks:
        raise ValueError(
            f'Available number of MPI tasks ({ntasks}) is below the minimum '
            f'of {min_tasks} for step {step.name}'
        )

    gpus = _get_schedulable_gpus(step, available_resources, ntasks, min_tasks)
    cores = max(1, ntasks * cpus_per_task)
    min_cores = max(1, min_tasks * min_cpus_per_task)
    reserve_nodes = _estimate_nodes(cores, cores_per_node, nodes)
    min_nodes = _estimate_nodes(min_cores, cores_per_node, nodes)

    return StepResourceRequest(
        cores=cores,
        min_cores=min_cores,
        nodes=reserve_nodes,
        min_nodes=min_nodes,
        gpus=gpus,
        min_gpus=_get_min_gpus(step, min_tasks),
        memory=step.max_memory,
    )


def get_step_resource_lease(step, available_resources):
    """
    Build a lightweight resource lease for a step.

    Parameters
    ----------
    step : polaris.Step
        The step being run.

    available_resources : dict
        Available CPU, GPU and MPI resources for this run.

    Returns
    -------
    resources : StepResourceLease
        The resource assignment for the step.
    """
    ntasks = step.ntasks if step.ntasks is not None else 1
    cpus_per_task = step.cpus_per_task if step.cpus_per_task is not None else 1
    cores = max(1, int(ntasks) * int(cpus_per_task))
    local_workers = get_local_worker_count(available_resources)
    dask_workers = step.dask_workers if step.dask_workers is not None else 1
    min_dask_workers = (
        step.min_dask_workers if step.min_dask_workers is not None else 1
    )
    workers = max(1, min(int(dask_workers), local_workers))
    if workers < min_dask_workers:
        raise ValueError(
            f'Available Dask workers ({workers}) is below the minimum of '
            f'{min_dask_workers} for step {step.name}'
        )

    nodes = available_resources.get('nodes', None)
    gpus_per_task = getattr(step, 'gpus_per_task', 0)
    gpus = None
    if gpus_per_task is not None and gpus_per_task > 0:
        gpus = int(ntasks) * int(gpus_per_task)

    return StepResourceLease(
        cores=cores,
        workers=workers,
        nodes=nodes,
        gpus=gpus,
        memory=step.max_memory,
    )


def _get_schedulable_gpus(step, available_resources, ntasks, min_tasks):
    gpus_per_task = _step_count(step.gpus_per_task, default=0)
    min_gpus_per_task = _step_count(step.min_gpus_per_task, default=0)
    if gpus_per_task <= 0:
        return 0

    available_gpus = _resource_count(
        available_resources.get('gpus'), default=0
    )
    if available_gpus == 0:
        raise ValueError(
            f'Step {step.name} requests {gpus_per_task} GPUs per task but no '
            'GPUs are available on this machine.'
        )

    if gpus_per_task < min_gpus_per_task:
        raise ValueError(
            f'Available gpus_per_task ({gpus_per_task}) is below the minimum '
            f'of {min_gpus_per_task} for step {step.name}'
        )

    gpu_tasks = available_gpus // gpus_per_task
    ntasks = min(ntasks, gpu_tasks)
    if ntasks < min_tasks:
        raise ValueError(
            f'Available number of MPI tasks ({ntasks}) is below the minimum '
            f'of {min_tasks} for step {step.name} after GPU constraints are '
            'applied'
        )

    return ntasks * gpus_per_task


def _get_min_gpus(step, min_tasks):
    min_gpus_per_task = _step_count(step.min_gpus_per_task, default=0)
    return min_tasks * min_gpus_per_task


def _estimate_nodes(cores, cores_per_node, total_nodes):
    nodes = (cores + cores_per_node - 1) // cores_per_node
    return max(1, min(nodes, total_nodes))


def _resource_count(value, default):
    if value is None:
        value = default
    return max(0, int(value))


def _step_count(value, default):
    if value is None:
        value = default
    return int(value)
