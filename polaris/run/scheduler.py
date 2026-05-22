import json
import os
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Mapping, Optional

from polaris.run.dask import get_dask_runtime_info
from polaris.run.resources import ResourcePool, get_step_resource_request
from polaris.run.shared import (
    accumulate_statuses,
    end_color,
    error_str,
    fail_str,
    pass_str,
    print_to_stdout,
    read_baseline_status_from_logs,
    read_property_status_from_logs,
    run_step,
    run_step_as_subprocess,
    setup_config,
    start_time_color,
    success_str,
)


@dataclass(frozen=True)
class SchedulerNode:
    """
    A step participating in scheduler graph validation.

    Attributes
    ----------
    key : str
        Stable scheduler key for this node.

    task_name : str, optional
        The task path/name this step was selected from, or ``None`` for an
        already-satisfied dependency outside the selected task steps.

    step_name : str
        The step name.

    step : polaris.Step
        The step represented by this node.

    order : int
        Stable inventory order used for deterministic graph traversal.

    selected : bool
        Whether this step was selected by ``steps_to_run``.

    cached : bool
        Whether this step is configured to use cached outputs.

    completed : bool
        Whether this step has an existing completion marker.
    """

    key: str
    task_name: Optional[str]
    step_name: str
    step: Any
    order: int
    selected: bool
    cached: bool
    completed: bool


@dataclass(frozen=True)
class SchedulerGraph:
    """
    Directed graph of scheduler nodes.

    Edges point from prerequisites to dependents.
    """

    nodes: dict[str, SchedulerNode]
    predecessors: dict[str, set[str]]
    successors: dict[str, set[str]]

    def ordered_nodes(self) -> list[SchedulerNode]:
        """
        Return graph nodes in deterministic scheduler order.
        """
        return sorted(self.nodes.values(), key=lambda node: node.order)

    def topological_order(self) -> list[SchedulerNode]:
        """
        Return a deterministic topological order for the graph.
        """
        return _topological_order(self)


class ScheduleRecorder:
    """
    Record human-readable and structured scheduler events for one task.
    """

    def __init__(self, task):
        """
        Create a recorder for the given task.

        Parameters
        ----------
        task : polaris.Task
            The task being scheduled.
        """
        self.task = task
        self.event_filename = os.path.join(
            task.work_dir, 'schedule_events.jsonl'
        )
        self.active_steps = 0
        with open(self.event_filename, 'w'):
            pass

    def emit(self, event, **kwargs):
        """
        Write one structured schedule event.

        Parameters
        ----------
        event : str
            Event type.

        kwargs : dict
            Additional event metadata.
        """
        payload = dict(event=event, time=time.time(), **kwargs)
        with open(self.event_filename, 'a') as event_file:
            event_file.write(f'{json.dumps(payload, sort_keys=True)}\n')


