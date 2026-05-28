# Task Parallelism in Polaris: Phase 1

Creation date: 2026/04/28

Contributors:

- Xylar Asay-Davis
- Codex

## Summary

Phase 1 introduces the future task-parallel execution path in Polaris without
yet enabling concurrent step execution. This phase adds a new command,
`polaris run`, that mirrors the current `polaris serial` command as closely as
practical for suites, tasks and individual steps. The new command discovers
work from the same work directories, accepts similar command-line options and
preserves the per-step execution semantics that users rely on.

After Perlmutter validation, Phase 1 must also establish the runtime model
that Phase 2 will use. The only intended policy difference between Phase 1
and Phase 2 is the eligible non-MPI concurrency cap: Phase 1 sets the cap to
one active non-MPI step, while Phase 2 raises the cap so independent eligible
steps may run concurrently when resources allow.

The phrase "task parallelism" is the historical project label, but the Polaris
scheduling unit is a `Step`. Later phases are intended to run independent
selected steps concurrently, potentially drawn from one task or from multiple
tasks in a suite. They are not intended to treat whole Polaris `Task` objects
as the indivisible unit of parallel execution.

Unlike `polaris serial`, `polaris run` is intended to become the foundation
for later task parallelism. In Phase 1, it builds the scheduling framework
needed for future concurrent execution, including dependency-graph
construction, ready-step selection, resource-aware scheduling, deterministic
step ordering, schedule summaries, structured scheduler events and metadata
for future step eligibility. It also establishes the Dask Distributed
orchestration layer that Phase 2 will use for concurrent non-MPI execution.
However, Dask workers must be phase-scoped rather than run-scoped. They are
started only for eligible non-MPI phases and are stopped before MPI or
otherwise serialized steps are launched. The scheduler still executes only
one eligible non-MPI Polaris step at a time in Phase 1.

Phase 1 therefore aims to prove that the new execution path is correct and
complete before it is asked to deliver speedup. Some slowdown relative to
`polaris serial` is acceptable. The goal is not improved wall time in this
phase, but rather that the infrastructure needed for Phase 2 is already in
place, so Phase 2 can focus primarily on raising the concurrency cap and
debugging issues that arise only when independent steps overlap.

`polaris serial` remains unchanged and continues to be the default execution
path recommended by `polaris setup` and `polaris suite`. Phase 1 provides an
opt-in path for `polaris setup` and `polaris suite` to set up tasks and suites
that use `polaris run`.

Success in Phase 1 means that `polaris run`:

- works correctly for suites, tasks and individual steps,
- preserves per-step outputs, logs, completion markers and runtime input
  checking,
- produces final outputs that match task-serial baselines for deterministic
  workflows,
- schedules steps from explicit dependencies and declared input/output file
  dependencies rather than from implicit serial order,
- rejects invalid dependency graphs before running,
- enforces minimum resource requirements even though it still runs one step at
  a time,
- records enough structured scheduler data to verify single-active-step
  execution and diagnose later task-parallel behavior, and
- has been validated on representative workflows, with remaining platform and
  suite validation recorded explicitly.

## Requirements

### Requirement: New Task-Parallel Command Path

Date last modified: 2026/05/23

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

Date last modified: 2026/05/23

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

Date last modified: 2026/05/23

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

Date last modified: 2026/05/23

Contributors:

- Xylar Asay-Davis
- Codex

`polaris run` shall construct a dependency graph before executing work. The
graph shall be based on explicit step dependencies and on declared
input/output-file dependencies.

Implicit dependence on the order of `steps_to_run` shall not be treated as a
source of truth for scheduling. If an existing suite or task relied on serial
ordering without declaring a true dependency, it is acceptable for
`polaris run` to expose that bug.

Before starting execution, `polaris run` shall reject invalid dependency
graphs, including cycles and cases where declared dependencies cannot be
satisfied.

### Requirement: Deterministic Ready-Step Selection

Date last modified: 2026/05/23

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

Date last modified: 2026/05/23

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

Date last modified: 2026/05/23

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

Date last modified: 2026/05/23

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

Date last modified: 2026/05/23

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

Date last modified: 2026/05/28

Contributors:

- Xylar Asay-Davis
- Codex

Phase 1 shall function correctly on the supported execution environments that
matter most for later task parallelism, including Chrysalis, Perlmutter,
Aurora and Frontier.

