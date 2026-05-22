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
It should also establish the long-lived Dask Distributed orchestration layer
that Phase 2 will use for concurrent non-MPI execution. However, the scheduler
will still execute only one ready step at a time in this phase.

The phrase "task parallelism" is the historical project label, but the Polaris
scheduling unit is a `Step`. Later phases are intended to run independent
selected steps concurrently, potentially drawn from one task or from multiple
tasks in a suite. They are not intended to treat whole Polaris `Task` objects
as the indivisible unit of parallel execution.

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

Date last modified: 2026/05/14

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

Date last modified: 2026/05/14

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

The single ready step shall run through the `polaris run` orchestration path,
including the selected execution backend where applicable, so Phase 1
validates worker lifecycle, serialization, logging and cleanup before
concurrency is enabled.

### Requirement: Future Parallel-Eligibility Metadata

Date last modified: 2026/05/14

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

The metadata shall allow Polaris to derive whether a step should be treated as
MPI or non-MPI from existing step information, while also allowing an explicit
override for ambiguous steps. Non-MPI steps shall have a way to be marked
unsafe or ineligible for task-parallel execution.

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

## Algorithm Design

### Algorithm Design: New Task-Parallel Command Path

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

`polaris run` should be designed as the permanent task-parallel command path,
not as a thin alias for `polaris serial`. It should share the same setup
artifacts, pickle files, work-directory discovery and user-facing scope as
`polaris serial`, but it should route execution through a scheduler that owns
the dependency graph, resource pool and step lifecycle.

Phase 1 should stand up an allocation-scoped Dask Distributed environment
before executing work. This environment should contain one Dask scheduler for
the run and multiple single-threaded Dask worker processes per allocated node.
All allocated nodes, including the node that hosts the scheduler and
orchestrator, should be eligible to host workers. Dask Distributed is therefore
a runtime dependency of `polaris run`.

Polaris should control the Dask scheduler and worker lifecycle directly rather
than using `dask-mpi` as the primary orchestration mechanism. This keeps
resource handoff policy in Polaris, avoids making the batch scheduler
responsible for many small Python tasks, and leaves room for scheduler-specific
MPI launch behavior to remain isolated in later phases.

### Algorithm Design: Backward-Compatible Per-Step Execution Semantics

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

Each scheduled step should still use the existing Polaris step lifecycle:
runtime input checks, dependency loading, resource constraining,
`runtime_setup()`, `run()` or command-line argument execution, output checks,
baseline/property validation and completion markers. The scheduler should
decide when a step is eligible to begin, but it should not change what it
means for the step to run successfully.

For atomic steps, the whole Polaris step is the scheduling unit. Phase 1
should submit only one such step to Dask at a time. The step may run in a
worker process, but it should preserve the same working directory, logging,
environment and completion semantics as existing task-serial execution.

Dask-aware non-MPI steps should be introduced with a separate execution hook
rather than by changing the meaning of `run()`. A Dask-aware step should run
coordinating code under the Polaris orchestrator with an assigned Dask client
and resource lease, and that coordinating code may submit internal Dask work.
This hook gives large Python steps a path to use multiple workers without
changing ordinary step semantics.

### Algorithm Design: Task-Parallel Output Equivalence

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

The algorithmic goal in Phase 1 is semantic equivalence, not speedup. Since
only one step runs at a time, output differences relative to `polaris serial`
should indicate a bug in dependency construction, Dask-backed execution,
resource setup, logging side effects, or step lifecycle preservation.

Baseline comparison should remain a post-step validation action. Completed
steps should record the same pass/fail markers used by `polaris serial`, so
reruns and suite summaries can aggregate existing results without rerunning
completed work.

### Algorithm Design: Explicit Dependency-Graph Scheduling

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

Before running any step, `polaris run` should build a directed acyclic graph
from existing setup metadata. Graph edges should come from:

- explicit `step.dependencies`, and
- resolved input/output file relationships where one selected step consumes a
  file produced by another selected step.

