from types import SimpleNamespace

import pytest

from polaris.run.resources import (
    ExecutionKind,
    NodeResources,
    ResourcePool,
    StepPlacement,
    StepResourceLease,
    StepResourceRequest,
    get_local_worker_count,
    get_resource_views,
    get_step_execution_kind,
    get_step_resource_lease,
    get_step_resource_request,
)


def test_get_local_worker_count_caps_to_one_node():
    assert get_local_worker_count({}) == 1
    assert get_local_worker_count({'cores': None}) == 1
    assert get_local_worker_count({'cores': 0}) == 1
    assert get_local_worker_count({'cores': 4}) == 4
    assert (
        get_local_worker_count(
            {'cores': 128, 'nodes': 4, 'cores_per_node': 32}
        )
        == 32
    )


def test_get_resource_views_reserves_control_plane_core():
    views = get_resource_views(
        {
            'cores': 64,
            'nodes': 2,
            'cores_per_node': 32,
            'gpus': 0,
            'mpi_allowed': True,
        }
    )

    assert views.allocated['cores'] == 64
    assert views.control_plane['cores'] == 1
    assert views.control_plane['nodes'] == 0
    assert views.data_plane['cores'] == 63
    assert views.data_plane['nodes'] == 2
    assert views.data_plane['cores_per_node'] == 32
    assert views.data_plane['node_core_counts'] == (31, 32)
    assert views.data_plane['control_plane_cores'] == 1


def test_get_resource_views_can_reserve_whole_control_node():
    views = get_resource_views(
        {
            'cores': 96,
            'nodes': 3,
            'cores_per_node': 32,
            'gpus': 12,
            'gpus_per_node': 4,
            'mpi_allowed': True,
        },
        control_plane_nodes=1,
    )

    assert views.control_plane['cores'] == 32
    assert views.control_plane['nodes'] == 1
    assert views.control_plane['gpus'] == 4
    assert views.data_plane['cores'] == 64
    assert views.data_plane['nodes'] == 2
    assert views.data_plane['gpus'] == 8
    assert views.data_plane['node_core_counts'] == (0, 32, 32)
    assert views.data_plane['node_gpu_counts'] == (0, 4, 4)


def test_get_step_resource_lease():
    step = SimpleNamespace(
        name='step',
        ntasks=2,
        cpus_per_task=3,
        dask_workers=12,
        min_dask_workers=1,
        gpus_per_task=1,
        max_memory=1024,
    )

    resources = get_step_resource_lease(
        step,
        {
            'cores': 128,
            'nodes': 4,
            'cores_per_node': 32,
        },
    )

    assert resources == StepResourceLease(
        cores=6,
        workers=12,
        nodes=4,
        gpus=2,
        memory=1024,
    )


def test_get_step_resource_lease_rejects_too_few_dask_workers():
    step = SimpleNamespace(
        name='step',
        ntasks=1,
        cpus_per_task=1,
        dask_workers=12,
        min_dask_workers=8,
        gpus_per_task=0,
        max_memory=None,
    )

    with pytest.raises(ValueError, match='Available Dask workers'):
        get_step_resource_lease(
            step,
            {
                'cores': 4,
                'nodes': 1,
                'cores_per_node': 4,
            },
        )


def test_get_step_resource_request_caps_like_constrain_resources():
    step = SimpleNamespace(
        name='step',
        ntasks=8,
        min_tasks=2,
        cpus_per_task=4,
        min_cpus_per_task=2,
        gpus_per_task=0,
        min_gpus_per_task=0,
        max_memory=2048,
    )

    request = get_step_resource_request(
        step,
        {
            'cores': 16,
            'nodes': 2,
            'cores_per_node': 8,
            'gpus': 0,
            'mpi_allowed': True,
        },
    )

    assert request == StepResourceRequest(
        cores=16,
        min_cores=4,
        nodes=2,
        min_nodes=1,
        gpus=0,
        min_gpus=0,
        memory=2048,
    )


def test_resource_pool_reserve_and_release():
    pool = ResourcePool({'cores': 16, 'nodes': 2, 'gpus': 4})
    step = SimpleNamespace(name='step')
    request = StepResourceRequest(
        cores=6,
        min_cores=2,
        nodes=1,
        min_nodes=1,
        gpus=2,
        min_gpus=0,
    )

    reservation = pool.reserve_step(step, request)

    assert reservation.step_name == 'step'
    assert pool.free_cores == 10
    assert pool.free_nodes == 1
    assert pool.free_gpus == 2

    pool.release(reservation)

    assert pool.free_cores == 16
    assert pool.free_nodes == 2
    assert pool.free_gpus == 4


def test_resource_pool_rejects_busy_resources():
    pool = ResourcePool({'cores': 8, 'nodes': 1, 'gpus': 0})
    step = SimpleNamespace(name='step')
    request = StepResourceRequest(
        cores=6,
        min_cores=2,
        nodes=1,
        min_nodes=1,
        gpus=0,
        min_gpus=0,
    )
    pool.reserve_step(step, request)

    with pytest.raises(ValueError, match='only 2 are free'):
        pool.reserve_step(step, request)