Validation will inevitably focus on representative suites and machines rather
than exhaustive testing of every suite on every platform. Even so, the Phase 1
goal is that `polaris run` should work correctly for all existing suites, not
just for a small pilot subset.

Representative validation and performance comparisons shall include
`omega_pr` on Chrysalis, Perlmutter, Aurora and Frontier, using both CPU and
GPU configurations where they are available. To keep Phase 1 validation
manageable, broader-suite validation with `omega_nightly` and `mpaso_pr`
shall be limited to Chrysalis. On these suites, the slowdown of `polaris run`
relative to `polaris serial` should remain within an acceptable budget, on
the order of roughly 40-50% and not much more.

### Desired: Frontier Support

Date last modified: 2026/05/23

Contributors:

- Xylar Asay-Davis
- Codex

Frontier support and validation in Phase 1 are valuable and have been included
in the cross-machine `omega_pr` validation scope.

### Desired: Task-Serial Summary

Date last modified: 2026/05/23

Contributors:

- Xylar Asay-Davis
- Codex

Comparable schedule and resource summaries for `polaris serial` would be
useful for side-by-side debugging, even though they are not required in
Phase 1.

## Algorithm Design

### Algorithm Design: New Task-Parallel Command Path

Date last modified: 2026/05/26

Contributors:

- Xylar Asay-Davis
- Codex

`polaris run` should be designed as the permanent task-parallel command path,
not as a thin alias for `polaris serial`. It should share the same setup
artifacts, pickle files, work-directory discovery and user-facing scope as
`polaris serial`, but it should route execution through a scheduler that owns
the dependency graph, resource pool and step lifecycle.

Phase 1 should stand up Dask Distributed only when the current execution
phase contains eligible non-MPI work that runs through the task-parallel
executor. The default local backend uses a local `distributed.LocalCluster`.
On multi-node supported allocations, an allocation-scoped backend should
launch one Dask scheduler and multiple single-threaded worker processes
across the data-plane resources. Dask workers should not be launched for a
whole mixed suite.

Polaris should control the Dask scheduler and worker lifecycle directly
rather than using `dask-mpi` as the primary orchestration mechanism. This
keeps resource handoff policy in Polaris, avoids making the batch scheduler
responsible for many small Python tasks, and leaves room for
scheduler-specific MPI launch behavior to remain isolated in later phases.
The parent `polaris run` process and any Dask scheduler process are
control-plane work. Polaris should reserve control-plane resources before
planning Dask workers or checking step feasibility.

### Algorithm Design: Backward-Compatible Per-Step Execution Semantics

Date last modified: 2026/05/23

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
should submit only one such step at a time. The step may run in a worker
process or a subprocess when required by the existing step configuration, but
it should preserve the same working directory, logging, environment and
completion semantics as existing task-serial execution.

Dask-aware non-MPI steps should use a separate execution hook rather than
changing the meaning of `run()`. A Dask-aware step should run coordinating
code with an assigned Dask client and resource lease, and that coordinating
code may submit internal Dask work. This hook gives large Python steps a path
to use multiple workers without changing ordinary step semantics.

### Algorithm Design: Task-Parallel Output Equivalence

Date last modified: 2026/05/23

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

Date last modified: 2026/05/23

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

Shared steps should be identified by canonical work directory. If the same
underlying step is selected by multiple tasks, the scheduler should execute it
once and make all consumers depend on the same producer node.

### Algorithm Design: Deterministic Ready-Step Selection

Date last modified: 2026/05/23

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

Date last modified: 2026/05/23

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
treating MPI task counts as non-MPI worker counts.

### Algorithm Design: Single-Step Execution in Phase 1

Date last modified: 2026/05/26

Contributors:

- Xylar Asay-Davis
- Codex

The Phase 1 scheduling policy should be a single-active-step policy layered on
top of the future task-parallel scheduler. The scheduler should repeatedly:

- identify ready graph nodes,
- filter them by completion, cached status, resource feasibility and execution
  mode,
- choose the next step from the current execution-mode batch when possible,
- reserve resources for that step,
- execute it through the `polaris run` orchestration path, and
- release resources after the step succeeds or fails.

No second step should be started while another step is active, even if it is
independent and enough resources remain idle. This intentionally leaves
parallel speedup for Phase 2 while proving that the scheduler, resource pool
and task-parallel runtime path are already real.