The listed `steps_to_run` order should be used only as a deterministic
tie-breaker, not as an implicit dependency source. If a selected input file is
produced by no selected step and does not already exist as an external input,
the graph should be rejected before execution. Cycles should also be rejected
before execution.

Cached and already completed steps should participate in graph validation.
They should be treated as satisfied nodes when their expected completion or
cached-output evidence is present, but their outputs should still be available
for dependency reasoning.

### Algorithm Design: Deterministic Ready-Step Selection

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

The scheduler should maintain a stable ordering derived from setup order:
suite order, task order and step order within each task. When multiple steps
are ready, this order should be the primary tie-breaker after dependency and
resource constraints. This rule is simple to explain, repeatable across runs,
and close enough to existing user expectations to make debugging tractable.

Phase 1 should record the selected order even though only one step runs at a
time. Phase 2 can reuse the same ordering when packing multiple ready non-MPI
steps into the available resource pool.

### Algorithm Design: Resource-Aware Scheduling and Enforcement

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

Phase 1 should introduce the same dynamic resource-pool model that later
phases will use, even though only one step can hold resources at a time. The
pool should track at least nodes, physical CPU cores and GPUs when available.
Hyperthreads should not increase schedulable CPU capacity by default.

For Phases 1 and 2, Polaris should reserve resources and avoid logical
oversubscription but should not require hard CPU affinity or pinning. Affinity
can become a machine-specific hardening feature if validation shows that
resource interference cannot otherwise be controlled.

For non-MPI atomic steps that do not opt in to Dask-aware execution,
`cpus_per_task` and `min_cpus_per_task` should be interpreted as the target
and minimum core reservation for the step. `ntasks` and `min_tasks` should
remain MPI-oriented fields. Non-MPI steps that need multiple workers should
use the Dask-aware step hook and Dask-specific worker metadata rather than
treating MPI task counts as non-MPI worker counts. The active execution
backend determines which resource request is reserved: ordinary execution
reserves the ordinary step resources, while Dask-aware execution reserves the
CPU cores needed by the assigned Dask workers.

### Algorithm Design: Single-Step Execution in Phase 1

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

The Phase 1 scheduling policy should be a single-active-step policy layered on
top of the future task-parallel scheduler. The scheduler should repeatedly:

- identify ready graph nodes,
- filter them by completion, cached status and resource feasibility,
- choose the first ready step in deterministic order,
- reserve resources for that step,
- execute it through the `polaris run` Dask orchestration path, and
- release resources after the step succeeds or fails.

No second step should be started while another step is active, even if it is
independent and enough resources remain idle. This intentionally leaves
parallel speedup for Phase 2 while proving that the scheduler, resource pool
and Dask execution path are already real.

### Algorithm Design: Future Parallel-Eligibility Metadata

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

Step metadata should distinguish execution kind from resource size. Polaris
should derive a default execution kind from existing information, such as
whether a step uses MPI-style resources or launches through the component
parallel command path. Ambiguous or special steps should be able to override
the derived kind explicitly.

Non-MPI steps should be eligible for Phase 2 concurrent execution by default.
Step authors should be able to mark a non-MPI step as unsafe or ineligible
when it has shared mutable state, external side effects, uncontrolled process
launching, or other behavior that makes concurrent execution inappropriate.

MPI steps and ineligible non-MPI steps should remain serialized in Phase 2.
Later phases may invert or refine defaults for MPI steps, but they should use
the same execution-kind metadata rather than adding a separate classification
system.

### Algorithm Design: Observable Execution and Schedule Summaries

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

Phase 1 observability should have both structured and human-readable forms.
The human-readable output should summarize selected order, wait reasons,
resource reservations, step timing, completion state and final results. The
structured output should record schedule/resource events so Phase 2 and later
debugging can reconstruct what happened without scraping free-form logs.

