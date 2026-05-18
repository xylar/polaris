import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional


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
