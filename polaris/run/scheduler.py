import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any, Mapping, Optional

import mpas_tools.io
from mpas_tools.logging import LoggingContext

from polaris.logging import log_function_call
from polaris.run.dask import get_dask_runtime_info
from polaris.run.resources import (
    ResourcePool,
    get_resource_views,
    get_step_resource_request,
)
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
    update_steps_to_run,
)

__all__ = [
    'SchedulerGraph',
    'SchedulerNode',
    'build_scheduler_graph',
    'run_suite',
    'run_task',
]


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


class _ScheduleRecorder:
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


@dataclass
class _SuiteTaskRunState:
    """
    Runtime status for one task in a suite-wide scheduler run.
    """

    task: Any
    log_filename: str
    task_time: float = 0.0
    result_str: str = pass_str
    success: bool = True
    exec_failed: bool = False
    diff_failed: bool = False
    baselines_passed: Optional[bool] = None
    task_pass: bool = True
    start_time: float = 0.0


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
    output_providers: dict[str, list[str]] = {}
    order, selected_step_keys = _add_selected_scheduler_nodes(
        tasks=tasks,
        nodes=nodes,
        predecessors=predecessors,
        successors=successors,
        output_providers=output_providers,
    )
    satisfied_output_providers = _get_satisfied_output_providers(
        tasks=tasks, selected_step_keys=selected_step_keys
    )

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