Structured events should include at least graph construction, ready-step
selection, resource reservation, Dask worker-pool state, step start, step
finish, failure and resource release. These events should be sufficient to
verify that Phase 1 did not accidentally run steps concurrently and that Phase
2 does run eligible steps concurrently.

### Algorithm Design: Cross-Machine Phase-1 Functionality

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

The Phase 1 algorithm should assume the batch scheduler provides a fixed
allocation, while Polaris manages Dask worker resources inside that allocation.
Machine-specific differences should be confined to allocation discovery, job
script generation, worker launch details and later MPI launch behavior.

On Slurm systems such as Chrysalis and Perlmutter, the design should avoid
using a scheduler-launched job step for every small Python step. On PBS-based
systems such as Aurora, the same allocation-scoped Dask model should remain
the conceptual target even if worker launch details differ.

### Algorithm Design: Frontier Support

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

N/A for Phase 1. Frontier validation is desired, but it does not change the
core Phase 1 algorithm beyond the cross-machine portability choices described
above.

### Algorithm Design: Task-serial Summary

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

Comparable `polaris serial` summaries should use the same schedule-summary
concepts where practical: selected steps, timing, completion state and final
validation status. They do not need Dask worker or resource-pool events.

## Implementation

### Implementation: New Task-Parallel Command Path

Date last modified: 2026/05/18

Contributors:

- Xylar Asay-Davis
- Codex

The first implementation chunk shall declare Dask Distributed as a Polaris
runtime dependency. The pixi deployment template is the source of truth for
the development and supported deployment environments, so `dask` and
`distributed` shall be added to `deploy/pixi.toml.j2`.

Matching entries shall also be added to `pyproject.toml` so package metadata
and `pip check` can verify that the Python dependency set is complete. Polaris
is not expected to run from PyPI dependencies alone; the `pyproject.toml`
entries are a consistency check rather than the deployment source of truth.

This chunk shall not introduce `polaris run`, Dask lifecycle management, or
changes to `polaris serial`. Those will be implemented in later, separately
reviewed commits.

The second implementation chunk shall extract command-independent pieces of
the existing `polaris serial` implementation into shared run infrastructure.
The extracted helpers shall cover suite unpickling, runtime config setup,
dependency loading, completion markers, step-list selection, task logging and
status accumulation, completed-step validation marker reads, per-step
lifecycle execution, subprocess step execution and pull-request summary
generation.

`polaris serial` shall import and use these shared helpers without changing
its command line, work-directory discovery or runtime behavior. This chunk is
intended to make the next `polaris run` commit small: the new command should
be able to reuse the same task and step lifecycle helpers rather than
depending on private functions inside `polaris.run.serial`.

The third implementation chunk shall add a thin `polaris run` command
skeleton. The command shall be wired into the top-level Polaris CLI and shall
mirror `polaris serial` work-directory discovery for suites, tasks and single
steps. In this chunk, `polaris run` shall still execute task-serially through
the shared task and step lifecycle helpers; it shall not yet start a Dask
client, build a dependency graph or change setup-generated job scripts.

The fourth implementation chunk shall add the first Dask Distributed
lifecycle wrapper for `polaris run`. The command shall start one local Dask
cluster and client for each suite, task or single-step run, using
single-threaded workers capped to the local node's available core count. This
chunk shall not yet launch workers across all nodes in a multi-node
allocation; parallel-system-aware multi-node worker launch shall come with the
later scheduler and resource-management work. The client shall be passed
through the shared run helpers so later Dask-aware hooks can use it, but this
chunk shall keep the existing task-serial step loop and shall not run more
than one Polaris step at a time. The Dask client and cluster shall be closed
on normal completion and on failure.

The fifth implementation chunk shall add conservative step execution metadata
and the first Dask-aware step hook. Each step shall have an execution kind
that is derived as MPI when the step requests more than one task, requires
more than one task, or uses command-line parallel arguments. Step authors may
override the derived execution kind when the conservative default is wrong.
Non-MPI steps shall be considered eligible for future concurrent scheduling by
default, with an opt-out flag for steps that are not safe to run concurrently.

