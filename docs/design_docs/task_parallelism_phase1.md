# Task Parallelism in Polaris: Phase 1

Creation date: 2026/04/28

Contributors:

- Xylar Asay-Davis
- Codex

## Summary

Phase 1 introduces the future task-parallel execution path in Polaris without
yet enabling concurrent step execution. This phase adds a new command,
`polaris run`, that mirrors the current `polaris serial` command as closely as
practical for suites, tasks and individual steps. The new command should be
able to discover work from the same work directories, accept similar command
line options, and preserve the per-step execution semantics that users rely on.

Unlike `polaris serial`, `polaris run` is intended to become the foundation
for later task parallelism. In Phase 1, it should build the full scheduling
framework needed for future task-parallel execution, including dependency-graph
construction, ready-step selection, resource-aware scheduling, deterministic
step ordering, schedule summaries and metadata for future step eligibility.
However, the scheduler will still execute only one ready step at a time in
this phase.

Phase 1 therefore aims to prove that the new execution path is correct and
complete before it is asked to deliver speedup. The phase is expected to add
overhead, and some slowdown relative to `polaris serial` is acceptable. The
goal is not improved wall time in this phase, but rather that nearly all of
the infrastructure needed for Phase 2 is already in place, so Phase 2 can
focus primarily on enabling parallel execution of eligible non-MPI steps and
debugging any issues that arise.

`polaris serial` should remain unchanged and should continue to be the default
execution path recommended by `polaris setup` and `polaris suite`. Phase 1
should also provide an opt-in path for `polaris setup` and `polaris suite` to
set up tasks and suites that use `polaris run`. `polaris serial` should remain
a viable option indefinitely unless a later design decision shows that keeping
it unchanged would fundamentally block the task-parallel architecture.

Success in Phase 1 means that `polaris run`:

- works correctly for suites, tasks and steps,
- preserves per-step outputs, logs, completion markers and runtime input
  checking,
- produces final outputs that match task-serial baselines exactly,
- schedules steps from explicit dependencies and declared input/output file
  dependencies rather than from implicit serial order,
- rejects invalid dependency graphs before running,
- enforces minimum resource requirements even though it still runs one step at
  a time, and
- remains within an acceptable slowdown budget on representative suites such
  as `omega_pr`, `omega_nightly` and `mpaso_pr`, on the order of roughly
  40-50% slower than `polaris serial` but not much more.

## Requirements

### Requirement: New Task-Parallel Command Path

Date last modified: 2026/04/28

Contributors:

- Xylar Asay-Davis
- Codex

Phase 1 shall add a new execution command, `polaris run`, that provides the
future task-parallel command path for Polaris.

`polaris run` shall support suites, tasks and individual steps. Its command
line behavior shall mirror `polaris serial` as closely as practical, including
work-directory autodetection and relevant execution options such as selecting
or skipping steps and controlling output verbosity.

`polaris serial` shall remain available and unchanged in Phase 1. The default
job scripts, setup behavior and user guidance produced by `polaris setup` and
`polaris suite` shall continue to use `polaris serial`.

`polaris setup` and `polaris suite` shall provide an opt-in way to set up
tasks and suites to use `polaris run`. Task-serial setup shall remain the
default in Phase 1.

### Requirement: Backward-Compatible Per-Step Execution Semantics

Date last modified: 2026/04/23

Contributors:

- Xylar Asay-Davis
- Codex

When `polaris run` executes a step, it shall preserve the same per-step
semantics users expect from `polaris serial`.

This includes the same step inputs and outputs, the same completion markers,
the same per-step logs, the same restart and rerun behavior, and the same
runtime failures when required inputs are missing. `polaris run` shall not
attempt to infer missing dependencies from runtime failures or recover by
guessing undeclared inputs.

Phase 1 does not require `polaris run` to preserve the same suite-level
stdout ordering or the same task/step order that `polaris serial` happened to
use, as long as the chosen order is valid with respect to the dependency graph
and resources.

### Requirement: Task-Parallel Output Equivalence

Date last modified: 2026/04/28

Contributors:

- Xylar Asay-Davis
- Codex

Phase 1 shall produce the same final outputs as task-serial execution for
deterministic Polaris workflows.

Representative suites shall be compared against baselines produced by
task-serial execution using the existing Polaris baseline-comparison
capability. This requirement ensures that `polaris run` preserves outputs
before Phase 2 enables concurrent execution.

### Requirement: Explicit Dependency-Graph Scheduling

Date last modified: 2026/04/23

Contributors:

- Xylar Asay-Davis
- Codex