Within that single-active-step constraint, Phase 1 should already use the
same mode-batching policy expected in Phase 2. If a task-parallel worker pool
is active, the scheduler should continue running ready eligible non-MPI steps
that can use the active worker-pool phase before stopping it for serialized
work. If no worker pool is active, the scheduler should run a batch of ready
MPI or ineligible serialized steps before starting the worker-pool mode again,
unless no serialized work is ready and an eligible non-MPI step can make
progress. Stable deterministic step order remains the tie-breaker within a
mode, but independent steps may be reordered across modes to avoid
unnecessary worker-pool start/stop cycles.

### Algorithm Design: Future Parallel-Eligibility Metadata

Date last modified: 2026/05/23

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

Date last modified: 2026/05/26

Contributors:

- Xylar Asay-Davis
- Codex

Phase 1 observability should have both structured and human-readable forms.
The human-readable output should summarize selected order, wait reasons,
resource reservations, step timing, completion state and final results. The
structured output should record schedule/resource events so Phase 2 and later
debugging can reconstruct what happened without scraping free-form logs.

Structured events should include at least graph construction, ready-step
selection, resource feasibility, resource reservation, task-parallel runtime
state, mode-batch start/stop decisions, worker-pool launch requested,
worker-pool ready, worker-pool shutdown requested, worker-pool stopped,
serialized-step barriers, step start, step finish, step failure, skipped or
blocked steps and resource release. These events should be sufficient to
verify that Phase 1 did not accidentally run steps concurrently, that Phase 2
does run eligible steps concurrently and that Polaris is not paying avoidable
task-parallel runtime lifecycle overhead.

The scheduler should also summarize task-parallel runtime lifecycle overhead
in human-readable output. At minimum, a task-parallel run should report the
number of worker-pool phases, total worker-pool startup wall time, total
worker-pool shutdown wall time, mean and maximum startup time, mean and
maximum shutdown time and the fraction of suite wall time spent in
worker-pool lifecycle management. For the current Dask-backed implementation,
this report should name Dask startup and shutdown explicitly. This report is
required because Perlmutter validation showed that lifecycle overhead can be
large enough to dominate small `omega_pr` steps.

Task and suite timing should be distinct. Suite runtime should be wall-clock
time for the whole run. A task runtime reported by `polaris run` should be the
sum of measured runtimes for that task's selected steps. If a shared step is
used by multiple tasks, its measured runtime should contribute to each task
that references it, reflecting the time that task would have taken if it were
run in isolation.

### Algorithm Design: Cross-Machine Phase-1 Functionality

Date last modified: 2026/05/26

Contributors:

- Xylar Asay-Davis
- Codex

The Phase 1 algorithm should assume the batch scheduler provides a fixed
allocation, while Polaris explicitly separates control-plane resources from
the data-plane resources available to Dask workers and steps.
Machine-specific differences should be confined to allocation discovery, job
script generation, worker launch details and later MPI launch behavior.

On Slurm systems such as Chrysalis and Perlmutter, the design should avoid
using a scheduler-launched job step for every small Python step and should
avoid overlapping long-lived Dask worker job steps with model `srun` calls.
On PBS-based systems such as Aurora, the same phase-scoped Dask model should
remain the conceptual target even if worker launch details differ.

### Algorithm Design: Frontier Support

Date last modified: 2026/05/23

Contributors:

- Xylar Asay-Davis
- Codex

N/A for Phase 1. Frontier validation is desired, but it does not change the
core Phase 1 algorithm beyond the cross-machine portability choices described
above.

### Algorithm Design: Task-Serial Summary

Date last modified: 2026/05/23

Contributors:

- Xylar Asay-Davis
- Codex

Comparable `polaris serial` summaries should use the same schedule-summary
concepts where practical: selected steps, timing, completion state and final
validation status. They do not need Dask worker or resource-pool events.

## Implementation

### Implementation: New Task-Parallel Command Path

Date last modified: 2026/05/26

Contributors:

- Xylar Asay-Davis
- Codex

Dask Distributed is part of the Polaris runtime dependency set. The
`polaris.run.parallel` module implements the `polaris run` command path and
shares command-independent lifecycle helpers with `polaris serial` through
`polaris.run.shared`. These shared helpers cover suite unpickling, runtime
configuration setup, dependency loading, step selection, completion markers,
task logging, status accumulation, per-step lifecycle execution, subprocess
step execution and pull-request summary generation.