def run_suite(
    suite,
    stdout_logger,
    quiet,
    log_dir,
    available_resources,
    subprocess_command='run',
    dask_client=None,
):
    """
    Run a suite through one suite-wide scheduler graph.

    Parameters
    ----------
    suite : dict
        The suite description containing selected tasks.

    stdout_logger : logging.Logger
        Logger used for run-level output.

    quiet : bool
        Whether step progress should be omitted from stdout.

    log_dir : str
        Directory for per-task log files.

    available_resources : dict
        Available CPU, GPU and MPI resources for this run.

    subprocess_command : str, optional
        Polaris subcommand to use when a step must run in a subprocess.

    dask_client : distributed.Client, optional
        Dask client for the active ``polaris run`` lifecycle.

    Returns
    -------
    results : dict
        Aggregate suite results with task times, result strings, failure
        counts and failure task lists.
    """
    states = _prepare_suite_tasks(
        suite=suite,
        stdout_logger=stdout_logger,
        quiet=quiet,
        log_dir=log_dir,
    )
    tasks = {state.task.path: state.task for state in states.values()}
    graph = build_scheduler_graph(tasks)
    resource_views = get_resource_views(available_resources)
    data_plane_resources = resource_views.data_plane
    resource_pool = ResourcePool(data_plane_resources)
    recorders = {
        task_name: _ScheduleRecorder(state.task)
        for task_name, state in states.items()
    }
    ordered_nodes = graph.topological_order()
    edge_count = sum(
        len(successors) for successors in graph.successors.values()
    )
    runtime_info = get_dask_runtime_info(dask_client)
    for task_name, state in states.items():
        recorder = recorders[task_name]
        recorder.emit(
            'graph_constructed', nodes=len(graph.nodes), edges=edge_count
        )
        if runtime_info is not None:
            _emit_dask_runtime(recorder, runtime_info)
        with LoggingContext(
            task_name.replace('/', '_'), log_filename=state.log_filename
        ) as task_logger:
            _configure_task_loggers(
                state.task,
                task_logger,
                stdout_logger,
                quiet,
                state.log_filename,
            )
            _log_schedule_summary(state.task, ordered_nodes, graph)

    cwd = os.getcwd()
    current_task_name = None
    active_counter = dict(active_steps=0)
    blocked_keys: set[str] = set()
    failed_keys: set[str] = set()
    step_runtimes: dict[str, float] = {}
    for node in ordered_nodes:
        if not node.selected:
            continue

        task_name = node.task_name
        assert task_name is not None
        state = states[task_name]
        task = state.task
        if task_name != current_task_name:
            stdout_logger.info(f'{task_name}')
            current_task_name = task_name

        blocked_dependencies = sorted(
            graph.predecessors[node.key] & (blocked_keys | failed_keys)
        )
        if len(blocked_dependencies) > 0:
            state.task_pass = False
            state.exec_failed = True
            blocked_keys.add(node.key)
            with LoggingContext(
                task_name.replace('/', '_'), log_filename=state.log_filename
            ) as task_logger:
                _configure_task_loggers(
                    task, task_logger, stdout_logger, quiet, state.log_filename
                )
                _block_scheduler_node(
                    node=node,
                    task=task,
                    recorder=recorders[task_name],
                    blocked_dependencies=blocked_dependencies,
                    active_counter=active_counter,
                )
            continue

        try:
            with LoggingContext(
                task_name.replace('/', '_'), log_filename=state.log_filename
            ) as task_logger:
                _configure_task_loggers(
                    task, task_logger, stdout_logger, quiet, state.log_filename
                )
                baseline_status, _ = _run_scheduler_node(
                    node=node,
                    task=task,
                    available_resources=data_plane_resources,
                    resource_pool=resource_pool,
                    recorder=recorders[task_name],
                    cwd=cwd,
                    subprocess_command=subprocess_command,
                    dask_client=dask_client,
                    predecessor_keys=graph.predecessors[node.key],
                    active_counter=active_counter,
                    step_runtimes=step_runtimes,
                )
            if baseline_status is not None:
                state.baselines_passed = accumulate_statuses(
                    state.baselines_passed, baseline_status
                )
        except Exception:
            state.task_pass = False
            state.exec_failed = True
            failed_keys.add(node.key)
            with LoggingContext(
                task_name.replace('/', '_'), log_filename=state.log_filename
            ) as task_logger:
                task_logger.exception(
                    'Exception raised while running the steps of the task'
                )

    _finalize_suite_task_states(states, stdout_logger, step_runtimes)
    return _suite_results(states)


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
    cwd = os.getcwd()
    graph = build_scheduler_graph({task.path: task})
    resource_views = get_resource_views(available_resources)
    data_plane_resources = resource_views.data_plane
    resource_pool = ResourcePool(data_plane_resources)
    recorder = _ScheduleRecorder(task)
    ordered_nodes = graph.topological_order()
    edge_count = sum(
        len(successors) for successors in graph.successors.values()
    )
    recorder.emit(
        'graph_constructed', nodes=len(graph.nodes), edges=edge_count
    )
    runtime_info = get_dask_runtime_info(dask_client)
    if runtime_info is not None:
        _emit_dask_runtime(recorder, runtime_info)
    _log_schedule_summary(task, ordered_nodes, graph)

    baselines_passed = None
    property_passed = None
    blocked_keys: set[str] = set()
    failed_keys: set[str] = set()
    first_exception = None
    for node in ordered_nodes:
        if not node.selected:
            continue

        blocked_dependencies = sorted(
            graph.predecessors[node.key] & (blocked_keys | failed_keys)
        )
        if len(blocked_dependencies) > 0:
            blocked_keys.add(node.key)
            _block_scheduler_node(
                node=node,
                task=task,
                recorder=recorder,
                blocked_dependencies=blocked_dependencies,
                active_counter=None,
            )
            continue

        try:
            baseline_status, property_status = _run_scheduler_node(
                node=node,
                task=task,
                available_resources=data_plane_resources,
                resource_pool=resource_pool,
                recorder=recorder,
                cwd=cwd,
                subprocess_command=subprocess_command,
                dask_client=dask_client,
                predecessor_keys=graph.predecessors[node.key],
            )
        except Exception as exception:
            failed_keys.add(node.key)
            if first_exception is None:
                first_exception = exception
            continue

        if baseline_status is not None:
            baselines_passed = accumulate_statuses(
                baselines_passed, baseline_status
            )
        if property_status is not None:
            property_passed = accumulate_statuses(
                property_passed, property_status
            )

    if first_exception is not None:
        raise first_exception

    return baselines_passed


