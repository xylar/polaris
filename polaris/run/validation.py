import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


@dataclass(frozen=True)
class ScheduleEventSummary:
    """
    Summary of one task's scheduler event file.

    Attributes
    ----------
    event_filename : str
        The event file that was summarized.

    graph_constructed : bool
        Whether the scheduler graph-construction event was recorded.

    dask_backend : str, optional
        The Dask backend recorded by the run, if present.

    dask_workers : int, optional
        The Dask worker count recorded by the run, if present.

    ready_steps : tuple of str
        Steps selected by the scheduler.

    started_steps : tuple of str
        Steps that started execution.

    finished_steps : tuple of str
        Steps that finished successfully.

    failed_steps : tuple of str
        Steps that failed.

    skipped_steps : tuple of str
        Steps skipped because they were cached or already completed.

    blocked_steps : tuple of str
        Steps blocked because a dependency failed.

    resource_decisions : int
        Number of resource-feasibility events.

    infeasible_steps : tuple of str
        Steps with infeasible resource decisions.

    max_active_steps : int
        Maximum per-task active-step count recorded in the file.

    max_suite_active_steps : int
        Maximum suite-wide active-step count recorded in the file.
    """

    event_filename: str
    graph_constructed: bool
    dask_backend: Optional[str]
    dask_workers: Optional[int]
    ready_steps: tuple[str, ...]
    started_steps: tuple[str, ...]
    finished_steps: tuple[str, ...]
    failed_steps: tuple[str, ...]
    skipped_steps: tuple[str, ...]
    blocked_steps: tuple[str, ...]
    resource_decisions: int
    infeasible_steps: tuple[str, ...]
    max_active_steps: int
    max_suite_active_steps: int

    @property
    def dask_runtime_used(self) -> bool:
        """
        Whether a Dask runtime event was recorded.
        """
        return self.dask_backend is not None

    @property
    def scheduler_path_used(self) -> bool:
        """
        Whether the event file shows scheduler graph execution.
        """
        return self.graph_constructed and len(self.ready_steps) > 0

    @property
    def single_active_step(self) -> bool:
        """
        Whether all recorded active-step counts satisfy the Phase 1 policy.
        """
        return self.max_active_steps <= 1 and self.max_suite_active_steps <= 1


@dataclass(frozen=True)
class SuiteScheduleSummary:
    """
    Summary of scheduler event files from a suite run.
    """

    task_summaries: tuple[ScheduleEventSummary, ...]

    @property
    def scheduler_path_used(self) -> bool:
        """
        Whether every task event file shows scheduler graph execution.
        """
        return all(
            summary.scheduler_path_used for summary in self.task_summaries
        )

    @property
    def dask_runtime_used(self) -> bool:
        """
        Whether every task event file records the Dask runtime.
        """
        return all(
            summary.dask_runtime_used for summary in self.task_summaries
        )

    @property
    def single_active_step(self) -> bool:
        """
        Whether every task event file satisfies the Phase 1 active-step policy.
        """
        return all(
            summary.single_active_step for summary in self.task_summaries
        )

    @property
    def max_suite_active_steps(self) -> int:
        """
        Maximum suite-wide active-step count across all event files.
        """
        return max(
            (
                summary.max_suite_active_steps
                for summary in self.task_summaries
            ),
            default=0,
        )


def read_schedule_events(event_filename) -> list[dict[str, Any]]:
    """
    Read a scheduler JSON-lines event file.

    Parameters
    ----------
    event_filename : str or pathlib.Path
        Path to ``schedule_events.jsonl``.

    Returns
    -------
    events : list of dict
        Decoded scheduler events.
    """
    events = []
    with Path(event_filename).open() as event_file:
        for line_number, line in enumerate(event_file, start=1):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as exception:
                raise ValueError(
                    f'Could not decode scheduler event line {line_number} '
                    f'in {event_filename}: {exception}'
                ) from exception
    return events