`polaris run` is wired into the top-level Polaris CLI and mirrors
`polaris serial` scope detection for suites, tasks and individual steps. The
command constructs the scheduler and passes allocation resources to it. The
scheduler owns Dask phase lifetimes and passes an active Dask client to step
lifecycle helpers only while an eligible non-MPI phase is running.

Generated job scripts remain task-serial by default. `polaris setup` and
`polaris suite` accept `--run_command` with choices `serial` and `run`. When
`--run_command run` is selected, task and step scripts run `polaris run` and
suite scripts run `polaris run <suite>`.

### Implementation: Backward-Compatible Per-Step Execution Semantics

Date last modified: 2026/05/26

Contributors:

- Xylar Asay-Davis
- Codex

The scheduler executes each started step through the same shared lifecycle
used by `polaris serial`. Runtime configuration is reloaded before the step
runs, runtime dependencies are refreshed from post-run pickle files,
resources are constrained, `runtime_setup()` is called, command-line or Python
step execution is performed, outputs are checked, validation hooks run and
completion markers are written.

Steps that require subprocess execution keep that behavior under
`polaris run`. When a subprocess is started during an active Dask phase, the
parent run passes the active Dask scheduler address through the
`POLARIS_DASK_SCHEDULER_ADDRESS` environment variable so the subprocess can
connect to the existing Dask runtime instead of starting a nested runtime.
Serialized MPI or ineligible subprocesses should not inherit a stale Dask
scheduler address after a Dask phase has stopped.

Dask-aware steps can implement `run_with_dask(client, resources)`. The default
implementation falls back to `run()`, so ordinary step behavior does not
change unless a step opts in. The WOA23 hydrography combine step currently
provides a Dask-aware implementation, but it remains an expensive manual
validation target rather than a routine regression test.

### Implementation: Task-Parallel Output Equivalence

Date last modified: 2026/05/23

Contributors:

- Xylar Asay-Davis
- Codex

Baseline and property comparisons remain part of the normal shared step
lifecycle. Scheduler results aggregate task execution status, baseline status
and property status using the same markers used by `polaris serial`.

Cached and already completed selected steps remain graph participants. They
are skipped at execution time, emit explicit structured skip events and can
contribute existing baseline and property marker status to task summaries.
Downstream selected steps can therefore rely on cached or completed producers
without forcing reruns.

### Implementation: Explicit Dependency-Graph Scheduling

Date last modified: 2026/05/23

Contributors:

- Xylar Asay-Davis
- Codex

`polaris.run.scheduler` defines `SchedulerNode`, `SchedulerGraph`,
`build_scheduler_graph()`, `run_task()` and `run_suite()`. The graph builder
inventories selected steps in stable suite/task/step order, preserves cached
and already completed selected steps as graph nodes, adds edges from explicit
`Step.dependencies` and adds edges from declared input/output file
relationships. File paths are resolved to canonical absolute paths before
matching.

Unselected cached or completed dependencies may appear as satisfied graph
participants. Existing input files with no selected producer are treated as
external satisfied inputs. Missing declared inputs are rejected unless they
are produced by a selected step or by an unselected cached or already
completed step. Cycles are rejected before execution.

Shared-step handling uses the canonical step work directory as the step
identity. Repeated selected step objects and symlinked aliases are represented
by one producer node, and all explicit or file-derived consumers point to that
node.

### Implementation: Deterministic Ready-Step Selection

Date last modified: 2026/05/23

Contributors:

- Xylar Asay-Davis
- Codex

The graph stores an inventory order for every selected or satisfied node. The
scheduler uses deterministic topological ordering based on dependency
constraints and that stable inventory order. `steps_to_run` therefore affects
tie-breaking among otherwise ready steps but does not create dependency edges.

Suite-scope `polaris run` builds one scheduler graph across all selected
tasks. This lets cross-task explicit dependencies and file-derived
dependencies affect order directly, rather than relying on an outer suite task
loop.

### Implementation: Resource-Aware Scheduling and Enforcement

Date last modified: 2026/05/23

Contributors:

- Xylar Asay-Davis
- Codex

`polaris.run.resources` provides resource-request and resource-pool helpers.
The scheduler derives step resource requests from step CPU, node, GPU and MPI
metadata without mutating the step. It checks minimum feasibility before
starting a step, reserves resources for the active step, records the
reservation, and releases the reservation in a `finally` block.