def test_get_step_resource_request_rejects_min_cpu_failure():
    step = SimpleNamespace(
        name='step',
        ntasks=1,
        min_tasks=1,
        cpus_per_task=16,
        min_cpus_per_task=16,
        gpus_per_task=0,
        min_gpus_per_task=0,
        max_memory=None,
    )

    with pytest.raises(ValueError, match='cpus_per_task'):
        get_step_resource_request(
            step,
            {
                'cores': 8,
                'nodes': 1,
                'cores_per_node': 8,
                'gpus': 0,
                'mpi_allowed': True,
            },
        )


def test_get_step_resource_request_rejects_min_gpu_failure():
    step = SimpleNamespace(
        name='step',
        ntasks=2,
        min_tasks=2,
        cpus_per_task=1,
        min_cpus_per_task=1,
        gpus_per_task=1,
        min_gpus_per_task=1,
        max_memory=None,
    )

    with pytest.raises(ValueError, match='after GPU constraints'):
        get_step_resource_request(
            step,
            {
                'cores': 8,
                'nodes': 1,
                'cores_per_node': 8,
                'gpus': 1,
                'mpi_allowed': True,
            },
        )


# --- ExecutionKind and get_step_execution_kind ---


def test_get_step_execution_kind_mpi_ntasks():
    step = SimpleNamespace(ntasks=4, min_tasks=1, args=None)
    assert get_step_execution_kind(step) == ExecutionKind.MPI


def test_get_step_execution_kind_mpi_min_tasks():
    step = SimpleNamespace(ntasks=1, min_tasks=2, args=None)
    assert get_step_execution_kind(step) == ExecutionKind.MPI


def test_get_step_execution_kind_mpi_args():
    step = SimpleNamespace(ntasks=1, min_tasks=1, args=['--nprocs', '4'])
    assert get_step_execution_kind(step) == ExecutionKind.MPI


def test_get_step_execution_kind_local_plain():
    step = SimpleNamespace(ntasks=1, min_tasks=1, args=None)
    assert get_step_execution_kind(step) == ExecutionKind.LOCAL


def test_get_step_execution_kind_local_cpus_per_task_only():
    # cpus_per_task > 1 but ntasks == 1 and no args → LOCAL
    step = SimpleNamespace(ntasks=1, min_tasks=1, args=None, cpus_per_task=8)
    assert get_step_execution_kind(step) == ExecutionKind.LOCAL


def test_get_step_execution_kind_local_dask_only():
    # step with dask_workers but ntasks == 1 and no args → LOCAL
    step = SimpleNamespace(ntasks=1, min_tasks=1, args=None, dask_workers=8)
    assert get_step_execution_kind(step) == ExecutionKind.LOCAL


def test_get_step_execution_kind_explicit_mpi():
    step = SimpleNamespace(execution_kind='mpi')
    assert get_step_execution_kind(step) == ExecutionKind.MPI


def test_get_step_execution_kind_explicit_non_mpi():
    step = SimpleNamespace(execution_kind='non_mpi')
    assert get_step_execution_kind(step) == ExecutionKind.LOCAL


# --- NodeResources and StepPlacement smoke tests ---


def test_node_resources_construction():
    node = NodeResources(node_index=0, total_cores=64, total_gpus=4)
    assert node.node_index == 0
    assert node.total_cores == 64
    assert node.total_gpus == 4
    assert node.free_cores is None
    assert node.free_gpus is None


def test_node_resources_with_free_counts():
    node = NodeResources(
        node_index=1, total_cores=32, total_gpus=0, free_cores=16, free_gpus=0
    )
    assert node.free_cores == 16


def test_node_resources_is_frozen():
    import dataclasses

    node = NodeResources(node_index=0, total_cores=32)
    with pytest.raises(dataclasses.FrozenInstanceError):
        node.total_cores = 16  # type: ignore[misc]


def test_step_placement_construction():
    placement = StepPlacement(kind='local', node_indices=(0,), cores=8)
    assert placement.kind == 'local'
    assert placement.node_indices == (0,)
    assert placement.cores == 8
    assert placement.gpus == 0
    assert placement.memory is None


def test_step_placement_mpi():
    placement = StepPlacement(
        kind='mpi', node_indices=(0, 1, 2), cores=192, gpus=0
    )
    assert placement.kind == 'mpi'
    assert len(placement.node_indices) == 3


def test_step_placement_is_frozen():
    import dataclasses

    placement = StepPlacement(kind='local', node_indices=(0,), cores=4)
    with pytest.raises(dataclasses.FrozenInstanceError):
        placement.cores = 8  # type: ignore[misc]


def test_resource_reservation_accepts_placement():
    placement = StepPlacement(kind='local', node_indices=(0,), cores=4)
    from polaris.run.resources import ResourceReservation

    reservation = ResourceReservation(
        reservation_id=1,
        step_name='step',
        cores=4,
        nodes=1,
        gpus=0,
        placement=placement,
    )
    assert reservation.placement is placement
    assert reservation.placement.kind == 'local'


def test_resource_reservation_placement_defaults_none():
    from polaris.run.resources import ResourceReservation

    reservation = ResourceReservation(
        reservation_id=1,
        step_name='step',
        cores=4,
        nodes=1,
        gpus=0,
    )
    assert reservation.placement is None