This chunk shall also add Dask-specific worker metadata to each step and a
lightweight step resource lease with assigned cores and Dask workers, plus
optional fields for later node, GPU and memory accounting. Ordinary resource
fields such as `cpus_per_task` shall describe the non-Dask execution resource
request. `dask_workers` and `min_dask_workers` shall describe the Dask-aware
execution resource request, and those workers still represent CPU cores that
must be reserved while the Dask-aware step is active. When `polaris run`
executes a Python step with a Dask client, it shall call
`run_with_dask(client, resources)`. The default implementation of that hook
shall fall back to `run()`, so ordinary step behavior remains unchanged unless
a step opts in to Dask-aware behavior.

The sixth implementation chunk shall convert the WOA23 hydrography combine
step into the first Dask-aware pilot. The ordinary `run()` path shall continue
to combine the January and annual WOA climatologies and convert TEOS-10 fields
serially. The new `run_with_dask()` path shall keep the same input/output
contract but submit conservative-temperature and absolute-salinity conversion
work per depth slice to the active Dask client, then gather and reassemble
results in deterministic depth order before writing `woa_combined.nc`.

The seventh implementation chunk shall add the first scheduler graph module
without routing `polaris run` through it yet. The module shall inventory
selected task steps in stable suite/task/step order, preserve cached and
already completed selected steps as graph nodes, add directed edges from
explicit `Step.dependencies`, and reject graphs with cycles. If a selected
step has an explicit dependency that is not itself selected, graph
construction shall require that dependency to already be satisfied by cache or
an existing completion marker and shall keep that satisfied dependency as an
unselected graph participant.

The eighth implementation chunk shall extend scheduler graph construction to
derive dependency edges from declared step input and output files. File paths
shall be resolved to absolute paths before matching. If a selected step
consumes a file produced by another selected step, the producing step shall be
a graph predecessor regardless of the selected-step order. Existing input
files with no selected producer shall be treated as external satisfied inputs.
Missing declared inputs shall be rejected unless they are produced by a
selected step or by an unselected cached or already completed step that can be
kept in the graph as a satisfied participant.

The ninth implementation chunk shall introduce a scheduler resource-pool
model for the existing `available_resources` metadata. The pool shall track
logical nodes, CPU cores and GPUs and shall support reserving and releasing
step resource requests. Step resource requests shall be derived without
mutating the step, using the same feasibility constraints as
`Step.constrain_resources()` for MPI availability, CPU core limits and GPU
limits. This chunk shall not change the existing per-step
`constrain_resources()` lifecycle call; it shall make the scheduler able to
reject impossible minimum CPU/GPU requirements before starting a step and to
account for reservations in later scheduler integration commits.

The tenth implementation chunk shall route task-scope `polaris run` through
the scheduler while keeping suite-scope `polaris run`, `polaris serial` and
single-step runs unchanged. The task scheduler shall build the dependency
graph, choose selected steps in deterministic topological order, skip cached
and already completed selected steps, reserve one step's resources at a time,
execute that step through the existing shared lifecycle helpers and release
the reservation in a `finally` block. This preserves the Phase 1 single-active
step policy while proving that task-scope `polaris run` now depends on graph
validity and resource feasibility rather than on a serial selected-step loop.

The eleventh implementation chunk shall route suite-scope `polaris run`
through the same per-task scheduler runner while preserving the existing suite
task loop, per-task logs and aggregate pass/fail accounting. Each scheduled
task shall write a human-readable selected-order summary to its normal task
output and a `schedule_events.jsonl` file in the task work directory. The
structured events shall record graph construction, ready selection, skipped
cached/completed steps, resource reservation, step start, step finish/failure
and resource release. These events shall include an active-step count so tests
and developers can verify that Phase 1 keeps only one Polaris step active at
a time. This chunk shall continue to use the existing Dask lifecycle
abstraction; replacing the current local Dask deployment with an
allocation-scoped scheduler and workers remains later Phase 1 work.

