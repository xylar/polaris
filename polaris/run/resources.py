from dataclasses import dataclass
from enum import Enum
from typing import Literal, Optional


class ExecutionKind(Enum):
    """Classification of a step's execution model for resource scheduling."""

    LOCAL = 'local'
    MPI = 'mpi'


@dataclass(frozen=True)
class NodeResources:
    """
    Resources available on a single allocation node.

    Attributes
    ----------
    node_index : int
        Zero-based index of the node in the allocation.

    total_cores : int
        Total physical CPU cores on this node.

    total_gpus : int
        Total GPUs on this node.

    total_memory : int, optional
        Total memory on this node in MB.

    free_cores : int, optional
        Currently unreserved CPU cores on this node.

    free_gpus : int, optional
        Currently unreserved GPUs on this node.

    free_memory : int, optional
        Currently unreserved memory on this node in MB.
    """

    node_index: int
    total_cores: int
    total_gpus: int = 0
    total_memory: int | None = None
    free_cores: int | None = None
    free_gpus: int | None = None
    free_memory: int | None = None


@dataclass(frozen=True)
class StepPlacement:
    """
    Placement of a step within the allocation.

    Attributes
    ----------
    kind : {'local', 'mpi'}
        Execution model of the step. ``'local'`` steps run on a single node;
        ``'mpi'`` steps span the full allocation.

    node_indices : tuple of int
        Indices of the nodes reserved for this step.

    cores : int
        CPU cores reserved for the step.

    gpus : int
        GPUs reserved for the step.

    memory : int, optional
        Memory reserved for the step in MB.
    """

    kind: Literal['local', 'mpi']
    node_indices: tuple
    cores: int
    gpus: int = 0
    memory: int | None = None


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

    placement : StepPlacement, optional
        Node and core placement for the step. Populated by node-aware
        reservation methods; ``None`` when using aggregate-only reservation.
    """

    reservation_id: int
    step_name: str
    cores: int
    nodes: int
    gpus: int
    memory: Optional[int] = None
    placement: Optional[StepPlacement] = None


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


@dataclass(frozen=True)
class ResourceViews:
    """
    Resource views for task-parallel orchestration.

    Attributes
    ----------
    allocated : dict
        Full resources reported by the active machine allocation.

    control_plane : dict
        Resources reserved for the parent ``polaris run`` process and any
        scheduler processes it owns.

    data_plane : dict
        Resources available to Dask workers and Polaris steps after the
        control-plane reservation has been subtracted.
    """

    allocated: dict
    control_plane: dict
    data_plane: dict


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


def get_step_execution_kind(step) -> ExecutionKind:
    """
    Classify a step's execution model for resource scheduling.

    Uses ``step.execution_kind`` when available, which is the authoritative
    source on real ``polaris.Step`` objects. Falls back to inspecting
    ``ntasks``, ``min_tasks`` and ``args`` directly for duck-typed objects.

    Parameters
    ----------
    step : polaris.Step
        The step to classify.

    Returns
    -------
    kind : ExecutionKind
        ``ExecutionKind.MPI`` if the step uses MPI or a parallel command
        launcher; ``ExecutionKind.LOCAL`` otherwise.
    """
    declared = getattr(step, 'execution_kind', None)
    if declared == 'mpi':
        return ExecutionKind.MPI
    if declared == 'non_mpi':
        return ExecutionKind.LOCAL
    # Fallback: inspect raw attributes (mirrors _get_default_execution_kind).

    ntasks = getattr(step, 'ntasks', None)
    min_tasks = getattr(step, 'min_tasks', None)
    if (
        (ntasks is not None and int(ntasks) > 1)
        or (min_tasks is not None and int(min_tasks) > 1)
        or getattr(step, 'args', None) is not None
    ):
        return ExecutionKind.MPI
    return ExecutionKind.LOCAL


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


def get_resource_views(
    available_resources,
    control_plane_cores=1,
    control_plane_gpus=0,
    control_plane_nodes=0,
):
    """
    Split allocation resources into control-plane and data-plane views.

    Parameters
    ----------
    available_resources : dict
        Full allocation resources from the active machine.

    control_plane_cores : int, optional
        CPU cores to reserve for the parent ``polaris run`` process and any
        scheduler processes.

    control_plane_gpus : int, optional
        GPUs to reserve for the control plane.

    control_plane_nodes : int, optional
        Whole nodes to reserve for the control plane. A value of zero uses
        core-level control-plane reservation.

    Returns
    -------
    views : ResourceViews
        Full allocation, control-plane reservation and data-plane resources.
    """
    allocated = dict(available_resources)
    total_cores = _resource_count(allocated.get('cores'), default=1)
    total_nodes = _resource_count(allocated.get('nodes'), default=1)
    cores_per_node = _resource_count(
        allocated.get('cores_per_node'), default=total_cores
    )
    total_gpus = _resource_count(allocated.get('gpus'), default=0)
    gpus_per_node = allocated.get('gpus_per_node')
    if gpus_per_node is not None:
        gpus_per_node = _resource_count(gpus_per_node, default=0)

    node_cores = _node_resource_counts(
        total=total_cores, nodes=total_nodes, per_node=cores_per_node
    )
    node_gpus = _node_resource_counts(
        total=total_gpus, nodes=total_nodes, per_node=gpus_per_node
    )

    reserved_nodes = min(
        _resource_count(control_plane_nodes, default=0),
        max(0, total_nodes - 1),
    )
    if reserved_nodes > 0:
        reserved_cores, reserved_gpus = _reserve_control_plane_nodes(
            reserved_nodes, node_cores, node_gpus
        )
    else:
        reserved_cores, reserved_gpus = _reserve_control_plane_cores(
            total_cores=total_cores,
            total_gpus=total_gpus,
            control_plane_cores=control_plane_cores,
            control_plane_gpus=control_plane_gpus,
            node_cores=node_cores,
            node_gpus=node_gpus,
        )

    data_cores = max(1, total_cores - reserved_cores)
    data_gpus = max(0, total_gpus - reserved_gpus)
    data_nodes = max(1, total_nodes - reserved_nodes)
    max_node_cores = max(node_cores) if len(node_cores) > 0 else data_cores
    data_cores_per_node = max(1, min(cores_per_node, max_node_cores))

    control_plane = dict(
        cores=reserved_cores,
        nodes=reserved_nodes,
        cores_per_node=cores_per_node,
        gpus=reserved_gpus,
        gpus_per_node=gpus_per_node,
        mpi_allowed=False,
    )

    data_plane = dict(allocated)
    data_plane.update(
        cores=data_cores,
        nodes=data_nodes,
        cores_per_node=data_cores_per_node,
        gpus=data_gpus,
        node_core_counts=tuple(node_cores),
        node_gpu_counts=tuple(node_gpus),
        control_plane_cores=reserved_cores,
        control_plane_gpus=reserved_gpus,
        control_plane_nodes=reserved_nodes,
    )

    return ResourceViews(
        allocated=allocated,
        control_plane=control_plane,
        data_plane=data_plane,
    )


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


def _reserve_control_plane_nodes(reserved_nodes, node_cores, node_gpus):
    reserved_cores = 0
    reserved_gpus = 0
    for node_index in range(reserved_nodes):
        reserved_cores += node_cores[node_index]
        reserved_gpus += node_gpus[node_index]
        node_cores[node_index] = 0
        node_gpus[node_index] = 0
    return reserved_cores, reserved_gpus


def _reserve_control_plane_cores(
    total_cores,
    total_gpus,
    control_plane_cores,
    control_plane_gpus,
    node_cores,
    node_gpus,
):
    reserved_cores = min(
        _resource_count(control_plane_cores, default=0),
        max(0, total_cores - 1),
    )
    reserved_gpus = min(
        _resource_count(control_plane_gpus, default=0), total_gpus
    )
    node_cores[0] = max(0, node_cores[0] - reserved_cores)
    node_gpus[0] = max(0, node_gpus[0] - reserved_gpus)
    return reserved_cores, reserved_gpus


def _node_resource_counts(total, nodes, per_node):
    if nodes <= 0:
        return []
    if per_node is None or per_node <= 0:
        per_node = total
    counts = []
    remaining = total
    for _ in range(nodes):
        count = min(per_node, remaining)
        counts.append(max(0, count))
        remaining -= count
    return counts


def _resource_count(value, default):
    if value is None:
        value = default
    return max(0, int(value))


def _step_count(value, default):
    if value is None:
        value = default
    return int(value)
