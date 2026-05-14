from types import SimpleNamespace

import pytest

from polaris.step import Step


class RecordingStep(Step):
    def __init__(self, **kwargs):
        super().__init__(
            component=SimpleNamespace(name='ocean'), name='record', **kwargs
        )
        self.ran = False

    def run(self):
        self.ran = True


def test_step_execution_kind_defaults_to_non_mpi():
    step = RecordingStep()

    assert step.execution_kind == 'non_mpi'
    assert step.can_run_concurrently
    assert step.dask_workers == 1
    assert step.min_dask_workers == 1


def test_step_execution_kind_detects_mpi_resources():
    assert RecordingStep(ntasks=2).execution_kind == 'mpi'
    assert RecordingStep(min_tasks=2).execution_kind == 'mpi'


def test_step_execution_kind_detects_command_line_parallel_args():
    step = RecordingStep()
    step.args = [['./model']]

    assert step.execution_kind == 'mpi'
    assert not step.can_run_concurrently


def test_step_execution_kind_override():
    step = RecordingStep(ntasks=4, execution_kind='non_mpi')

    assert step.execution_kind == 'non_mpi'
    assert step.can_run_concurrently

    step.set_execution_kind('mpi')

    assert step.execution_kind == 'mpi'
    assert not step.can_run_concurrently

    step.set_execution_kind(None)

    assert step.execution_kind == 'mpi'


def test_step_execution_kind_rejects_invalid_override():
    with pytest.raises(ValueError, match='Invalid execution_kind'):
        RecordingStep(execution_kind='gpu')


def test_step_task_parallelism_opt_out():
    step = RecordingStep(task_parallelism_allowed=False)

    assert step.execution_kind == 'non_mpi'
    assert not step.can_run_concurrently


def test_step_set_dask_resources():
    step = RecordingStep(dask_workers=4, min_dask_workers=2)

    assert step.dask_workers == 4
    assert step.min_dask_workers == 2

    step.set_dask_resources(dask_workers=8)

    assert step.dask_workers == 8
    assert step.min_dask_workers == 2

    step.set_dask_resources(min_dask_workers=3)

    assert step.dask_workers == 8
    assert step.min_dask_workers == 3


def test_run_with_dask_falls_back_to_run():
    step = RecordingStep()

    step.run_with_dask(client='client', resources='resources')

    assert step.ran