Resource feasibility, reservation and release are recorded in structured
events. These events include requested and minimum resources when available,
free and total resource counts, infeasible shortfalls and result status.

The Dask runtime backend also records planned worker resources. The local
backend uses single-threaded local workers. The allocation backend plans a
single Dask scheduler and single-threaded workers distributed across the
allocation, using physical core counts from the active machine resource model.

### Implementation: Single-Step Execution in Phase 1

Date last modified: 2026/05/23

Contributors:

- Xylar Asay-Davis
- Codex

The Phase 1 scheduler uses a single-active-step policy in both task-scope and
suite-scope runs. It never starts a second Polaris step while another step is
active. The active-step count is maintained in the scheduler and recorded in
`schedule_events.jsonl` as both per-task and suite-wide active-step metadata.

Failed steps release resources, record failure events and block selected
dependents. Independent selected steps remain eligible to run if their
dependencies are satisfied. Task-scope runs re-raise the original execution
failure after the scheduler has recorded blocked dependents; suite-scope runs
preserve aggregate pass/fail status and task log reporting.

### Implementation: Future Parallel-Eligibility Metadata

Date last modified: 2026/05/23

Contributors:

- Xylar Asay-Davis
- Codex

Step execution-kind metadata distinguishes MPI-style steps from non-MPI
steps. The default classification is conservative: steps that request more
than one MPI task, require more than one MPI task or use command-line parallel
arguments are treated as MPI. Step authors can override the derived execution
kind when the default is wrong.

Non-MPI steps are considered eligible for future concurrent execution by
default. Step authors can mark a non-MPI step ineligible when it has shared
mutable state, external side effects, uncontrolled process launching or other
behavior that should remain serialized in Phase 2.

### Implementation: Observable Execution and Schedule Summaries

Date last modified: 2026/05/23

Contributors:

- Xylar Asay-Davis
- Codex

Each task work directory gets a `schedule_events.jsonl` file when it is run
through the scheduler. These JSON-lines files record graph construction, Dask
runtime metadata, ready selection, wait reasons, resource feasibility,
resource reservation, step start, step finish, step failure, skipped or
blocked steps, and resource release.

Task logs include a human-readable selected-order summary with node status and
wait reason. The main suite log reports per-step runtime lines, per-task
execution status and final task-runtime summaries.

Task runtime summaries in `polaris run` are computed as the sum of measured
durations for each task's selected steps. Shared steps are still executed once
but are counted in the runtime of each task that references them. Suite
runtime remains the wall-clock duration of the whole run.

Allocation-scoped Dask scheduler and worker output is written to
`dask_runtime.log` in the run work directory. This keeps useful backend
debugging output available without obscuring the main Polaris log with normal
Dask lifecycle messages.

The `polaris.run.validation` module provides helpers for parsing scheduler
event files, summarizing scheduler and Dask evidence, counting events,
summing started-step durations and verifying that active-step counts satisfy
the Phase 1 single-step policy.

### Implementation: Cross-Machine Phase-1 Functionality

Date last modified: 2026/05/26

Contributors:

- Xylar Asay-Davis
- Codex

Dask runtime selection is abstracted behind backend classes. The automatic
selection path uses the allocation backend when the available resources
represent a multi-node allocation and a process launcher is available from
the active `mache` parallel system. Otherwise it falls back to the local
backend and records the fallback reason.

The allocation backend launches the Dask scheduler as a subprocess, writes a
scheduler metadata file, launches workers through a pluggable process
launcher, creates a Dask client from the scheduler metadata, and shuts down
the client, workers and scheduler at the end of the current non-MPI phase. On
Slurm systems, the worker command is launched through the active parallel
system rather than launching one batch step per Polaris step.

The implementation has been exercised most heavily on Chrysalis. Perlmutter
and Aurora remain required validation targets for the broader rollout.

### Implementation: Frontier Support

Date last modified: 2026/05/23

Contributors:

- Xylar Asay-Davis
- Codex

No Frontier-specific implementation is required for Phase 1. Any
Frontier-specific work should be limited to the same machine-resource,
job-script and Dask-worker launch abstractions used by the required
cross-machine targets.

### Implementation: Task-Serial Summary

Date last modified: 2026/05/23

Contributors:

- Xylar Asay-Davis
- Codex

No task-serial schedule-summary implementation is required for Phase 1.
`polaris serial` continues to use its existing log and runtime summaries.