def build_scheduler_graph(tasks: Mapping[str, Any]) -> SchedulerGraph:
    """
    Build a scheduler graph from selected task steps.

    Parameters
    ----------
    tasks : Mapping[str, polaris.Task]
        Tasks in stable suite order. Each task's ``steps_to_run`` controls the
        selected steps from that task.

    Returns
    -------
    graph : SchedulerGraph
        The scheduler graph.

    Raises
    ------
    ValueError
        If an explicit dependency or declared input file is not selected,
        external, cached or already completed, or if the dependency graph has
        a cycle.
    """
    nodes: dict[str, SchedulerNode] = {}
    predecessors: dict[str, set[str]] = {}
    successors: dict[str, set[str]] = {}
    selected_step_keys: dict[int, list[str]] = {}
    output_providers: dict[str, list[str]] = {}
    satisfied_output_providers: dict[str, Any] = {}

    order = 0
    for task_name, task in tasks.items():
        for step_name in task.steps_to_run:
            step = task.steps[step_name]
            key = _selected_step_key(task_name, step_name)
            node = _make_node(
                key=key,
                task_name=task_name,
                step_name=step_name,
                step=step,
                order=order,
                selected=True,
            )
            _add_node(node, nodes, predecessors, successors)
            selected_step_keys.setdefault(id(step), []).append(key)
            for output in step.outputs:
                output_path = _resolve_step_path(step, output)
                output_providers.setdefault(output_path, []).append(key)
            order += 1

    for task in tasks.values():
        selected_steps = {
            task.steps[step_name] for step_name in task.steps_to_run
        }
        for step in task.steps.values():
            if step in selected_steps or not _is_step_satisfied(step):
                continue
            for output in step.outputs:
                output_path = _resolve_step_path(step, output)
                satisfied_output_providers[output_path] = step

    for node in list(nodes.values()):
        for dependency in node.step.dependencies.values():
            dependency_key = _find_dependency_key(
                dependency, node, selected_step_keys, nodes
            )
            if dependency_key is None:
                if not _is_step_satisfied(dependency):
                    raise ValueError(
                        f'The dependency {dependency.path} of step '
                        f'{node.step.path} is not selected, cached or '
                        f'already completed.'
                    )
                dependency_key = _satisfied_dependency_key(dependency)
                if dependency_key not in nodes:
                    dependency_node = _make_node(
                        key=dependency_key,
                        task_name=None,
                        step_name=dependency.name,
                        step=dependency,
                        order=order,
                        selected=False,
                    )
                    _add_node(dependency_node, nodes, predecessors, successors)
                    order += 1
            _add_edge(dependency_key, node.key, predecessors, successors)

        for input_file in node.step.inputs:
            input_path = _resolve_step_path(node.step, input_file)
            provider_key = _get_selected_output_provider(
                input_path, node, output_providers, nodes
            )
            if provider_key is not None:
                _add_edge(provider_key, node.key, predecessors, successors)
                continue

            if input_path in satisfied_output_providers:
                provider = satisfied_output_providers[input_path]
                provider_key = _satisfied_dependency_key(provider)
                if provider_key not in nodes:
                    provider_node = _make_node(
                        key=provider_key,
                        task_name=None,
                        step_name=provider.name,
                        step=provider,
                        order=order,
                        selected=False,
                    )
                    _add_node(provider_node, nodes, predecessors, successors)
                    order += 1
                _add_edge(provider_key, node.key, predecessors, successors)
                continue

            if not os.path.exists(input_path):
                raise ValueError(
                    f'The input file {input_path} of step {node.step.path} '
                    'does not exist and is not produced by a selected, '
                    'cached or already completed step.'
                )

    _topological_order(
        SchedulerGraph(
            nodes=nodes, predecessors=predecessors, successors=successors
        )
    )

    return SchedulerGraph(
        nodes=nodes, predecessors=predecessors, successors=successors
    )


