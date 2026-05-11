# Task Parallelism in Polaris

Creation date: 2026/04/23

Contributors:

- Xylar Asay-Davis
- Codex

## Summary

This design document describes the overall direction for adding task
parallelism to Polaris. In this context, task parallelism means running
independent Polaris steps concurrently when their dependencies have been
satisfied and sufficient resources are available. The long-term goal is for
Polaris to make better use of multi-node HPC allocations by overlapping
independent work, while preserving the correctness, restart behavior and user
experience of existing serial execution.

The first priority is to support parallel execution of explicitly safe
non-MPI steps, including steps implemented primarily in Python. These steps
should be able to run concurrently across more than one node in an HPC
allocation. Polaris should also support realistic mixed workflows containing
both non-MPI and MPI steps. In the initial mixed-workflow capability, MPI steps
will run one at a time and will not overlap with non-MPI steps.

Task parallelism should be introduced as an opt-in capability. Existing
`polaris serial` behavior remains the compatibility baseline until task
parallelism is mature enough to consider broader use. A successful design will
therefore allow Polaris to add the scheduling and resource-management framework
without changing existing results or step behavior, then progressively enable
more concurrent execution.

This umbrella document intentionally keeps implementation choices high level.
Specific APIs, scheduler architecture, worker topology, MPI launch mechanisms,
and machine-specific details should be developed in phase-specific design
documents. Prior discussion of possible implementation directions is useful
background, but this document treats those ideas only as context rather than as
selected architecture.

The intended development phases are:

1. Add a parallel-ready framework that still runs task-serial and proves no
   regression relative to existing behavior.
2. Enable parallel execution of explicitly safe non-MPI steps, with mixed
   workflows supported by barriered, one-at-a-time MPI execution.
3. Enable concurrent execution of independent MPI steps when dependencies and
   resources allow.
4. Enable concurrent execution of non-MPI and MPI steps within the same
   allocation when resources can be safely partitioned.

## Requirements

### Requirement: Backward-Compatible Parallel-Ready Framework

Date last modified: 2026/04/23

Contributors:

- Xylar Asay-Davis
- Codex

Polaris shall gain the scheduling and resource-management framework needed for
future task parallelism without changing existing task-serial behavior during
the first phase of development.

The framework shall preserve the results, dependency behavior, restart
behavior, logging expectations and completion markers of existing Polaris
steps. Existing workflows that run with `polaris serial` shall continue to run
in the same order and with the same semantics unless a user explicitly selects
a task-parallel execution mode.

The first phase shall therefore prove that the new framework does no harm
before using it to run independent steps concurrently.

### Requirement: Dependency-Constrained Ready-Step Scheduling

Date last modified: 2026/04/23

Contributors:

- Xylar Asay-Davis
- Codex

In opt-in task-parallel mode, Polaris shall be able to run any selected step
when all of its dependencies are satisfied and the resources needed by the
step are available.

The listed order of tasks or steps shall not, by itself, prevent independent
steps from running concurrently or from running in a different order. Ordering
shall be determined by dependencies, resource availability, user-selected
steps, skipped steps, cached steps and completed steps.

If a step fails, Polaris shall prevent dependent steps from running. Completed
steps shall remain skippable on rerun, and users shall be able to resume from
work that completed successfully before the failure.

### Requirement: Eligible Non-MPI Step Parallelism

Date last modified: 2026/04/23

Contributors:

- Xylar Asay-Davis
- Codex

Polaris shall support concurrent execution of explicitly safe non-MPI steps
across a multi-node HPC allocation. This includes steps implemented primarily
in Python.

Eligibility for non-MPI task parallelism shall be conservative at first. Steps
that are unsafe, unclassified or not known to behave correctly when run
concurrently shall not run concurrently by default. The capability shall
provide a path for step authors to identify steps that are safe to run in
parallel with other eligible steps.

The capability shall support concurrent execution across more than one node,
not only within a single shared-memory node.