## Testing

### Testing and Validation: New Task-Parallel Command Path

Date last modified: 2026/05/23

Contributors:

- Xylar Asay-Davis
- Codex

Unit tests cover top-level CLI dispatch, `polaris run --help`, suite/task/step
work-directory discovery, `polaris run` Dask lifecycle creation and client
propagation, and generated job-script command selection. Setup and suite tests
verify that job scripts default to `polaris serial` and switch to
`polaris run` only when `--run_command run` is provided.

Dependency metadata is validated by importing `dask` and `distributed` from
the deployed pixi environment and by running `pip check` in that environment.
Pre-commit is run on changed files.

### Testing and Validation: Backward-Compatible Per-Step Execution Semantics

Date last modified: 2026/05/23

Contributors:

- Xylar Asay-Davis
- Codex

Shared run-helper tests cover step selection, completion marker behavior,
validation marker reads, status accumulation, subprocess step execution and
Dask-client propagation. Scheduler tests verify that both task-scope and
suite-scope `polaris run` still invoke the shared step lifecycle helpers with
the active Dask client.

Synthetic integration tests compare serial-style task execution with
scheduler execution for output files, task logs, completion markers,
dependency pickles, cached producers, completed producers, validation markers
and shared steps.

### Testing and Validation: Task-Parallel Output Equivalence

Date last modified: 2026/05/28

Contributors:

- Xylar Asay-Davis
- Codex

Unit and synthetic tests verify that scheduler execution preserves completion
markers, `step_after_run.pickle`, baseline markers and property markers. They
also verify that cached and completed producers are skipped with explicit
events while downstream selected steps still run.

Real-task validation on Chrysalis has used a small custom suite containing:

```none
mesh/spherical/icos/base_mesh/240km/task
e3sm/init/icos240km/topo/remap
e3sm/init/icos240km/topo/cull
```

This validation has been run with `polaris serial`, then with an equivalent
`polaris run` work directory using the serial output as the baseline. The
first scheduler run verified outputs, task logs, completion markers,
validation markers, cached steps and Dask-backed scheduler artifacts. Reruns
verified already-completed and cached-step behavior.

Chrysalis regression testing has also been run from
`/lcrc/group/e3sm/ac.xylar/polaris_1.0/chrysalis/test_20260528`, with outputs
in `mpaso-pr-parallel`, `omega-nightly-parallel` and
`omega-pr-parallel`. These runs show no sign of trouble or unusual runtime
growth relative to the baselines. The `omega_nightly` run reported baseline
failures only because the baseline itself timed out and required files were
missing. Direct wall-time comparison with the baselines is not meaningful
because those baselines were run incorrectly with hyperthreading enabled,
whereas the task-parallel runs correctly were not.

### Testing and Validation: Explicit Dependency-Graph Scheduling

Date last modified: 2026/05/23

Contributors:

- Xylar Asay-Davis
- Codex

Scheduler unit tests cover graph construction from explicit dependencies and
declared input/output file relationships. These tests include selected
producer/consumer edges, cross-task dependencies, cycles, missing producers,
existing external inputs, cached producers, completed producers and already
satisfied unselected dependencies.

Shared-step tests cover repeated shared step objects, symlinked shared step
work directories and suite execution where multiple selected tasks consume
the same shared producer. These tests verify that the graph contains a single
producer node, that all consumers depend on that producer and that suite
execution runs the shared step only once.

### Testing and Validation: Deterministic Ready-Step Selection

Date last modified: 2026/05/23

Contributors:

- Xylar Asay-Davis
- Codex

Scheduler tests verify that topological order is deterministic and that
`steps_to_run` order affects tie-breaking without creating implicit
dependencies. Suite-wide scheduler tests verify that the suite graph, not the
outer task loop, chooses execution order when dependency order differs from
suite task order.

Human-readable selected-order summaries and `ready_selection` events are
checked in tests and in manual suite validation to confirm that selected order
is understandable and repeatable.

### Testing and Validation: Resource-Aware Scheduling and Enforcement

Date last modified: 2026/05/23

Contributors:

- Xylar Asay-Davis
- Codex

Resource tests cover scheduler resource-request derivation from step CPU, GPU
and MPI metadata; minimum CPU and GPU feasibility failures; and
reservation/release accounting for CPU cores, nodes and GPUs.