def run_task(
    task, available_resources, subprocess_command='serial', dask_client=None
):
    """
    Run each selected step in a task through the Phase 1 scheduler.

    Parameters
    ----------
    task : polaris.Task
        The task to run. Its ``steps_to_run`` list controls selected graph
        nodes.

    available_resources : dict
        Available CPU, GPU and MPI resources for this run.

    subprocess_command : str, optional
        Polaris subcommand to use when a step must run in a subprocess.

    dask_client : distributed.Client, optional
        Dask client for the active ``polaris run`` lifecycle.

    Returns
    -------
    baselines_passed : bool or None
        Aggregate baseline comparison status across selected steps. ``None``
        means no baseline comparisons were performed.
    """
    logger = task.logger
    cwd = os.getcwd()
    graph = build_scheduler_graph({task.path: task})
    resource_pool = ResourcePool(available_resources)
    recorder = ScheduleRecorder(task)
    ordered_nodes = graph.topological_order()
    edge_count = sum(
        len(successors) for successors in graph.successors.values()
    )
    recorder.emit(
        'graph_constructed', nodes=len(graph.nodes), edges=edge_count
    )
    runtime_info = get_dask_runtime_info(dask_client)
    if runtime_info is not None:
        recorder.emit(
            'dask_runtime',
            backend=runtime_info.backend,
            workers=runtime_info.workers,
        )
    _log_schedule_summary(task, ordered_nodes)

    baselines_passed = None
    property_passed = None
    for node in ordered_nodes:
        if not node.selected:
            continue

        step = node.step
        recorder.emit(
            'ready_selection',
            task=node.task_name,
            step=node.step_name,
            status=_node_status(node),
        )
        print_to_stdout(task, f'  * step: {node.step_name}')

        if node.completed:
            print_to_stdout(task, '          already completed')
            recorder.emit(
                'step_skipped',
                task=node.task_name,
                step=node.step_name,
                reason='already completed',
            )
            baseline_status = read_baseline_status_from_logs(step.work_dir)
            if baseline_status is not None:
                baseline_str = pass_str if baseline_status else fail_str
                print_to_stdout(
                    task, f'          baseline comp.:   {baseline_str}'
                )
                baselines_passed = accumulate_statuses(
                    baselines_passed, baseline_status
                )
            property_status = read_property_status_from_logs(step.work_dir)
            if property_status is not None:
                property_str = pass_str if property_status else fail_str
                print_to_stdout(
                    task, f'          property comp.:   {property_str}'
                )
                property_passed = accumulate_statuses(
                    property_passed, property_status
                )
            continue
        if node.cached:
            print_to_stdout(task, '          cached')
            recorder.emit(
                'step_skipped',
                task=node.task_name,
                step=node.step_name,
                reason='cached',
            )
            continue

        step_start = time.time()
        step.config = setup_config(step.base_work_dir, step.config.filepath)
        if task.log_filename is not None:
            step_log_filename = task.log_filename
        else:
            step_log_filename = None

        reservation = None
        try:
            request = get_step_resource_request(step, available_resources)
            reservation = resource_pool.reserve_step(step, request)
            print_to_stdout(
                task,
                f'          resources:        cores={reservation.cores}, '
                f'nodes={reservation.nodes}, gpus={reservation.gpus}',
            )
            recorder.emit(
                'resource_reserved',
                task=node.task_name,
                step=node.step_name,
                cores=reservation.cores,
                nodes=reservation.nodes,
                gpus=reservation.gpus,
            )
            recorder.active_steps += 1
            recorder.emit(
                'step_start',
                task=node.task_name,
                step=node.step_name,
                active_steps=recorder.active_steps,
            )
            if step.run_as_subprocess:
                run_step_as_subprocess(
                    logger,
                    step,
                    task.new_step_log_file,
                    subprocess_command=subprocess_command,
                )
            else:
                run_step(
                    task,
                    step,
                    task.new_step_log_file,
                    available_resources,
                    step_log_filename,
                    dask_client=dask_client,
                )
        except Exception:
            step_time = time.time() - step_start
            recorder.emit(
                'step_failure',
                task=node.task_name,
                step=node.step_name,
                active_steps=recorder.active_steps,
                duration=step_time,
            )
            print_to_stdout(task, f'          execution:        {error_str}')
            raise
        finally:
            if recorder.active_steps > 0:
                recorder.active_steps -= 1
            if reservation is not None:
                resource_pool.release(reservation)
                recorder.emit(
                    'resource_released',
                    task=node.task_name,
                    step=node.step_name,
                    active_steps=recorder.active_steps,
                )
            os.chdir(cwd)

        print_to_stdout(task, f'          execution:        {success_str}')
        step_time = time.time() - step_start
        recorder.emit(
            'step_finish',
            task=node.task_name,
            step=node.step_name,
            active_steps=recorder.active_steps,
            duration=step_time,
        )
        step_time_str = str(timedelta(seconds=round(step_time)))

        compared, status = step.check_properties()
        if compared:
            property_str = pass_str if status else fail_str
            print_to_stdout(
                task, f'          property checks:   {property_str}'
            )
            property_passed = accumulate_statuses(property_passed, status)

        compared, status = step.validate_baselines()
        if compared:
            baseline_str = pass_str if status else fail_str
            print_to_stdout(
                task, f'          baseline comp.:   {baseline_str}'
            )
            baselines_passed = accumulate_statuses(baselines_passed, status)

        print_to_stdout(
            task,
            f'          runtime:          '
            f'{start_time_color}{step_time_str}{end_color}',
        )

    return baselines_passed


def _log_schedule_summary(task, ordered_nodes: list[SchedulerNode]) -> None:
    selected_nodes = [node for node in ordered_nodes if node.selected]
    print_to_stdout(task, '  scheduler: selected order')
    for index, node in enumerate(selected_nodes, start=1):
        print_to_stdout(
            task,
            f'    {index}. {node.task_name}/{node.step_name} '
            f'[{_node_status(node)}]',
        )


def _node_status(node: SchedulerNode) -> str:
    if node.completed:
        return 'already completed'
    if node.cached:
        return 'cached'
    return 'ready'