### Requirement: CPU-Aware Resource Scheduling

Date last modified: 2026/04/23

Contributors:

- Xylar Asay-Davis
- Codex

Eligible non-MPI steps shall be able to express both target and minimum CPU
core requirements. The requirements should describe the amount of CPU resource
needed by the step without forcing non-MPI work into MPI-specific terminology.

Task-parallel execution shall avoid CPU oversubscription when scheduling
eligible steps. If the target CPU resources for all ready steps cannot fit in
the available allocation, Polaris shall be able to run a subset of ready steps
and defer the rest until resources become available. If a step's minimum CPU
requirement cannot be met, Polaris shall report that the step cannot be run
with the available resources.

The initial implementation is not required to schedule by memory, but the
framework shall not preclude later memory-aware scheduling. A later phase shall
allow steps to express memory requirements so Polaris can avoid combinations
of concurrent steps that would exceed available memory.

### Requirement: Mixed Non-MPI and MPI Workflows

Date last modified: 2026/04/23

Contributors:

- Xylar Asay-Davis
- Codex

Polaris shall support realistic workflows that contain both eligible non-MPI
steps and MPI steps.

In the initial mixed-workflow phase, eligible non-MPI steps may run in
parallel with one another, but MPI steps shall run one at a time. No non-MPI
steps shall be active while an MPI step is running in this phase. This
barriered execution model shall allow workflows to alternate between parallel
non-MPI phases and task-serial MPI phases.

The barriered mixed-workflow capability shall preserve dependency correctness
and shall avoid designing out later phases in which MPI steps can overlap with
other work.

### Requirement: Concurrent MPI Step Execution

Date last modified: 2026/04/23

Contributors:

- Xylar Asay-Davis
- Codex

A later phase shall allow independent MPI steps to run concurrently when their
dependencies are satisfied and sufficient resources are available.

Concurrent MPI execution shall preserve step correctness, avoid resource
oversubscription and provide enough isolation that independent MPI steps do
not interfere with one another. If resources are insufficient for concurrent
MPI execution, Polaris shall still be able to run MPI steps serially.

### Requirement: Concurrent Non-MPI and MPI Execution

Date last modified: 2026/04/23

Contributors:

- Xylar Asay-Davis
- Codex

A later phase shall allow eligible non-MPI steps and MPI steps to run
simultaneously within the same HPC allocation when Polaris can safely allocate
non-overlapping resources to each kind of work.

This capability shall preserve dependency correctness, avoid CPU and memory
oversubscription, and allow Polaris to prevent or diagnose resource
interference between concurrent non-MPI and MPI work.

### Requirement: Portable and Observable HPC Execution

Date last modified: 2026/04/23

Contributors:

- Xylar Asay-Davis
- Codex

Task-parallel execution shall be portable in concept across multi-node HPC
systems. Development and validation may proceed in stages, with Chrysalis as
the initial target, Perlmutter as a subsequent Slurm system with different
operational constraints, and Aurora as a later PBS-based system.

Task-parallel runs shall record enough information for users and developers to
understand what happened during the run. This shall include step timing,
evidence of step overlap, node and core use, comparison with task-serial wall
time when appropriate, failure behavior and resume behavior.

## Testing

### Testing and Validation: Backward-Compatible Parallel-Ready Framework

Date last modified: 2026/04/23

Contributors:

- Xylar Asay-Davis
- Codex

Unit and integration tests shall verify that adding the parallel-ready
framework does not change task-serial execution. Existing tests and
representative workflows shall continue to produce the same outputs,
completion markers, dependency pickles and logs when run in serial mode.

Regression tests shall include rerunning workflows with already completed
steps to confirm that completed steps remain skippable and that restart
behavior is unchanged.

### Testing and Validation: Dependency-Constrained Ready-Step Scheduling

Date last modified: 2026/04/23

Contributors:

- Xylar Asay-Davis
- Codex