Scheduler tests verify feasible and infeasible resource events, resource
shortfalls, resource reservation and resource release. Dask-runtime tests
cover local worker-count selection, allocation launch planning, multi-node
CPU worker placement, partial-node CPU allocations, GPU-per-node metadata and
local fallback reasons.

### Testing and Validation: Single-Step Execution in Phase 1

Date last modified: 2026/05/23

Contributors:

- Xylar Asay-Davis
- Codex

Scheduler tests verify that the maximum active Polaris step count never
exceeds one in task-scope and suite-scope runs. Synthetic suites include
independent branches that could run concurrently in later phases but must
remain single-active-step in Phase 1.

Failure tests verify that failed steps release resources, selected dependents
are blocked, independent selected steps can still run where graph
dependencies allow and the Dask runtime context is closed when scheduler
execution raises.

The validation helper
`polaris.run.validation.validate_phase1_schedule_event_files()` checks
`schedule_events.jsonl` files for the single-active-step invariant. Manual
system validation should run this helper on representative suite outputs.

### Testing and Validation: Future Parallel-Eligibility Metadata

Date last modified: 2026/05/23

Contributors:

- Xylar Asay-Davis
- Codex

Unit tests cover default execution-kind classification, MPI classification
from task counts and command-line parallel arguments, explicit overrides,
invalid overrides, non-MPI task-parallel opt-out behavior and the default
`run_with_dask()` fallback.

Resource-lease tests cover assigned cores, workers and placeholder fields for
future node, GPU and memory accounting.

### Testing and Validation: Observable Execution and Schedule Summaries

Date last modified: 2026/05/26

Contributors:

- Xylar Asay-Davis
- Codex

Scheduler tests verify that `schedule_events.jsonl` files are written and
that they include graph construction, control-plane reservation, Dask runtime
metadata, scheduler-owned Dask phase start/stop, serialized-step barriers,
ready selection, wait reasons, resource feasibility, reservation, start,
finish, failure, skip, block and release events.

Validation-helper tests cover parsing scheduler event files, summarizing
scheduler/Dask evidence, rejecting missing required events, detecting active
step counts that violate the Phase 1 policy and summing finished and failed
started-step durations.

Task-runtime tests verify that suite task runtimes are summed from measured
step durations, and that a shared step contributes to the runtime of every
task that references it while still running only once.

### Testing and Validation: Cross-Machine Phase-1 Functionality

Date last modified: 2026/05/28

Contributors:

- Xylar Asay-Davis
- Codex

Unit tests use fake launchers, fake clients and fake parallel systems to cover
local backend selection, automatic allocation-backend selection, local
fallback, data-plane worker launch planning, worker launch command
construction and scheduler/worker/client cleanup on success and failure.
These tests do not require a real Slurm or PBS allocation.

Dry-run job-script tests cover Slurm and PBS rendering for default
`polaris serial` behavior and explicit `--run_command run` behavior.

Recorded system validation status is:

- Phase 1 cross-machine validation targets `omega_pr` on Chrysalis,
  Perlmutter, Aurora and Frontier, with CPU and GPU configurations where
  available. `omega_nightly` and `mpaso_pr` validation are limited to
  Chrysalis to keep the validation matrix manageable.
- **Chrysalis**: real-task custom icosahedral/topography validation has passed.
  The latest recorded regression run is
  `/lcrc/group/e3sm/ac.xylar/polaris_1.0/chrysalis/test_20260528`, with
  outputs in `mpaso-pr-parallel`, `omega-nightly-parallel` and
  `omega-pr-parallel`. These runs show no sign of trouble or unusual runtime
  growth relative to the baselines. The `omega_nightly` run reported baseline
  failures only because the baseline itself timed out and required files were
  missing. Direct wall-time comparison with the baselines is not meaningful
  because those baselines were run incorrectly with hyperthreading enabled,
  whereas the task-parallel runs correctly were not.
