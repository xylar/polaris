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
    resources_for_local_placement,
    resources_for_mpi_placement,
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


def test_get_resource_views_default_no_control_plane():
    # Phase 1 default: control_plane_cores=0, so data_plane == allocated.
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
    assert views.control_plane['cores'] == 0
    assert views.control_plane['nodes'] == 0
    assert views.data_plane['cores'] == 64
    assert views.data_plane['nodes'] == 2
    assert views.data_plane['cores_per_node'] == 32
    assert views.data_plane['node_core_counts'] == (32, 32)
    assert views.data_plane['control_plane_cores'] == 0


def test_get_resource_views_explicit_control_plane_core():
    # Future-phase opt-in: explicitly passing control_plane_cores=1 works.
    views = get_resource_views(
        {
            'cores': 64,
            'nodes': 2,
            'cores_per_node': 32,
            'gpus': 0,
            'mpi_allowed': True,
        },
        control_plane_cores=1,
    )

    assert views.allocated['cores'] == 64
    assert views.control_plane['cores'] == 1
    assert views.control_plane['nodes'] == 0
    assert views.data_plane['cores'] == 63
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
    # 2-node pool: 8 cores each, 2 GPUs each
    pool = ResourcePool(
        {'cores': 16, 'nodes': 2, 'gpus': 4, 'cores_per_node': 8}
    )
    step = SimpleNamespace(name='step', ntasks=1, min_tasks=1, args=None)
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
    assert reservation.placement is not None
    assert reservation.placement.kind == 'local'
    assert reservation.placement.node_indices == (0,)
    assert reservation.placement.cores == 6
    # Node 0 had 8 cores; 6 reserved → 2 free on node 0, 8 free on node 1
    assert pool.free_cores == 10
    assert pool.free_gpus == 2
    # Both nodes still have some free cores, so free_nodes == 2
    assert pool.free_nodes == 2
    assert pool.nodes[0].free_cores == 2
    assert pool.nodes[1].free_cores == 8

    pool.release(reservation)

    assert pool.free_cores == 16
    assert pool.free_gpus == 4
    assert pool.free_nodes == 2
    assert pool.nodes[0].free_cores == 8
    assert pool.nodes[1].free_cores == 8


def test_resource_pool_rejects_busy_resources():
    # Reserve 6 of 8 cores on the single node, leaving 2 free.
    # A subsequent request with min_cores=4 cannot be satisfied.
    pool = ResourcePool({'cores': 8, 'nodes': 1, 'gpus': 0})
    step = SimpleNamespace(name='step', ntasks=1, min_tasks=1, args=None)
    request = StepResourceRequest(
        cores=6,
        min_cores=2,
        nodes=1,
        min_nodes=1,
        gpus=0,
        min_gpus=0,
    )
    pool.reserve_step(step, request)

    big_request = StepResourceRequest(
        cores=6,
        min_cores=4,
        nodes=1,
        min_nodes=1,
        gpus=0,
        min_gpus=0,
    )
    with pytest.raises(ValueError, match='single node'):
        pool.reserve_step(step, big_request)


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


# --- ResourcePool per-node accounting (R3) ---


def test_resource_pool_local_two_reservations_share_node():
    # Two small local steps can both fit on the same node.
    pool = ResourcePool(
        {'cores': 16, 'nodes': 2, 'gpus': 0, 'cores_per_node': 8}
    )
    req = StepResourceRequest(
        cores=1, min_cores=1, nodes=1, min_nodes=1, gpus=0, min_gpus=0
    )

    r1 = pool.reserve_local_step('a', req)
    r2 = pool.reserve_local_step('b', req)

    assert r1.placement is not None
    assert r2.placement is not None
    assert r1.placement.node_indices == (0,)
    assert r2.placement.node_indices == (0,)
    assert pool.nodes[0].free_cores == 6
    assert pool.free_cores == 14

    pool.release(r1)
    pool.release(r2)

    assert pool.free_cores == 16
    assert pool.nodes[0].free_cores == 8