Unit tests shall cover dependency graph construction, ready-step selection,
skipped steps, cached steps, completed steps, failed steps and resume behavior.
Synthetic workflows shall include independent branches, shared dependencies
and dependent steps that must not begin until their prerequisites complete.

Integration tests shall include synthetic steps that sleep, produce outputs,
consume outputs from dependencies and fail intentionally. These tests shall
verify that independent steps can run out of listed order, that failed steps
block dependents and that reruns resume from completed work.

### Testing and Validation: Eligible Non-MPI Step Parallelism

Date last modified: 2026/04/23

Contributors:

- Xylar Asay-Davis
- Codex

Unit tests shall verify that only explicitly eligible non-MPI steps can run
concurrently in the initial implementation and that unsafe or unclassified
steps are not scheduled concurrently by default.

Integration and HPC tests shall use controlled synthetic non-MPI steps to
demonstrate concurrent execution across multiple nodes. The tests shall record
step timing and overlap and shall show measurable wall-time improvement
relative to serial execution without requiring a fixed speedup threshold.

Representative real Polaris workflows shall be added after the synthetic tests
are reliable, so the capability is tested on workloads that resemble
production use.

### Testing and Validation: CPU-Aware Resource Scheduling

Date last modified: 2026/04/23

Contributors:

- Xylar Asay-Davis
- Codex

Unit tests shall verify resource accounting for target and minimum CPU
requirements, including cases where all ready steps fit, only a subset can run
concurrently and a step cannot meet its minimum CPU requirement.

Integration tests shall use synthetic CPU-consuming steps with different
resource requirements to confirm that task-parallel execution avoids CPU
oversubscription. Future tests shall cover memory-aware scheduling when memory
requirements become part of the implementation.

### Testing and Validation: Mixed Non-MPI and MPI Workflows

Date last modified: 2026/04/23

Contributors:

- Xylar Asay-Davis
- Codex

HPC tests shall include mixed workflows with a parallel non-MPI phase, a
barriered MPI step and a second non-MPI phase. These tests shall verify that
non-MPI steps can overlap with one another, that the MPI step runs by itself
and that later non-MPI work does not begin until the MPI step and its
dependencies are complete.

The tests shall include both controlled synthetic workloads and at least one
representative Polaris workflow once suitable real workloads are identified.

### Testing and Validation: Concurrent MPI Step Execution

Date last modified: 2026/04/23

Contributors:

- Xylar Asay-Davis
- Codex

Future phase-specific tests shall include independent MPI steps whose combined
resource requirements fit within the allocation and MPI steps whose combined
requirements do not fit. These tests shall verify correct dependency behavior,
resource isolation and fallback to serial MPI execution when concurrent MPI
execution is not possible.

HPC validation shall record timing, resource allocation and any launch-system
limitations that affect whether concurrent MPI execution is viable on each
target system.

### Testing and Validation: Concurrent Non-MPI and MPI Execution

Date last modified: 2026/04/23

Contributors:

- Xylar Asay-Davis
- Codex

Future phase-specific tests shall include workflows where eligible non-MPI
steps and MPI steps run simultaneously using non-overlapping resources. These
tests shall verify correctness, resource accounting, failure propagation and
the ability to diagnose interference when concurrent execution is unsafe or
misconfigured.

The tests shall compare simultaneous execution with the earlier barriered
mixed-workflow behavior so developers can evaluate correctness and performance
benefits.

### Testing and Validation: Portable and Observable HPC Execution

Date last modified: 2026/04/23

Contributors:

- Xylar Asay-Davis
- Codex

HPC validation shall begin on Chrysalis and later expand to Perlmutter and
Aurora. Each system-specific validation shall record the scheduler
environment, node and core allocation, step timing, step overlap, failure
behavior, resume behavior and serial-vs-parallel wall-time comparison where
appropriate.

The exact workflows and machine-specific settings should be chosen in
phase-specific design documents, but this umbrella design requires enough
observability that failures, performance regressions and resource-allocation
mistakes can be understood after a run.