def summarize_schedule_events(event_filename) -> ScheduleEventSummary:
    """
    Summarize one scheduler JSON-lines event file.

    Parameters
    ----------
    event_filename : str or pathlib.Path
        Path to ``schedule_events.jsonl``.

    Returns
    -------
    summary : ScheduleEventSummary
        Summary of the scheduler events.
    """
    events = read_schedule_events(event_filename)
    dask_event = _first_event(events, 'dask_runtime')
    skipped_events = [
        event
        for event in events
        if event.get('event') == 'step_skipped'
        and event.get('reason') != 'blocked_dependency'
    ]
    blocked_events = [
        event
        for event in events
        if event.get('event') == 'step_skipped'
        and event.get('reason') == 'blocked_dependency'
    ]
    resource_events = [
        event
        for event in events
        if event.get('event') == 'resource_feasibility'
    ]
    return ScheduleEventSummary(
        event_filename=str(event_filename),
        graph_constructed=_first_event(events, 'graph_constructed')
        is not None,
        dask_backend=None if dask_event is None else dask_event.get('backend'),
        dask_workers=None if dask_event is None else dask_event.get('workers'),
        ready_steps=_event_steps(events, 'ready_selection'),
        started_steps=_event_steps(events, 'step_start'),
        finished_steps=_event_steps(events, 'step_finish'),
        failed_steps=_event_steps(events, 'step_failure'),
        skipped_steps=tuple(_event_step(event) for event in skipped_events),
        blocked_steps=tuple(_event_step(event) for event in blocked_events),
        resource_decisions=len(resource_events),
        infeasible_steps=tuple(
            _event_step(event)
            for event in resource_events
            if not event.get('feasible', False)
        ),
        max_active_steps=_max_event_count(events, 'active_steps'),
        max_suite_active_steps=_max_event_count(events, 'suite_active_steps'),
    )


def validate_phase1_schedule_events(
    event_filename, require_dask_runtime=False
) -> ScheduleEventSummary:
    """
    Validate one Phase 1 scheduler event file.

    Parameters
    ----------
    event_filename : str or pathlib.Path
        Path to ``schedule_events.jsonl``.

    require_dask_runtime : bool, optional
        Whether the event file must include a Dask runtime event.

    Returns
    -------
    summary : ScheduleEventSummary
        Summary of the scheduler events.

    Raises
    ------
    ValueError
        If required Phase 1 scheduler evidence is missing.
    """
    summary = summarize_schedule_events(event_filename)
    errors = _phase1_schedule_errors(summary, require_dask_runtime)
    if len(errors) > 0:
        message = '\n'.join(f'- {error}' for error in errors)
        raise ValueError(
            f'Invalid Phase 1 schedule events in {event_filename}:\n{message}'
        )
    return summary


def validate_phase1_schedule_event_files(
    event_filenames: Iterable, require_dask_runtime=False
) -> SuiteScheduleSummary:
    """
    Validate scheduler event files from a Phase 1 suite run.

    Parameters
    ----------
    event_filenames : iterable
        Paths to task ``schedule_events.jsonl`` files.

    require_dask_runtime : bool, optional
        Whether each event file must include a Dask runtime event.

    Returns
    -------
    summary : SuiteScheduleSummary
        Summary of the suite scheduler artifacts.
    """
    task_summaries = tuple(
        validate_phase1_schedule_events(
            event_filename, require_dask_runtime=require_dask_runtime
        )
        for event_filename in event_filenames
    )
    if len(task_summaries) == 0:
        raise ValueError('No scheduler event files were provided.')
    return SuiteScheduleSummary(task_summaries=task_summaries)


def _phase1_schedule_errors(
    summary: ScheduleEventSummary, require_dask_runtime: bool
) -> list[str]:
    errors = []
    if not summary.graph_constructed:
        errors.append('missing graph_constructed event')
    if len(summary.ready_steps) == 0:
        errors.append('missing ready_selection events')
    if require_dask_runtime and not summary.dask_runtime_used:
        errors.append('missing dask_runtime event')
    if not summary.single_active_step:
        errors.append(
            'active-step counts exceed the Phase 1 single-step policy'
        )
    return errors


def _first_event(events: list[dict[str, Any]], event_name: str):
    for event in events:
        if event.get('event') == event_name:
            return event
    return None


def _event_steps(
    events: list[dict[str, Any]], event_name: str
) -> tuple[str, ...]:
    return tuple(
        _event_step(event)
        for event in events
        if event.get('event') == event_name
    )


def _event_step(event: dict[str, Any]) -> str:
    task_name = event.get('task')
    step_name = event.get('step')
    if task_name is None:
        return str(step_name)
    return f'{task_name}/{step_name}'


def _max_event_count(events: list[dict[str, Any]], field: str) -> int:
    return max(
        (
            int(event[field])
            for event in events
            if event.get(field) is not None
        ),
        default=0,
    )