`polaris run` shall construct a dependency graph before executing work. The
graph shall be based on explicit step dependencies and on declared
input/output-file dependencies.

Implicit dependence on the order of `steps_to_run` shall not be treated as a
source of truth for scheduling. If an existing suite or task relied on serial
ordering without declaring a true dependency, it is acceptable for `polaris
run` to expose that bug.

Before starting execution, `polaris run` shall reject invalid dependency
graphs, including cycles and cases where declared dependencies cannot be
satisfied.

### Requirement: Deterministic Ready-Step Selection

Date last modified: 2026/04/23

Contributors:

- Xylar Asay-Davis
- Codex

When more than one step is ready to run, `polaris run` shall choose among the
ready steps deterministically.

The deterministic choice does not need to match the step order previously used
by `polaris serial`, but repeated runs with the same configured work and the
same available resources should choose the same valid execution order. This
requirement is intended to improve debuggability and make schedule summaries
easy to interpret.

### Requirement: Resource-Aware Scheduling and Enforcement

Date last modified: 2026/04/28

Contributors:

- Xylar Asay-Davis
- Codex

Phase 1 shall include the resource-aware scheduling capabilities needed for
future task-parallel execution, even though the scheduler will still execute
only one ready step at a time.

`polaris run` shall take step resource requirements into account when making
scheduling decisions, and it shall enforce minimum resource requirements. If a
step cannot meet its minimum resource requirements, `polaris run` shall fail
rather than silently weakening those requirements below the existing supported
minimum.

The Phase 1 scheduler shall therefore be prepared to support task-parallel
execution later without requiring its core resource-accounting model to be
replaced in Phase 2. Steps expected to be eligible for parallel scheduling in
Phases 1 and 2 shall have enough resource information for Phase 2 to schedule
them concurrently without oversubscribing the available allocation.

### Requirement: Single-Step Execution in Phase 1

Date last modified: 2026/04/23

Contributors:

- Xylar Asay-Davis
- Codex

Although Phase 1 shall build the scheduling framework for future task
parallelism, it shall execute only one ready step at a time.

This requirement applies even when multiple independent steps are ready and
their combined resource requirements would allow future concurrent execution.
The purpose of Phase 1 is to validate the new command path, scheduler,
dependency graph and observability without yet enabling task parallelism
itself.

### Requirement: Future Parallel-Eligibility Metadata

Date last modified: 2026/04/23

Contributors:

- Xylar Asay-Davis
- Codex

Phase 1 shall introduce the step metadata needed to support later phases in
which only certain classes of work are eligible for concurrent execution.

The metadata shall provide a path for future designs to distinguish steps that
are safe for the first task-parallel phases from steps that should remain
serialized or require different handling. Defining the exact software
mechanism for this metadata is part of algorithm design and implementation,
not a requirement.

### Requirement: Observable Execution and Schedule Summaries

Date last modified: 2026/04/23

Contributors:

- Xylar Asay-Davis
- Codex

Phase 1 shall provide the schedule and resource summaries expected to be
useful in later task-parallel phases, even though execution remains
single-step in this phase.

The summary shall make it possible for users and developers to determine the
dependency-validated execution order selected by `polaris run`, the resource
reasoning associated with that schedule, and the timing information needed to
compare the new command path with `polaris serial`.

The observability added in Phase 1 should be sufficient to support debugging
and performance evaluation in Phase 2 without requiring a separate redesign of
run summaries.

### Requirement: Cross-Machine Phase-1 Functionality

Date last modified: 2026/04/23

Contributors:

- Xylar Asay-Davis
- Codex

Phase 1 shall function correctly on the supported execution environments that
matter most for later task parallelism, including Chrysalis, Perlmutter and
Aurora.

Validation will inevitably focus on representative suites and machines rather
than exhaustive testing of every suite on every platform. Even so, the Phase 1
goal is that `polaris run` should work correctly for all existing suites, not
just for a small pilot subset.

Representative validation and performance comparisons shall include at least
`omega_pr`, `omega_nightly` and `mpaso_pr`. On these suites, the slowdown of
`polaris run` relative to `polaris serial` should remain within an acceptable
budget, on the order of roughly 40-50% and not much more.

### Desired: Frontier Support

Date last modified: 2026/04/23

Contributors:

- Xylar Asay-Davis
- Codex

Frontier support and validation in Phase 1 would be valuable, even if it is
not required.

### Desired: Task-serial Summary

Date last modified: 2026/04/23

Contributors:

- Xylar Asay-Davis
- Codex

Comparable schedule and resource summaries for `polaris serial` would be
useful for side-by-side debugging, even though they are not required in
Phase 1.
