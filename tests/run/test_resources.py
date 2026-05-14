from types import SimpleNamespace

import pytest

from polaris.run.resources import (
    StepResourceLease,
    get_local_worker_count,
    get_step_resource_lease,
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