def _prepare_suite_tasks(suite, stdout_logger, quiet, log_dir):
    states = {}
    cwd = os.getcwd()
    for task in suite['tasks'].values():
        task_name = task.path.replace('/', '_')
        log_filename = os.path.join(log_dir, f'{task_name}.log')
        with LoggingContext(
            task_name, log_filename=log_filename
        ) as task_logger:
            _configure_task_loggers(
                task, task_logger, stdout_logger, quiet, log_filename
            )
            os.chdir(task.work_dir)
            config = setup_config(task.base_work_dir, task.config.filepath)
            task.config = config
            mpas_tools.io.default_format = config.get('io', 'format')
            mpas_tools.io.default_engine = config.get('io', 'engine')
            task.steps_to_run = update_steps_to_run(
                task.name, None, None, config, task.steps
            )
            log_function_call(function=run_suite, logger=task_logger)
            task_logger.info('')
            task_list = ', '.join(task.steps_to_run)
            task_logger.info(f'Running steps: {task_list}')
            os.chdir(cwd)
        states[task.path] = _SuiteTaskRunState(
            task=task,
            log_filename=log_filename,
            start_time=time.time(),
        )
    return states


def _configure_task_loggers(
    task, task_logger, stdout_logger, quiet, log_filename
):
    if quiet:
        task.stdout_logger = task_logger
    else:
        task.stdout_logger = stdout_logger
    task.logger = task_logger
    task.log_filename = log_filename
    task.new_step_log_file = False


def _add_selected_scheduler_nodes(
    tasks,
    nodes,
    predecessors,
    successors,
    output_providers,
):
    selected_step_keys: dict[str, list[str]] = {}
    order = 0
    for task_name, task in tasks.items():
        for step_name in task.steps_to_run:
            step = task.steps[step_name]
            step_identity = _step_identity(step)
            existing_keys = selected_step_keys.get(step_identity)
            if existing_keys is not None:
                key = existing_keys[0]
                _add_step_outputs(output_providers, step, key)
                continue

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
            selected_step_keys[step_identity] = [key]
            _add_step_outputs(output_providers, step, key)
            order += 1
    return order, selected_step_keys


def _get_satisfied_output_providers(tasks, selected_step_keys):
    satisfied_output_providers = {}
    for task in tasks.values():
        for step in task.steps.values():
            step_identity = _step_identity(step)
            if step_identity in selected_step_keys or not _is_step_satisfied(
                step
            ):
                continue
            for output in step.outputs:
                output_path = _resolve_step_path(step, output)
                satisfied_output_providers[output_path] = step
    return satisfied_output_providers


