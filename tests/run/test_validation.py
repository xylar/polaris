import pytest

from polaris.run.validation import (
    read_schedule_events,
    summarize_schedule_events,
    validate_phase1_schedule_event_files,
    validate_phase1_schedule_events,
)


def test_read_schedule_events_reports_bad_json(tmp_path):
    event_filename = tmp_path / 'schedule_events.jsonl'
    event_filename.write_text('{"event": "graph_constructed"}\nnot json\n')

    with pytest.raises(ValueError, match='line 2'):
        read_schedule_events(event_filename)


def test_summarize_schedule_events(tmp_path):
    event_filename = tmp_path / 'schedule_events.jsonl'
    event_filename.write_text(
        '\n'.join(
            [
                '{"edges": 1, "event": "graph_constructed", "nodes": 2}',
                (
                    '{"backend": "local", "event": "dask_runtime", '
                    '"fallback_reason": "single_node_allocation", '
                    '"scheduler_address": "tcp://x", "state": "active", '
                    '"workers": 4}'
                ),
                (
                    '{"event": "ready_selection", "task": "ocean/task", '
                    '"step": "init", "wait_reason": "no_dependencies"}'
                ),
                (
                    '{"event": "resource_feasibility", "feasible": true, '
                    '"step": "init", "task": "ocean/task"}'
                ),
                (
                    '{"active_steps": 1, "event": "step_start", '
                    '"step": "init", "suite_active_steps": 1, '
                    '"task": "ocean/task"}'
                ),
                (
                    '{"active_steps": 0, "event": "resource_released", '
                    '"step": "init", "suite_active_steps": 0, '
                    '"task": "ocean/task"}'
                ),
                (
                    '{"active_steps": 0, "event": "step_finish", '
                    '"duration": 2.5, "step": "init", '
                    '"suite_active_steps": 0, '
                    '"task": "ocean/task"}'
                ),
                (
                    '{"active_steps": 0, "event": "step_failure", '
                    '"duration": 0.75, "step": "failed", '
                    '"suite_active_steps": 0, '
                    '"task": "ocean/task"}'
                ),
                (
                    '{"event": "step_skipped", "reason": "cached", '
                    '"step": "cached", "task": "ocean/task"}'
                ),
                (
                    '{"event": "step_skipped", '
                    '"reason": "blocked_dependency", "step": "blocked", '
                    '"task": "ocean/task"}'
                ),
            ]
        )
    )

    summary = summarize_schedule_events(event_filename)

    assert summary.graph_constructed
    assert summary.dask_runtime_used
    assert summary.dask_backend == 'local'
    assert summary.dask_workers == 4
    assert summary.dask_scheduler_address == 'tcp://x'
    assert summary.dask_fallback_reason == 'single_node_allocation'
    assert summary.scheduler_path_used
    assert summary.single_active_step
    assert summary.ready_steps == ('ocean/task/init',)
    assert summary.started_steps == ('ocean/task/init',)
    assert summary.finished_steps == ('ocean/task/init',)
    assert summary.failed_steps == ('ocean/task/failed',)
    assert summary.skipped_steps == ('ocean/task/cached',)
    assert summary.blocked_steps == ('ocean/task/blocked',)
    assert summary.event_count == 10
    assert summary.resource_decisions == 1
    assert summary.finished_step_runtime == 2.5
    assert summary.failed_step_runtime == 0.75
    assert summary.total_step_runtime == 3.25


def test_validate_phase1_schedule_events_rejects_missing_dask(tmp_path):
    event_filename = tmp_path / 'schedule_events.jsonl'
    event_filename.write_text(
        '\n'.join(
            [
                '{"event": "graph_constructed"}',
                '{"event": "ready_selection", "step": "init"}',
            ]
        )
    )

    with pytest.raises(ValueError, match='missing dask_runtime'):
        validate_phase1_schedule_events(
            event_filename, require_dask_runtime=True
        )


def test_validate_phase1_schedule_events_rejects_active_overlap(tmp_path):
    event_filename = tmp_path / 'schedule_events.jsonl'
    event_filename.write_text(
        '\n'.join(
            [
                '{"event": "graph_constructed"}',
                '{"event": "ready_selection", "step": "init"}',
                (
                    '{"active_steps": 1, "event": "step_start", '
                    '"suite_active_steps": 2, "step": "init"}'
                ),
            ]
        )
    )

    with pytest.raises(ValueError, match='single-step policy'):
        validate_phase1_schedule_events(event_filename)


def test_validate_phase1_schedule_event_files(tmp_path):
    event_filenames = []
    for index in range(2):
        event_filename = tmp_path / f'task_{index}.jsonl'
        event_filename.write_text(
            '\n'.join(
                [
                    '{"event": "graph_constructed"}',
                    '{"event": "ready_selection", "step": "init"}',
                    (
                        '{"active_steps": 1, "event": "step_start", '
                        '"suite_active_steps": 1, "step": "init"}'
                    ),
                    (
                        '{"active_steps": 0, "duration": 1.5, '
                        '"event": "step_finish", '
                        '"suite_active_steps": 0, "step": "init"}'
                    ),
                ]
            )
        )
        event_filenames.append(event_filename)

    summary = validate_phase1_schedule_event_files(event_filenames)

    assert summary.scheduler_path_used
    assert summary.single_active_step
    assert summary.max_suite_active_steps == 1
    assert summary.event_count == 8
    assert summary.finished_step_runtime == 3.0
    assert summary.failed_step_runtime == 0.0
    assert summary.total_step_runtime == 3.0


def test_validate_phase1_schedule_event_files_rejects_empty_list():
    with pytest.raises(ValueError, match='No scheduler event files'):
        validate_phase1_schedule_event_files([])
