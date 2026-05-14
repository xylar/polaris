from dataclasses import dataclass
from typing import Optional


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