def _run_scheduler_node(
    node,
    task,
    available_resources,
    resource_pool,
    recorder,
    cwd,
    subprocess_command,
    dask_client,
    predecessor_keys=None,
    active_counter=None,
    step_runtimes=None,
):
    step = node.step
    recorder.emit(
        'ready_selection',
        task=node.task_name,
        step=node.step_name,
        status=_node_status(node),
        wait_reason=_wait_reason(predecessor_keys),
    )
    print_to_stdout(task, f'  * step: {node.step_name}')

    if node.completed:
        print_to_stdout(task, '          already completed')
        baseline_status = read_baseline_status_from_logs(step.work_dir)
        if baseline_status is not None:
            baseline_str = pass_str if baseline_status else fail_str
            print_to_stdout(
                task, f'          baseline comp.:   {baseline_str}'
            )
        property_status = read_property_status_from_logs(step.work_dir)
        if property_status is not None:
            property_str = pass_str if property_status else fail_str
            print_to_stdout(
                task, f'          property comp.:   {property_str}'
            )
        recorder.emit(
            'step_skipped',
            task=node.task_name,
            step=node.step_name,
            baseline_status=baseline_status,
            completion_marker=True,
            property_status=property_status,
            reason='already completed',
            result='skipped',
            satisfies_dependencies=True,
            wait_reason=_wait_reason(predecessor_keys),
        )
        return baseline_status, property_status

    if node.cached:
        print_to_stdout(task, '          cached')
        recorder.emit(
            'step_skipped',
            task=node.task_name,
            step=node.step_name,
            reason='cached',
            result='skipped',
            satisfies_dependencies=True,
            wait_reason=_wait_reason(predecessor_keys),
        )
        return None, None

    step_start = time.time()
    step.config = setup_config(step.base_work_dir, step.config.filepath)
    if task.log_filename is not None:
        step_log_filename = task.log_filename
    else:
        step_log_filename = None

    reservation = None
    try:
        try:
            request = get_step_resource_request(step, available_resources)
        except ValueError as exception:
            _emit_resource_feasibility(
                recorder=recorder,
                node=node,
                resource_pool=resource_pool,
                feasible=False,
                reason=str(exception),
            )
            raise
        feasible = _is_resource_request_feasible(request, resource_pool)
        _emit_resource_feasibility(
            recorder=recorder,
            node=node,
            resource_pool=resource_pool,
            request=request,
            feasible=feasible,
        )
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
            free_cores=resource_pool.free_cores,
            free_gpus=resource_pool.free_gpus,
            free_nodes=resource_pool.free_nodes,
            nodes=reservation.nodes,
            gpus=reservation.gpus,
            result='reserved',
            total_cores=resource_pool.total_cores,
            total_gpus=resource_pool.total_gpus,
            total_nodes=resource_pool.total_nodes,
        )
        recorder.active_steps += 1
        _increment_active_counter(active_counter)
        recorder.emit(
            'step_start',
            task=node.task_name,
            step=node.step_name,
            active_steps=recorder.active_steps,
            result='running',
            suite_active_steps=_active_count(active_counter),
        )
        if step.run_as_subprocess:
            run_step_as_subprocess(
                task.logger,
                step,
                task.new_step_log_file,
                subprocess_command=subprocess_command,
                dask_client=dask_client,
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
        _record_step_runtime(step_runtimes, step, step_time)
        recorder.emit(
            'step_failure',
            task=node.task_name,
            step=node.step_name,
            active_steps=recorder.active_steps,
            duration=step_time,
            result='failure',
            suite_active_steps=_active_count(active_counter),
        )
        print_to_stdout(task, f'          execution:        {error_str}')
        raise
    finally:
        if recorder.active_steps > 0:
            recorder.active_steps -= 1
        _decrement_active_counter(active_counter)
        if reservation is not None:
            resource_pool.release(reservation)
            recorder.emit(
                'resource_released',
                task=node.task_name,
                step=node.step_name,
                active_steps=recorder.active_steps,
                free_cores=resource_pool.free_cores,
                free_gpus=resource_pool.free_gpus,
                free_nodes=resource_pool.free_nodes,
                result='released',
                suite_active_steps=_active_count(active_counter),
                total_cores=resource_pool.total_cores,
                total_gpus=resource_pool.total_gpus,
                total_nodes=resource_pool.total_nodes,
            )
        os.chdir(cwd)

    print_to_stdout(task, f'          execution:        {success_str}')
    step_time = time.time() - step_start
    _record_step_runtime(step_runtimes, step, step_time)
    recorder.emit(
        'step_finish',
        task=node.task_name,
        step=node.step_name,
        active_steps=recorder.active_steps,
        duration=step_time,
        result='success',
        suite_active_steps=_active_count(active_counter),
    )
    step_time_str = str(timedelta(seconds=round(step_time)))

    property_status = None
    compared, status = step.check_properties()
    if compared:
        property_str = pass_str if status else fail_str
        print_to_stdout(task, f'          property checks:   {property_str}')
        property_status = status

    baseline_status = None
    compared, status = step.validate_baselines()
    if compared:
        baseline_str = pass_str if status else fail_str
        print_to_stdout(task, f'          baseline comp.:   {baseline_str}')
        baseline_status = status

    print_to_stdout(
        task,
        f'          runtime:          '
        f'{start_time_color}{step_time_str}{end_color}',
    )
    return baseline_status, property_status


def _block_scheduler_node(
    node,
    task,
    recorder,
    blocked_dependencies,
    active_counter,
):
    dependency_list = ', '.join(blocked_dependencies)
    recorder.emit(
        'ready_selection',
        task=node.task_name,
        step=node.step_name,
        status='blocked',
        wait_reason='failed_dependency',
    )
    print_to_stdout(task, f'  * step: {node.step_name}')
    print_to_stdout(
        task, f'          blocked by failed dependency: {dependency_list}'
    )
    recorder.emit(
        'step_skipped',
        task=node.task_name,
        step=node.step_name,
        blocked_dependencies=blocked_dependencies,
        reason='blocked_dependency',
        result='blocked',
        suite_active_steps=_active_count(active_counter),
        wait_reason='failed_dependency',
    )


def _finalize_suite_task_states(states, stdout_logger, step_runtimes):
    for state in states.values():
        state.task_time = _task_step_runtime(state.task, step_runtimes)
        if state.task_pass:
            if state.baselines_passed is None:
                state.result_str = pass_str
                state.success = True
            elif state.baselines_passed:
                state.result_str = pass_str
                state.success = True
            else:
                state.result_str = fail_str
                state.success = False
                state.diff_failed = True
        else:
            state.result_str = fail_str
            state.success = False

        with LoggingContext(
            state.task.path.replace('/', '_'),
            log_filename=state.log_filename,
        ) as task_logger:
            task_status = 'PASS' if state.task_pass else 'FAIL'
            task_logger.info(f'POLARIS TASK: {task_status}')
            if state.baselines_passed is not None:
                baseline_status = 'PASS' if state.baselines_passed else 'FAIL'
                task_logger.info(f'POLARIS BASELINE: {baseline_status}')

        run_status = success_str if state.task_pass else error_str
        stdout_logger.info(f'  task execution:   {run_status}')
        if not state.success and not state.diff_failed:
            task_name = state.task.path.replace('/', '_')
            stdout_logger.error(f'  see: case_outputs/{task_name}.log')
        if state.baselines_passed is not None and state.task_pass:
            baseline_str = pass_str if state.baselines_passed else fail_str
            stdout_logger.info(f'  baseline comp.:   {baseline_str}')
        task_time_str = str(timedelta(seconds=round(state.task_time)))
        stdout_logger.info(
            f'  task runtime:     {start_time_color}{task_time_str}{end_color}'
        )


def _record_step_runtime(step_runtimes, step, step_time) -> None:
    if step_runtimes is None:
        return
    step_runtimes[_step_identity(step)] = step_time


def _task_step_runtime(task, step_runtimes) -> float:
    total = 0.0
    for step_name in task.steps_to_run:
        step = task.steps[step_name]
        total += step_runtimes.get(_step_identity(step), 0.0)
    return total


def _suite_results(states):
    exec_fail_tasks = []
    diff_fail_tasks = []
    task_times = {}
    result_strs = {}
    failures = 0

    for task_name, state in states.items():
        task_times[task_name] = state.task_time
        result_strs[task_name] = state.result_str
        if not state.success:
            failures += 1
        if state.exec_failed:
            exec_fail_tasks.append(task_name)
        if state.diff_failed:
            diff_fail_tasks.append(task_name)

    return dict(
        failures=failures,
        task_times=task_times,
        result_strs=result_strs,
        exec_fail_tasks=exec_fail_tasks,
        diff_fail_tasks=diff_fail_tasks,
    )


def _log_schedule_summary(
    task, ordered_nodes: list[SchedulerNode], graph: SchedulerGraph
) -> None:
    selected_nodes = [
        node
        for node in ordered_nodes
        if node.selected and node.task_name == task.path
    ]
    print_to_stdout(task, '  scheduler: selected order')
    for index, node in enumerate(selected_nodes, start=1):
        print_to_stdout(
            task,
            f'    {index}. {node.task_name}/{node.step_name} '
            f'[{_summary_node_status(node)}; wait: '
            f'{_summary_wait_reason(graph.predecessors[node.key])}]',
        )


def _summary_node_status(node: SchedulerNode) -> str:
    if node.completed:
        return 'already completed'
    if node.cached:
        return 'cached'
    return 'pending'


def _summary_wait_reason(predecessor_keys) -> str:
    if predecessor_keys is None or len(predecessor_keys) == 0:
        return 'no_dependencies'
    return 'waiting_for_dependencies'


def _node_status(node: SchedulerNode) -> str:
    if node.completed:
        return 'already completed'
    if node.cached:
        return 'cached'
    return 'ready'


def _wait_reason(predecessor_keys) -> str:
    if predecessor_keys is None or len(predecessor_keys) == 0:
        return 'no_dependencies'
    return 'dependencies_satisfied'


def _emit_dask_runtime(recorder, runtime_info) -> None:
    payload = {
        field: value
        for field, value in asdict(runtime_info).items()
        if value is not None
    }
    payload['state'] = 'active'
    recorder.emit('dask_runtime', **payload)


def _emit_resource_feasibility(
    recorder,
    node,
    resource_pool,
    request=None,
    feasible=True,
    reason=None,
) -> None:
    payload = dict(
        task=node.task_name,
        step=node.step_name,
        feasible=feasible,
        result='feasible' if feasible else 'infeasible',
        wait_reason='resources_available'
        if feasible
        else 'insufficient_resources',
        free_cores=resource_pool.free_cores,
        free_nodes=resource_pool.free_nodes,
        free_gpus=resource_pool.free_gpus,
        total_cores=resource_pool.total_cores,
        total_nodes=resource_pool.total_nodes,
        total_gpus=resource_pool.total_gpus,
    )
    if request is not None:
        insufficient = _resource_shortfalls(request, resource_pool)
        payload.update(
            requested_cores=request.cores,
            requested_nodes=request.nodes,
            requested_gpus=request.gpus,
            minimum_cores=request.min_cores,
            minimum_nodes=request.min_nodes,
            minimum_gpus=request.min_gpus,
            insufficient=insufficient,
        )
    elif not feasible:
        payload['insufficient'] = ['request']
    if reason is not None:
        payload['reason'] = reason
    recorder.emit('resource_feasibility', **payload)


def _is_resource_request_feasible(request, resource_pool) -> bool:
    return (
        request.cores <= resource_pool.free_cores
        and request.nodes <= resource_pool.free_nodes
        and request.gpus <= resource_pool.free_gpus
    )


def _resource_shortfalls(request, resource_pool) -> list[str]:
    shortfalls = []
    if request.cores > resource_pool.free_cores:
        shortfalls.append('cores')
    if request.nodes > resource_pool.free_nodes:
        shortfalls.append('nodes')
    if request.gpus > resource_pool.free_gpus:
        shortfalls.append('gpus')
    return shortfalls


def _increment_active_counter(active_counter) -> None:
    if active_counter is not None:
        active_counter['active_steps'] += 1


def _decrement_active_counter(active_counter) -> None:
    if active_counter is not None and active_counter['active_steps'] > 0:
        active_counter['active_steps'] -= 1


def _active_count(active_counter) -> Optional[int]:
    if active_counter is None:
        return None
    return active_counter['active_steps']


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


def _add_step_outputs(
    output_providers: dict[str, list[str]], step: Any, key: str
) -> None:
    for output in step.outputs:
        output_path = _resolve_step_path(step, output)
        _add_output_provider(output_providers, output_path, key)


def _add_output_provider(
    output_providers: dict[str, list[str]], output_path: str, key: str
) -> None:
    providers = output_providers.setdefault(output_path, [])
    if key not in providers:
        providers.append(key)


def _find_dependency_key(
    dependency: Any,
    node: SchedulerNode,
    selected_step_keys: dict[str, list[str]],
    nodes: dict[str, SchedulerNode],
) -> Optional[str]:
    candidates = selected_step_keys.get(_step_identity(dependency), [])
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


def _step_identity(step: Any) -> str:
    return os.path.realpath(os.path.abspath(step.work_dir))


def _is_step_satisfied(step: Any) -> bool:
    return step.cached or _is_step_complete(step)


def _is_step_complete(step: Any) -> bool:
    complete_filename = os.path.join(
        step.work_dir, 'polaris_step_complete.log'
    )
    return os.path.exists(complete_filename)


def _resolve_step_path(step: Any, filename: str) -> str:
    if os.path.isabs(filename):
        return os.path.realpath(os.path.abspath(filename))
    return os.path.realpath(
        os.path.abspath(os.path.join(step.work_dir, filename))
    )
