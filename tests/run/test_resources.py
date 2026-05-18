from types import SimpleNamespace

import pytest

from polaris.run.resources import (
    ResourcePool,
    StepResourceLease,
    StepResourceRequest,
    get_local_worker_count,
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