def test_resource_pool_local_rejects_when_min_exceeds_node():
    # min_cores > any single node → ValueError.
    pool = ResourcePool(
        {'cores': 16, 'nodes': 2, 'gpus': 0, 'cores_per_node': 8}
    )
    req = StepResourceRequest(
        cores=10, min_cores=10, nodes=1, min_nodes=1, gpus=0, min_gpus=0
    )
    with pytest.raises(ValueError, match='single node'):
        pool.reserve_local_step('step', req)


def test_resource_pool_local_caps_cores_to_node_capacity():
    # cores > node capacity but min_cores fits → reserved amount is capped.
    pool = ResourcePool(
        {'cores': 16, 'nodes': 2, 'gpus': 0, 'cores_per_node': 8}
    )
    req = StepResourceRequest(
        cores=12, min_cores=4, nodes=1, min_nodes=1, gpus=0, min_gpus=0
    )
    reservation = pool.reserve_local_step('step', req)

    assert reservation.placement is not None
    assert reservation.placement.cores == 8  # capped to node capacity
    assert pool.nodes[0].free_cores == 0
    assert pool.free_cores == 8  # node 1 untouched


def test_resource_pool_mpi_spans_all_nodes():
    # MPI reservation drains all nodes; release restores them.
    pool = ResourcePool(
        {'cores': 16, 'nodes': 2, 'gpus': 4, 'cores_per_node': 8}
    )
    req = StepResourceRequest(
        cores=16, min_cores=8, nodes=2, min_nodes=1, gpus=4, min_gpus=0
    )

    reservation = pool.reserve_mpi_step('mpi_step', req)

    assert reservation.placement is not None
    assert reservation.placement.kind == 'mpi'
    assert set(reservation.placement.node_indices) == {0, 1}
    assert reservation.placement.cores == 16
    assert pool.free_cores == 0
    assert pool.free_gpus == 0
    assert pool.free_nodes == 0

    pool.release(reservation)

    assert pool.free_cores == 16
    assert pool.free_gpus == 4
    assert pool.free_nodes == 2
    assert pool.nodes[0].free_cores == 8
    assert pool.nodes[1].free_cores == 8


def test_resource_pool_sequential_reserve_release_restores_state():
    # Two sequential local reserve+release pairs leave the pool unchanged.
    pool = ResourcePool(
        {'cores': 16, 'nodes': 2, 'gpus': 0, 'cores_per_node': 8}
    )
    req = StepResourceRequest(
        cores=4, min_cores=4, nodes=1, min_nodes=1, gpus=0, min_gpus=0
    )

    for _ in range(2):
        r = pool.reserve_local_step('step', req)
        pool.release(r)

    assert pool.free_cores == 16
    assert pool.free_nodes == 2


def test_resource_pool_reserve_step_dispatches_local():
    # reserve_step on a plain Python step → LOCAL placement.
    pool = ResourcePool(
        {'cores': 16, 'nodes': 2, 'gpus': 0, 'cores_per_node': 8}
    )
    step = SimpleNamespace(name='step', ntasks=1, min_tasks=1, args=None)
    req = StepResourceRequest(
        cores=4, min_cores=4, nodes=1, min_nodes=1, gpus=0, min_gpus=0
    )

    reservation = pool.reserve_step(step, req)
    assert reservation.placement.kind == 'local'


def test_resource_pool_reserve_step_dispatches_mpi():
    # reserve_step on an MPI step (ntasks > 1) → MPI placement.
    pool = ResourcePool(
        {'cores': 16, 'nodes': 2, 'gpus': 0, 'cores_per_node': 8}
    )
    step = SimpleNamespace(name='step', ntasks=4, min_tasks=1, args=None)
    req = StepResourceRequest(
        cores=4, min_cores=4, nodes=1, min_nodes=1, gpus=0, min_gpus=0
    )

    reservation = pool.reserve_step(step, req)
    assert reservation.placement.kind == 'mpi'


# --- resources_for_local_placement and resources_for_mpi_placement (R6) ---