The twelfth implementation chunk shall add an explicit setup-time opt-in for
generated job scripts to use `polaris run`. The `polaris setup` and
`polaris suite` commands shall accept `--run_command` with choices `serial`
and `run`, defaulting to `serial`. Generated job scripts shall continue to run
`polaris serial` unless the user opts in to `--run_command run`, in which case
task and step scripts shall run `polaris run` and suite scripts shall run
`polaris run <suite>`. This chunk shall reuse the existing
`write_job_script()` custom-command hook rather than changing the default
behavior of direct `write_job_script()` calls.

The thirteenth implementation chunk shall abstract the Dask runtime backend
behind a small backend-selection API. The default backend remains the local
`distributed.LocalCluster` lifecycle from earlier Phase 1 work, with
single-threaded workers capped to the local available core count. The
selected backend shall attach structured runtime metadata to the Dask client
so scheduler summaries can record the backend name and worker count without
assuming a specific client implementation. Unknown backend names shall be
rejected explicitly; allocation-scoped planning and launch remain later
Phase 1 work.

The fourteenth implementation chunk shall add an allocation-scoped Dask
launch-plan model without replacing the current local runtime lifecycle. The
plan shall place the Dask scheduler on logical node 0, distribute
single-threaded Dask workers across allocation nodes according to
`cores_per_node`, preserve GPU-per-node metadata when it is available, and
mark single-node or unsupported launch situations as local fallbacks. This
chunk shall validate launch planning from the existing `available_resources`
metadata only; actual scheduler and worker process launch remains later Phase
1 work.

The fifteenth implementation chunk shall add the first multi-node-capable
Dask runtime lifecycle behind the backend abstraction. The automatic backend
selector shall use the allocation backend when the launch plan is multi-node
and a process launcher is available from the active `mache` parallel system;
otherwise it shall preserve the local backend fallback. The allocation backend
shall launch a Dask scheduler process, wait for scheduler connection metadata,
launch Dask workers through a pluggable launcher, create a Dask client from
the scheduler metadata and clean up the client, workers and scheduler on
normal completion or failure. Unit tests shall use fake launchers and clients;
real multi-node validation remains a manual/system activity.

The sixteenth implementation chunk shall add suite-wide scheduler execution.
Suite-scope `polaris run` shall prepare all selected task step lists, build
one scheduler graph across all selected tasks, preserve explicit and
file-derived dependencies across task boundaries, and execute selected nodes
from that suite graph in deterministic topological order. Phase 1 shall still
run only one Polaris step at a time. Per-task logs, completion markers,
structured schedule-event files and aggregate suite pass/fail summaries shall
remain available.

The seventeenth implementation chunk shall harden schedule observability and
failure semantics. The human-readable selected-order summary and
`schedule_events.jsonl` files shall include wait reasons, resource-feasibility
decisions, skip reasons, result status, Dask backend state and active-step
counts. Suite-wide scheduling shall not run a selected step after one of its
dependencies has failed or been blocked. Instead, the dependent step shall be
marked as blocked so the failure cause remains visible without confusing it
with an independent execution failure. Tests shall verify failures,
completed-step reruns, cached steps, blocked dependents and the Phase 1
single-active-step policy across a suite graph.

The eighteenth implementation chunk shall add lightweight Phase 1 validation
helpers and documentation. These helpers shall parse scheduler event files,
summarize whether the scheduler and Dask orchestration paths were used, and
verify that active-step counts do not exceed the Phase 1 single-step policy.
Documentation shall describe how to compare `polaris run` with
`polaris serial`, where to find scheduler artifacts and which heavy
machine-specific checks remain manual/system validation.

The nineteenth implementation chunk shall harden shared-step scheduling
semantics. When the same underlying step work directory is selected through
multiple tasks, repeated `Step` object references or symlinked aliases, the
scheduler graph shall represent that producer once. Downstream explicit
dependencies and declared input/output-file dependencies shall point to the
canonical selected producer so the shared step runs once while all consumers
still wait for it.