def _make_node(
    key: str,
    task_name: Optional[str],
    step_name: str,
    step: Any,
    order: int,
    selected: bool,
) -> SchedulerNode:
    return SchedulerNode(
        key=key,
        task_name=task_name,
        step_name=step_name,
        step=step,
        order=order,
        selected=selected,
        cached=step.cached,
        completed=_is_step_complete(step),
    )


def _add_node(
    node: SchedulerNode,
    nodes: dict[str, SchedulerNode],
    predecessors: dict[str, set[str]],
    successors: dict[str, set[str]],
) -> None:
    if node.key in nodes:
        raise ValueError(f'Duplicate scheduler node: {node.key}')
    nodes[node.key] = node
    predecessors[node.key] = set()
    successors[node.key] = set()


def _add_edge(
    from_key: str,
    to_key: str,
    predecessors: dict[str, set[str]],
    successors: dict[str, set[str]],
) -> None:
    successors[from_key].add(to_key)
    predecessors[to_key].add(from_key)


def _find_dependency_key(
    dependency: Any,
    node: SchedulerNode,
    selected_step_keys: dict[int, list[str]],
    nodes: dict[str, SchedulerNode],
) -> Optional[str]:
    candidates = selected_step_keys.get(id(dependency), [])
    if len(candidates) == 0:
        return None

    same_task_candidates = [
        key for key in candidates if nodes[key].task_name == node.task_name
    ]
    if len(same_task_candidates) == 1:
        return same_task_candidates[0]
    if len(candidates) == 1:
        return candidates[0]

    raise ValueError(
        f'The dependency {dependency.path} of step {node.step.path} is '
        'selected in multiple tasks and cannot be resolved unambiguously.'
    )


def _get_selected_output_provider(
    input_path: str,
    node: SchedulerNode,
    output_providers: dict[str, list[str]],
    nodes: dict[str, SchedulerNode],
) -> Optional[str]:
    candidates = [
        key for key in output_providers.get(input_path, []) if key != node.key
    ]
    if len(candidates) == 0:
        return None

    same_task_candidates = [
        key for key in candidates if nodes[key].task_name == node.task_name
    ]
    if len(same_task_candidates) == 1:
        return same_task_candidates[0]
    if len(candidates) == 1:
        return candidates[0]

    raise ValueError(
        f'The input file {input_path} of step {node.step.path} is produced '
        'by multiple selected steps and cannot be resolved unambiguously.'
    )


def _topological_order(graph: SchedulerGraph) -> list[SchedulerNode]:
    remaining_predecessors = {
        key: set(predecessors)
        for key, predecessors in graph.predecessors.items()
    }
    ready = [
        key
        for key, predecessors in remaining_predecessors.items()
        if len(predecessors) == 0
    ]
    ordered_keys: list[str] = []

    while len(ready) > 0:
        ready.sort(key=lambda key: graph.nodes[key].order)
        key = ready.pop(0)
        ordered_keys.append(key)

        for successor in sorted(
            graph.successors[key], key=lambda key: graph.nodes[key].order
        ):
            remaining_predecessors[successor].remove(key)
            if len(remaining_predecessors[successor]) == 0:
                ready.append(successor)

    if len(ordered_keys) != len(graph.nodes):
        cycle_keys = sorted(
            set(graph.nodes) - set(ordered_keys),
            key=lambda key: graph.nodes[key].order,
        )
        cycle_steps = ', '.join(
            graph.nodes[key].step.path for key in cycle_keys
        )
        raise ValueError(
            f'The scheduler dependency graph contains a cycle: {cycle_steps}'
        )

    return [graph.nodes[key] for key in ordered_keys]


def _selected_step_key(task_name: str, step_name: str) -> str:
    return f'{task_name}:{step_name}'


def _satisfied_dependency_key(step: Any) -> str:
    return f'satisfied:{step.path}'


def _is_step_satisfied(step: Any) -> bool:
    return step.cached or _is_step_complete(step)


def _is_step_complete(step: Any) -> bool:
    complete_filename = os.path.join(
        step.work_dir, 'polaris_step_complete.log'
    )
    return os.path.exists(complete_filename)


def _resolve_step_path(step: Any, filename: str) -> str:
    if os.path.isabs(filename):
        return os.path.abspath(filename)
    return os.path.abspath(os.path.join(step.work_dir, filename))