def _three_node_allocation():
    """3-node, 64-core-each allocation used by placement view tests."""
    return {
        'cores': 192,
        'nodes': 3,
        'cores_per_node': 64,
        'node_core_counts': (64, 64, 64),
        'gpus': 0,
        'node_gpu_counts': (0, 0, 0),
        'gpus_per_node': 0,
        'mpi_allowed': True,
    }


def test_resources_for_local_placement_single_node_view():
    allocation = _three_node_allocation()
    placement = StepPlacement(kind='local', node_indices=(0,), cores=8, gpus=0)
    from polaris.run.resources import ResourceReservation

    reservation = ResourceReservation(
        reservation_id=1,
        step_name='step',
        cores=8,
        nodes=1,
        gpus=0,
        placement=placement,
    )

    resources = resources_for_local_placement(allocation, reservation)

    assert resources['nodes'] == 1
    assert resources['cores'] == 8
    assert resources['cores_per_node'] == 64  # full node capacity
    assert resources['node_core_counts'] == (8,)  # reserved amount only
    assert resources['gpus'] == 0


def test_resources_for_local_placement_no_control_plane_subtraction():
    # No cores should be subtracted for an orchestration reservation.
    allocation = _three_node_allocation()
    placement = StepPlacement(kind='local', node_indices=(1,), cores=32)
    from polaris.run.resources import ResourceReservation

    reservation = ResourceReservation(
        reservation_id=1,
        step_name='step',
        cores=32,
        nodes=1,
        gpus=0,
        placement=placement,
    )

    resources = resources_for_local_placement(allocation, reservation)

    # Cores come from reservation, not from allocation minus some overhead.
    assert resources['cores'] == 32
    assert resources['cores_per_node'] == 64


def test_resources_for_mpi_placement_full_allocation_view():
    allocation = _three_node_allocation()
    placement = StepPlacement(
        kind='mpi', node_indices=(0, 1, 2), cores=192, gpus=0
    )
    from polaris.run.resources import ResourceReservation

    reservation = ResourceReservation(
        reservation_id=1,
        step_name='step',
        cores=192,
        nodes=3,
        gpus=0,
        placement=placement,
    )

    resources = resources_for_mpi_placement(allocation, reservation)

    assert resources['cores'] == 192
    assert resources['nodes'] == 3
    assert resources['node_core_counts'] == (64, 64, 64)


def test_resources_for_mpi_placement_no_control_plane_subtraction():
    allocation = _three_node_allocation()
    placement = StepPlacement(kind='mpi', node_indices=(0, 1, 2), cores=192)
    from polaris.run.resources import ResourceReservation

    reservation = ResourceReservation(
        reservation_id=1,
        step_name='step',
        cores=192,
        nodes=3,
        gpus=0,
        placement=placement,
    )

    resources = resources_for_mpi_placement(allocation, reservation)

    # MPI gets full allocation; no core is subtracted.
    assert resources['cores'] == allocation['cores']


def test_resources_for_local_placement_rejects_mpi_reservation():
    allocation = _three_node_allocation()
    placement = StepPlacement(kind='mpi', node_indices=(0, 1, 2), cores=192)
    from polaris.run.resources import ResourceReservation

    reservation = ResourceReservation(
        reservation_id=1,
        step_name='step',
        cores=192,
        nodes=3,
        gpus=0,
        placement=placement,
    )

    with pytest.raises(ValueError, match="kind='mpi'"):
        resources_for_local_placement(allocation, reservation)


def test_resources_for_mpi_placement_rejects_local_reservation():
    allocation = _three_node_allocation()
    placement = StepPlacement(kind='local', node_indices=(0,), cores=8)
    from polaris.run.resources import ResourceReservation

    reservation = ResourceReservation(
        reservation_id=1,
        step_name='step',
        cores=8,
        nodes=1,
        gpus=0,
        placement=placement,
    )

    with pytest.raises(ValueError, match="kind='local'"):
        resources_for_mpi_placement(allocation, reservation)