The twentieth implementation chunk shall harden cached and completed-step
rerun semantics. Cached and already completed selected steps shall remain
first-class graph nodes with explicit skip events that say they satisfy
dependencies. Already completed steps shall report existing baseline and
property marker status in the structured schedule events. Selected downstream
steps shall continue to run after cached or completed producers, and steps
that do run shall continue to create completion markers, validation markers
and `step_after_run.pickle` files through the existing shared lifecycle.

The twenty-first implementation chunk shall harden failure and blocked
dependency semantics. Task-scope and suite-scope scheduler runs shall both
record failed steps, release their resource reservations, block selected
dependent steps and keep independent ready steps eligible to run. Task-scope
runs shall still raise the original execution failure after the scheduler has
recorded blocked dependents, preserving the existing caller-facing failure
contract. The Dask runtime context shall continue to clean up when the
scheduler path raises.

The twenty-second implementation chunk shall improve resource and backend
diagnostics. Resource feasibility events shall report whether a request is
feasible or infeasible, resource wait reason, free and total resource counts,
minimum and requested resources when available, and any resource shortfalls.
Reservation and release events shall also record free and total resource
counts after the transition. Dask runtime events shall include backend
selection, worker count, scheduler address when available, planned scheduler
node, worker placement, total planned cores and GPUs, and any local-fallback
reason.

## Testing

### Testing and Validation: New Task-Parallel Command Path

Date last modified: 2026/05/18

Contributors:

- Xylar Asay-Davis
- Codex

The dependency declaration chunk shall be validated by importing `dask` and
`distributed` from the deployed pixi environment, running `pip check` in that
environment, and running pre-commit on the changed files.

The shared-infrastructure refactor shall be validated with focused unit tests
for step selection, validation marker reads and status accumulation. Existing
targeted tests shall also be run to catch import or runtime regressions from
moving helpers out of `polaris.run.serial`. Pre-commit shall be run on all
changed files.

The thin-command skeleton shall be validated with unit tests for top-level CLI
dispatch, `polaris run --help` and suite/task/step work-directory scope
detection. Existing targeted tests shall also be run to confirm the new command
does not regress the shared serial execution helpers.

The Dask lifecycle chunk shall be validated with unit tests that fake the
Dask `Client` and `LocalCluster` classes to verify worker-count selection and
cleanup without launching real workers. Focused run-command tests shall verify
that `polaris run` creates one Dask lifecycle around task execution and passes
the client to the shared task helper. Shared run-helper tests shall verify
that the existing step loop remains single-active-step even when a Dask client
is available.

The step-metadata chunk shall be validated with unit tests for default
execution-kind classification, MPI classification from task counts and
command-line parallel arguments, explicit overrides, invalid overrides,
non-MPI task-parallel opt-out behavior and the default `run_with_dask()`
fallback. Resource-lease tests shall cover assigned cores, workers and
placeholder fields for future scheduler accounting. Shared run-helper tests
shall verify that `polaris run` uses `run_with_dask()` when a Dask client is
available.

The WOA23 Dask-aware pilot shall be validated with the existing helper tests
and a synthetic local-client test that writes tiny WOA-like NetCDF files,
runs `CombineStep.run_with_dask()` and verifies that the resulting
`woa_combined.nc` matches the serial helper output. The unit test shall not
require downloading the full WOA23 dataset.

The first scheduler graph chunk shall be validated with focused unit tests for
stable selected-step inventory, cached and already completed graph-node
status, explicit dependency edges, unsatisfied dependencies and cycle
rejection. Because this chunk is not wired into `polaris run`, the runtime
command-path behavior shall remain covered by the existing run-command and
shared lifecycle tests.

The file-dependency graph chunk shall add tests for selected output to
selected input edges, already existing external inputs, missing declared
inputs, cached/completed unselected file providers and the absence of implicit
dependencies from `steps_to_run` order alone.