- **Perlmutter**: serial CPU and GPU `omega_pr` baselines have passed. Earlier
  task-parallel CPU and GPU `omega_pr` attempts stalled before completing
  the first test, consistent with a Slurm configuration that does not permit
  overlapping `srun` calls within the allocation. Post-remodel CPU and GPU
  `omega_pr` runs completed successfully with phase-scoped, data-plane Dask
  launches, but showed unacceptable worker-pool lifecycle overhead. The CPU
  run
  `/pscratch/sd/x/xylar/polaris_1.0/pm-cpu/test_20260526/omega-pr-parallel-gnu3/polaris_omega_pr.o53434041`
  took `0:13:51` compared with the serial baseline
  `/pscratch/sd/x/xylar/polaris_1.0/pm-cpu/test_20260526/omega-pr-baseline-gnu/polaris_omega_pr.o53428117`
  at `0:05:58`, and the GPU run
  `/pscratch/sd/x/xylar/polaris_1.0/pm-cpu/test_20260526/omega-pr-parallel-gnugpu3/polaris_omega_pr.o53433884`
  took `0:14:04` compared with the serial baseline
  `/pscratch/sd/x/xylar/polaris_1.0/pm-cpu/test_20260526/omega-pr-baseline-gnugpu/polaris_omega_pr.o53428342`
  at `0:09:35`. Both task-parallel runs created 13 worker-pool phases,
  motivating the mode-batching policy and worker-pool lifecycle timing
  report. After mode batching, the CPU run
  `/pscratch/sd/x/xylar/polaris_1.0/pm-cpu/test_20260526/omega-pr-parallel-gnu4/polaris_omega_pr.o53442097`
  completed in `0:10:19` with two worker-pool phases. Its recorded
  worker-pool lifecycle time was `0:00:55`, or 8.9% of suite wall time,
  suggesting that the remaining overhead is much smaller and that the
  previous CPU validation may also have been affected by normal run-to-run
  variability. `omega_nightly` and `mpaso_pr` are not planned for Perlmutter
  validation in Phase 1.
- **Aurora**: earlier CPU (`oneapi-ifx`) and GPU (`oneapi-ifxgpu`) `omega_pr`
  suite runs completed successfully, but those recorded artifacts predate the
  Perlmutter-driven scheduler restructure and should not be treated as the
  current Phase 1 validation record. Updated Aurora validation still needs to
  be recorded. `omega_nightly` and `mpaso_pr` are not planned for Aurora
  validation.
- **Frontier**: Tests of `omega_pr` on CPUs (`craygnu`) and GPUs (`craygnu-mphipcc`)
  have passed against serial baselines.  The CPU results are at:
  ```
  /lustre/orion/cli115/scratch/xylar/polaris_1.0/frontier/test_20260528/omega-pr-parallel-craygnu
  ```
  and took `0:05:52`, compared with `0:04:33` for the serial baseline.
  The GPU results are at:
  ```
  /lustre/orion/cli115/scratch/xylar/polaris_1.0/frontier/test_20260528/omega-pr-parallel-craygnu-mphipcc
  ```
  and took `0:05:01`, compared with `0:04:05` for the serial baseline.

### Testing and Validation: Frontier Support

Date last modified: 2026/05/28

Contributors:

- Xylar Asay-Davis
- Codex

Frontier validation has been performed as part of the cross-machine Phase 1
`omega_pr` validation. The CPU (`craygnu`) and GPU (`craygnu-mphipcc`) runs
passed against serial baselines, with successful `polaris run`, matching
serial baseline comparisons where available, Dask runtime metadata,
schedule-event validation and single-active-step evidence. `omega_nightly`
and `mpaso_pr` are not planned for Frontier validation in Phase 1.

### Testing and Validation: Task-Serial Summary

Date last modified: 2026/05/23

Contributors:

- Xylar Asay-Davis
- Codex

No task-serial schedule-summary validation is required for Phase 1. Existing
`polaris serial` tests and regression suites continue to validate the
unchanged serial path.

## Phase 2 Handoff

Phase 2 should be able to inherit the Phase 1 command path, graph builder,
resource feasibility model, Dask runtime, subprocess-client propagation and
structured event stream. The main Phase 2 scheduling change should be
replacing the single-active-step policy with conservative packing of eligible
ready non-MPI steps.

The following work is explicitly handed to Phase 2 or later:

- concurrent ready-step execution, beginning with eligible non-MPI steps in
  Phase 2;
- a cheap Dask-aware regression task or fixture that exercises Dask-aware step
  code without requiring a long global hydrography workflow;
- MPI-step concurrency and MPI/non-MPI scheduling barriers, which belong to
  Phases 3 and 4;
- memory-aware scheduling and prevention of memory oversubscription, which
  belongs to Phase 4;
- any additional platform validation beyond the Phase 1 `omega_pr` matrix;
  and
- broader `omega_nightly` and `mpaso_pr` system validation beyond Chrysalis.