The resource-pool chunk shall add tests for deriving scheduler resource
requests from step CPU/GPU metadata, minimum CPU and GPU feasibility failures,
and reservation/release accounting for CPU cores, nodes and GPUs.

The task-scheduler integration chunk shall add tests showing that task-scope
`polaris run` selects the scheduler-backed task runner while suite-scope runs
continue to use the shared serial task helper. Scheduler runner tests shall
verify dependency-graph order and Phase 1 single-active-step behavior while
still invoking the existing step lifecycle helper with the active Dask client.

The suite-scheduler and observability chunk shall update run-command tests to
verify that suite-scope tasks also receive the scheduler-backed runner.
Scheduler tests shall verify that structured schedule events are written,
that ready selections follow dependency order and that active-step counts in
the events never exceed one.

The setup opt-in chunk shall add tests for the generated run-command helper
and for `polaris setup` and `polaris suite` CLI parsing. These tests shall
verify that job scripts default to `polaris serial` and switch to `polaris run`
only when `--run_command run` is provided.

The Dask runtime backend abstraction chunk shall add unit tests showing that
the local backend is selected by default, reports the selected backend and
worker count, rejects unsupported backend names and closes both the Dask
client and cluster on success and failure. Scheduler tests shall verify that
`schedule_events.jsonl` records Dask backend metadata when the active client
was created by a Polaris Dask runtime backend.

The allocation-scoped Dask launch-planning chunk shall add unit tests for
single-node fallback, unsupported-launch fallback, multi-node CPU worker
placement, partial-node CPU allocations and GPU-per-node metadata. These tests
shall exercise only pure planning logic and shall not require a real batch
scheduler allocation.

The multi-node-capable Dask lifecycle chunk shall add tests for automatic
allocation-backend selection when a launcher is available, local fallback when
it is not, worker launch through a fake `mache` parallel system, and cleanup
of scheduler, worker and client resources on success and failure. These tests
shall not require Slurm, PBS or multiple allocated nodes.

The suite-wide scheduler chunk shall add tests for cross-task explicit
dependencies, cross-task file-derived dependencies and a synthetic suite run
where dependency order differs from suite task order. The synthetic suite test
shall verify that the suite graph, not the outer task loop, chooses execution
order while preserving per-task schedule-event files and aggregate task
results.

The observability and failure-semantics chunk shall add tests for failed steps
that block dependents without blocking independent ready steps. It shall also
verify that completed and cached steps are recorded as skipped with explicit
result status, that resource-feasibility and Dask runtime events are present,
and that suite-wide active-step counts never exceed one.

The validation-helper chunk shall add unit tests for parsing scheduler
event files, summarizing scheduler/Dask evidence, rejecting missing required
events and detecting active-step counts that violate the Phase 1 policy.

The shared-step hardening chunk shall add synthetic tests for repeated shared
step objects, symlinked shared step work directories and suite execution where
multiple selected tasks consume the same shared producer. These tests shall
verify that the graph contains a single producer node, that consumers have an
edge from that producer and that suite execution runs the shared step once.

The cached/completed rerun chunk shall add tests for mixed rerun scenarios in
which cached and already completed producers are skipped but selected
downstream steps still run. It shall also test that executed scheduler steps
write completion markers, validation markers and dependency pickle files in
the same way as the shared serial step lifecycle.

The failure and blocked-dependency chunk shall add tests for task-scope and
suite-scope failures. These tests shall verify that failed steps release
resources, selected dependents are blocked, independent selected steps can
still run where graph dependencies allow and the `polaris run` Dask lifecycle
is closed when scheduler execution raises.

The resource/backend diagnostics chunk shall add tests for feasible and
infeasible scheduler resource events, local and allocation Dask runtime
metadata, scheduler-address recording and validation-helper parsing of the
expanded Dask runtime fields.

The representative synthetic-suite chunk shall add compact end-to-end
workflows that run through the same step lifecycle as production tasks while
remaining independent of E3SM input datasets. These tests shall compare
`polaris serial`-style task execution with suite-wide `polaris run`
scheduling for output files, task logs, completion markers, dependency
pickles, cached producers, completed producers, validation markers, shared
steps and resource reservations. A separate synthetic failure suite shall
verify that failed producers block dependent selected steps while independent
selected steps still run.

The job-script hardening chunk shall add dry-run tests for Slurm and PBS
rendering that verify default `polaris serial` behavior and explicit
`--run_command run` opt-in behavior for suite scripts. These tests shall check
the batch metadata and rendered run commands without submitting jobs. Manual
machine validation should still confirm the generated scripts on each target
HPC system, including environment loading, allocation sizing and the presence
of `schedule_events.jsonl` files after `polaris run` completes.

The real-task equivalence chunk shall document a routine custom-suite
validation based on:

```none
mesh/spherical/icos/base_mesh/240km/task
e3sm/init/icos240km/topo/remap
e3sm/init/icos240km/topo/cull
```

This validation shall run the task set once with `polaris serial`, set up an
equivalent `polaris run` work directory with the serial output as the baseline
directory, run the scheduler path, then immediately rerun the scheduler path.
The first scheduler run shall validate outputs, task logs, completion markers,
validation markers, cached steps and Dask-backed scheduler artifacts. The
second scheduler run shall verify already-completed and cached-step behavior.
The expected scheduler artifacts are one `schedule_events.jsonl` file per task,
a recorded Dask runtime event in each file and active-step counts that satisfy
the Phase 1 single-step policy. Larger data-dependent tasks, including global
hydrography tasks that exercise Dask-aware Python step implementations, remain
optional manual/system validation until they are cheap enough for routine
developer checks.

The representative-suite subset chunk shall document validation of predefined
ocean suites, especially `omega_pr`, `omega_nightly` and `mpaso_pr`. When the
full suite is too expensive or unavailable on a machine, the validation may use
a custom `polaris setup` subset drawn from the corresponding suite file. The
validation record shall include the machine, model build, suite or subset name,
selected task list, final task-runtime table and the schedule-event summary.
The expected result is that suite-wide `polaris run` preserves dependency
order, aggregate pass/fail status and task logs while every task event file
records Dask runtime metadata and satisfies the Phase 1 single-active-step
policy. Failures in full-suite validation should be categorized as scheduler
regressions, model/task failures, missing data, machine-environment issues or
known unsupported tasks.

### Testing and Validation: Phase-1 Scheduler and Graph

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

Unit tests should cover graph construction from explicit dependencies and
input/output file relationships, including cycles, missing producers,
completed steps, cached steps, skipped steps and already satisfied external
inputs. Tests should verify that `steps_to_run` affects deterministic order
but does not create implicit dependencies.

Scheduler tests should cover deterministic ready-step selection, invalid
resource requests and single-active-step enforcement. Synthetic workflows
should include independent branches, shared dependencies and intentionally
failed steps.

### Testing and Validation: Phase-1 Execution Equivalence

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

Integration tests should compare `polaris run` with `polaris serial` for
suites, tasks and individual steps. These tests should verify equivalent
outputs, runtime input failures, per-step logs, completion markers,
`step_after_run.pickle` behavior, baseline/property markers and rerun
behavior.

Representative suite validation should include `omega_pr`, `omega_nightly`
and `mpaso_pr` on Chrysalis, Perlmutter and Aurora where available. The
structured schedule summary should confirm that Phase 1 ran through the Dask
orchestration path while keeping only one step active at a time.

Phase 1 validation should treat task and suite timing as distinct metrics.
Task runtime should eventually be reported as the sum of the runtime of the
steps that actually ran for that task, while suite runtime should remain the
wall-clock duration of the whole suite run. Until that summary behavior is
hardened, validation should rely on per-step runtime lines and structured
step start/finish/failure events when comparing `polaris run` and
`polaris serial` timing.
