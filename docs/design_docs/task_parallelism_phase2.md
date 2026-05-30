# Task Parallelism in Polaris: Phase 2

Creation date: 2026/04/28

Contributors:

- Xylar Asay-Davis
- Codex

## Summary

Phase 2 enables task-parallel execution for eligible non-MPI steps in
Polaris. This phase builds directly on the `polaris run` infrastructure from
Phase 1. The eligibility mechanism, dependency graph, resource model,
restart behavior and run summaries developed in Phase 1 become the foundation
for actually running independent work concurrently.

Phase 2 should be a small policy change on top of the Phase 1 runtime model:
raise the eligible non-MPI concurrency cap from one active step to
resource-limited concurrent execution. The scheduler, control-plane
reservation, phase-scoped worker-pool lifecycle and mixed-workflow barriers
should already have been implemented and validated in Phase 1.

The central new capability in Phase 2 is that independent non-MPI steps may
run at the same time by default, subject to dependency, explicit ineligibility
and resource constraints. MPI steps and non-MPI steps marked unsafe or
ineligible remain task serial in this phase. Mixed workflows are still
required because many Polaris suites contain both MPI and non-MPI work, but
Phase 2 uses a conservative barriered model: at any given time, Polaris runs
either eligible non-MPI work in task-parallel mode or non-eligible work in
task-serial mode.

Perlmutter validation showed that this barrier is also a launch requirement,
not just a scheduling preference. Worker-pool job steps used by the
task-parallel non-MPI runtime must be stopped before MPI or otherwise
serialized `srun` work begins, because some Slurm configurations do not allow
overlapping job steps within an allocation.

Perlmutter validation also showed that a correct barriered implementation can
still be inefficient if it switches between worker-pool mode and serialized
launch mode at every ready-step boundary. Phase 2 should batch work by
execution mode: run as much eligible non-MPI work as dependencies and
resources allow while worker-pool mode is active, then run as much serialized
MPI or ineligible work as possible before switching back.

Phase 2 is expected to reveal testing and debugging issues that were not
visible in Phase 1, even though the enabling software change may be small.
The goal is therefore correctness and robust validation rather than a required
speedup threshold. Speedup is expected for workflows with enough eligible
independent work, but a successful Phase 2 primarily means that task-parallel
execution preserves Polaris results, dependency behavior, restart behavior and
resource limits on realistic workflows and target machines.

Generated job scripts that opt in to `polaris run` shall not automatically
request larger allocations in Phase 2 merely because task parallelism may be
able to use them. The existing setup-time resource sizing remains based on
the resources needed by the largest individual selected step. Users and HPC
validation runs may manually request larger allocations when they want to
measure speedup or expose multi-node concurrency, but Polaris should avoid
increasing queue cost and idle allocation size by default before the
performance behavior is better understood.

Success in Phase 2 means that `polaris run`:

- runs eligible non-MPI steps concurrently when dependencies and resources
  allow,
- keeps ineligible and MPI steps from running concurrently with other work,
- supports suites, tasks and individual steps through the Phase 1 command
  path,
- preserves exact final outputs relative to task-serial baselines,
- preserves restart and rerun behavior after failures,
- makes use of multi-node allocations by running multiple eligible steps
  concurrently,
- provides human-readable progress output for task-parallel runs, and
- works correctly on representative suites such as `omega_pr`,
  `omega_nightly` and `mpaso_pr` on Chrysalis, Perlmutter and Aurora.

## Requirements

### Requirement: Eligible Non-MPI Step Parallelism

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

Phase 2 shall enable concurrent execution of non-MPI steps that are not marked
unsafe or ineligible by the Phase 1 eligibility mechanism.

Non-MPI steps shall be considered eligible for concurrent execution by
default. Steps that are MPI steps or explicitly ineligible shall not run
concurrently with other steps in Phase 2. The exact mechanism for recording
and applying eligibility is inherited from Phase 1 and refined in the Phase 2
algorithm design and implementation.

### Requirement: Mixed MPI and Non-MPI Workflow Support

Date last modified: 2026/05/26

Contributors:

- Xylar Asay-Davis
- Codex

Phase 2 shall support workflows that contain both eligible non-MPI steps and
steps that are not eligible for concurrent execution, including MPI steps.

At any given time, Phase 2 shall run either eligible non-MPI steps in
task-parallel mode or non-eligible steps in task-serial mode. These two
execution modes shall not overlap in Phase 2. This support is required because
many Polaris workflows mix MPI and non-MPI work, and Phase 2 cannot be tested
effectively on realistic workflows without mixed-workflow support.

Within dependency and resource constraints, Phase 2 shall minimize transitions
between task-parallel worker-pool mode and task-serial launch mode. This is a
runtime-efficiency requirement, not a requirement to use a particular
worker-pool implementation. If the implementation uses Dask, Dask startup and
shutdown are the current costs being minimized; another implementation would
need to minimize the analogous worker-pool lifecycle cost.

### Requirement: Dependency-Correct Parallel Execution

Date last modified: 2026/04/28

Contributors:

- Xylar Asay-Davis
- Codex

Phase 2 shall preserve dependency-correct scheduling when eligible non-MPI
steps run concurrently.

A step shall not begin until all of its dependencies have completed
successfully. Independent steps may run concurrently, but task parallelism
shall not weaken or bypass the dependency graph established in Phase 1.

### Requirement: Restart and Rerun Behavior

Date last modified: 2026/05/30

Contributors:

- Xylar Asay-Davis
- Codex

Phase 2 shall preserve restart and rerun behavior after task-parallel
execution.

Completed steps shall remain skippable on rerun. If a step fails, dependent
steps shall not run, unrelated independent work may continue to be launched
when the scheduler can still make dependency-correct progress, and users
shall be able to resume from successfully completed work without repeating
completed steps unnecessarily.

### Requirement: Failure Isolation and Independent Progress

Date last modified: 2026/05/30

Contributors:

- Xylar Asay-Davis
- Codex

Phase 2 shall prevent dependent steps from running after a prerequisite step
fails.

A failure in one step shall not prevent independent steps from running.
Independent work that does not depend on the failed step shall remain eligible
for execution, whether or not it was already running when the failure occurred.
This Phase 2 policy intentionally favors completing unrelated work before the
run exits with a clear failure status.

### Requirement: Resource-Constrained Non-MPI Scheduling

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

Phase 2 shall schedule concurrent eligible non-MPI steps within the available
resources.

Steps eligible for parallel scheduling in Phases 1 and 2 shall specify the
resources needed for effective scheduling. Phase 2 shall use those resource
requirements to avoid oversubscribing the available allocation when running
eligible non-MPI steps concurrently.

Phase 2 shall support both atomic non-MPI steps, whose core requests are
treated as reservations for whole-step execution, and internally parallel
non-MPI steps that can use assigned worker resources for internal parallel
work.

### Requirement: Multi-Node Task Parallelism

Date last modified: 2026/04/28

Contributors:

- Xylar Asay-Davis
- Codex

Phase 2 shall be able to make use of a multi-node allocation by running
eligible non-MPI steps concurrently across the allocation.

This requirement does not mean that an individual non-MPI step must be able
to span multiple nodes. The requirement is that the collection of concurrently
running eligible steps can use more than one node.

### Requirement: Task-Parallel Output Equivalence

Date last modified: 2026/04/28

Contributors:

- Xylar Asay-Davis
- Codex

Phase 2 shall produce the same final outputs as task-serial execution for
deterministic Polaris workflows.

Representative suites shall be compared against baselines produced by
task-serial execution using the existing Polaris baseline-comparison
capability.

### Requirement: Human-Readable Parallel Progress

Date last modified: 2026/05/30

Contributors:

- Xylar Asay-Davis
- Codex

Phase 2 shall provide human-readable progress output that allows users and
developers to follow task-parallel execution.

The progress output shall make it possible to understand which steps are
running, which steps have completed, which steps are waiting and how
concurrent execution is progressing. The exact format and location of this
output are design details.

Concurrent step execution shall not depend on multiple active steps writing
to the same task log at the same time. Scheduler-managed concurrent steps
shall write deterministic per-step logs, while task logs should retain
scheduler summaries, progress messages and references to the corresponding
step-level logs.

Phase 2 shall also preserve the structured schedule and resource-event output
introduced in Phase 1 so concurrent runs can be analyzed without scraping
free-form logs.

### Requirement: Suite, Task and Step Scope

Date last modified: 2026/04/28

Contributors:

- Xylar Asay-Davis
- Codex

Phase 2 shall preserve the Phase 1 `polaris run` scope for suites, tasks and
individual steps.

Task-parallel execution is primarily meaningful for suites and multi-step
tasks. For individual-step execution, Phase 2 may avoid unnecessary
task-parallel overhead as long as the behavior remains compatible with the
`polaris run` command path.

### Requirement: Conservative Scheduling Behavior

Date last modified: 2026/04/28

Contributors:

- Xylar Asay-Davis
- Codex

Phase 2 shall schedule conservatively when resource or eligibility constraints
limit concurrency.

If Polaris cannot determine that a valid concurrent schedule exists within
the available resources, it shall reduce concurrency or fail clearly rather
than guessing. Task parallelism shall not depend on optimistic resource
assumptions.

### Requirement: Opt-In Task-Parallel Setup

Date last modified: 2026/05/30

Contributors:

- Xylar Asay-Davis
- Codex

Phase 2 shall remain opt-in from the setup workflow.

`polaris setup` and `polaris suite` shall provide a way to set up tasks and
suites to use the task-parallel infrastructure. Task-serial setup shall remain
the default in Phase 2. A later phase may change the default so task-parallel
setup becomes the normal path and task-serial setup requires an explicit
option.

When `--run_command run` is selected, generated job scripts shall use
`polaris run` but shall not automatically request a larger node allocation
than the current task-serial resource-sizing logic would request. Larger
allocations for speedup experiments remain an explicit user or validation
choice.

### Requirement: Task-Serial Compatibility

Date last modified: 2026/04/28

Contributors:

- Xylar Asay-Davis
- Codex

Phase 2 shall keep `polaris serial` available and unchanged as the
task-serial compatibility baseline.

Users shall be able to fall back to the existing serial execution path if
task-parallel execution is not appropriate for a workflow or machine.

### Requirement: Cross-Machine Phase-2 Functionality

Date last modified: 2026/04/28

Contributors:

- Xylar Asay-Davis
- Codex

Phase 2 shall function correctly on Chrysalis, Perlmutter and Aurora.

Support for all three machines is required because each may expose different
task-parallel execution challenges, including GPU-related constraints on
Perlmutter and Aurora and scheduler differences between Slurm and PBS systems.

Representative validation shall include at least the `omega_pr`,
`omega_nightly` and `mpaso_pr` suites.

### Desired: Multi-Node Non-MPI Steps

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

It would be valuable for individual non-MPI steps to be able to use resources
spanning multiple nodes, especially for large Polaris meshes. Phase 2 should
introduce and validate an internally parallel step hook with a synthetic step,
but production use by real Polaris workflows may grow later.

### Desired: Compact Graphical Progress View

Date last modified: 2026/04/28

Contributors:

- Xylar Asay-Davis
- Codex

A compact graphical or timeline-style progress view would be valuable for
understanding task-parallel execution at a glance. This could include
ASCII/text graphics in the job log.

### Desired: Measurable Speedup on Representative Suites

Date last modified: 2026/04/28

Contributors:

- Xylar Asay-Davis
- Codex

Measurable speedup on representative real suites would be valuable in Phase 2,
but it is not required. Correctness, robustness and debuggability are the
primary goals of this phase.

### Desired: Richer Scheduling Diagnostics

Date last modified: 2026/04/28

Contributors:

- Xylar Asay-Davis
- Codex

Detailed diagnostics explaining why steps were waiting, serialized, blocked or
deferred would be useful for debugging task-parallel execution, even if they
are not required in Phase 2.

## Algorithm Design

### Algorithm Design: Eligible Non-MPI Step Parallelism

Date last modified: 2026/05/26

Contributors:

- Xylar Asay-Davis
- Codex

Phase 2 should enable concurrency by changing only the scheduling policy on
top of the Phase 1 command path. The dependency graph, Dask scheduler, worker
pool, resource pool, execution-kind metadata and structured event stream
should all be inherited from Phase 1.

The Phase 1 scheduler should already own Dask phase lifetimes and control
plane resource reservations. Phase 2 should not introduce a new Dask
lifecycle model; it should only allow more than one eligible non-MPI step to
hold data-plane resources during an active non-MPI phase.

Non-MPI steps should be eligible for concurrent execution by default. Step
authors should explicitly mark steps unsafe or ineligible when they rely on
shared mutable state, external side effects, uncontrolled process launching,
fixed output locations outside the step work directory, or other behavior that
is not safe under concurrent execution.

Atomic non-MPI steps should run as whole-step Dask tasks. Dask-aware non-MPI
steps should run coordinating code through the separate Dask-aware execution
hook and use an assigned Dask client and worker resources for internal work.

### Algorithm Design: Mixed MPI and Non-MPI Workflow Support

Date last modified: 2026/05/26

Contributors:

- Xylar Asay-Davis
- Codex

Phase 2 should use a barriered mixed-workflow policy. When eligible non-MPI
steps are active, no MPI or ineligible step should start. When an MPI or
ineligible step is selected, the task-parallel worker pool should be drained
and stopped before that serialized step runs. Worker-pool mode should restart
only when the next eligible non-MPI batch is ready to make progress.

The scheduler should avoid a strict ready-order policy that alternates between
worker-pool and serialized modes for every independent step. Instead, it
should maintain a current execution mode and continue selecting ready work
from that mode until no such work can make progress. Then it may switch modes,
paying the worker-pool startup or shutdown cost once for the next batch. This
batching policy may reorder independent ready steps across modes, but it must
not violate dependency order, resource feasibility or deterministic
tie-breaking within a mode.

### Algorithm Design: Dependency-Correct Parallel Execution

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

The Phase 1 graph remains the source of truth. A step may enter the ready set
only after all selected prerequisites have completed successfully or have been
recognized as already completed or cached. Parallel execution should not
weaken input checks inside the step lifecycle; missing required runtime inputs
remain step failures, not dependency-discovery signals.

Dependents of a failed step should be blocked immediately. Independent ready
work may continue if it does not depend on the failed step and if the selected
failure policy allows the run to continue.

### Algorithm Design: Restart and Rerun Behavior

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

Completed-step markers and validation markers should remain the restart
interface. On rerun, the scheduler should include completed nodes in graph
validation but should treat them as already satisfied rather than resubmitting
them to Dask.

If a Phase 2 run fails after independent work has completed, the next run
should reuse all successfully completed steps and schedule only remaining
eligible work. Structured events from the failed run should make it clear
which steps completed, failed, were blocked, or were never started.

### Algorithm Design: Failure Isolation and Independent Progress

Date last modified: 2026/05/30

Contributors:

- Xylar Asay-Davis
- Codex

When one concurrently running non-MPI step fails, Polaris should mark that
step failed, block its dependents and release its reserved resources.
Independent steps that are already running may finish. Independent ready steps
may continue to be scheduled if they do not depend on the failed step.

The scheduler should not cancel unrelated running steps merely because one
concurrent step failed, and it should continue launching unrelated ready work
if dependency and resource constraints allow. The final run result should
still fail clearly if any selected step fails or if any selected dependent
step is blocked by a failure.

### Algorithm Design: Resource-Constrained Non-MPI Scheduling

Date last modified: 2026/05/30

Contributors:

- Xylar Asay-Davis
- Codex

The Phase 2 scheduler should use greedy stable packing. It should walk ready
eligible non-MPI steps in deterministic Phase 1 order, start each step whose
target resources fit in the current resource pool and defer steps that do not
fit. If a step's minimum resources cannot be satisfied by the allocation,
`polaris run` should fail clearly rather than silently weakening the minimum.

For atomic non-MPI steps, requested cores are reservations that prevent
oversubscription. They do not guarantee that the step's Python code is spread
across multiple Dask workers. For Dask-aware steps, the resource lease should
provide access to the assigned Dask client and worker capacity so the step can
submit internal Dask work.

The scheduler should not reinterpret this packing policy as permission to
increase the batch allocation requested by setup-generated job scripts. Phase
2 scheduling operates within the allocation it is given. If a user wants more
eligible steps to fit concurrently, the user can request a larger allocation
outside the automatic setup defaults.

### Algorithm Design: Multi-Node Task Parallelism

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

The required Phase 2 multi-node capability is that concurrently running
eligible non-MPI steps can collectively use worker resources on more than one
node. The default worker topology should be multiple single-threaded worker
processes per allocated node, with schedulable capacity based on physical
cores.

An individual Dask-aware non-MPI step may also use workers on more than one
node through its assigned Dask client. Phase 2 should validate this capability
with a synthetic step, while real workflow adoption can remain incremental.

### Algorithm Design: Task-Parallel Output Equivalence

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

Output equivalence should be evaluated against task-serial baselines for
deterministic workflows. Any difference introduced by Phase 2 should be
treated as a concurrency, dependency, resource, or lifecycle bug unless the
workflow is explicitly nondeterministic.

Concurrent execution should not change baseline comparison semantics. Each
step should still validate its own outputs and record the same pass/fail
markers as in task-serial execution.

### Algorithm Design: Human-Readable Parallel Progress

Date last modified: 2026/05/30

Contributors:

- Xylar Asay-Davis
- Codex

Human-readable progress should show which steps are running, completed,
failed, blocked, waiting for dependencies, waiting for resources, or
serialized by policy. It should also summarize worker-pool state at the start
and end of non-MPI phases and before/after serialized MPI or ineligible
steps.

Task-level logs should remain readable scheduler records rather than becoming
the shared output stream for concurrently running step code. Concurrent
step-level output should be written to deterministic per-step logs so a
failure can be traced without relying on interleaved task-log output.

Structured events should remain the authoritative diagnostic record. They
should include enough timing and resource information to prove that eligible
steps overlapped and that MPI/ineligible work did not overlap with non-MPI
work in Phase 2. They should also make worker-pool lifecycle overhead visible:
launch requested, client/runtime ready, shutdown requested, stopped and
duration fields should be sufficient to estimate the cost of each mode
transition. Human-readable summaries should report the number of worker-pool
phases, total startup and shutdown time, mean and maximum startup and
shutdown time and the fraction of suite wall time spent managing the
task-parallel runtime. If the implementation uses Dask, the report should
label these as Dask lifecycle timings.

### Algorithm Design: Suite, Task and Step Scope

Date last modified: 2026/05/26

Contributors:

- Xylar Asay-Davis
- Codex

Suites and multi-step tasks should use the full Phase 2 scheduler. Individual
step execution should still use the `polaris run` command path for
compatibility, but it may avoid unnecessary graph complexity because there is
only one selected node.

The same execution-kind metadata, resource checks, task-parallel runtime
lifecycle and structured event output should apply at all scopes so behavior
remains consistent and debuggable.

### Algorithm Design: Conservative Scheduling Behavior

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

The scheduler should prefer clear serialization or clear failure over
optimistic concurrency. If execution kind, resource requirements, worker-pool
state, or graph validity cannot be determined, the affected step should not be
run concurrently.

The first implementation should avoid complex bin packing. Greedy stable
packing is easier to reason about, deterministic and sufficient to expose
most Phase 2 correctness issues.

### Algorithm Design: Opt-In Task-Parallel Setup

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

N/A. Phase 2 setup remains an opt-in workflow policy. The algorithmic design
assumes a work directory has already been configured for `polaris run`.

### Algorithm Design: Task-Serial Compatibility

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

N/A. `polaris serial` remains the compatibility baseline and should not share
the Dask orchestration path unless a later design explicitly changes that
policy.

### Algorithm Design: Cross-Machine Phase-2 Functionality

Date last modified: 2026/05/30

Contributors:

- Xylar Asay-Davis
- Codex

The algorithm should avoid per-step scheduler launches for non-MPI work.
Instead, Polaris should acquire a batch allocation, reserve control-plane
resources, start allocation-scoped Dask environments only for eligible
non-MPI phases, and schedule Python work internally while that phase is
active. This design is meant to reduce launch pressure on systems such as
Perlmutter and keep Slurm/PBS differences isolated from normal non-MPI step
scheduling.

Machine-specific configuration should provide physical core counts, node
counts, GPU counts where relevant and worker-launch details. The scheduling
policy should be the same on Chrysalis, Perlmutter and Aurora.

Initial Phase 2 HPC validation should begin with the `omega_pr` suite on
Chrysalis because it is short and Chrysalis queue times are typically modest.
When the synthetic and Chrysalis `omega_pr` evidence is stable, validation
should broaden to additional Chrysalis suites and then to Perlmutter and
Aurora, where GPU and scheduler differences are more likely to expose
machine-specific issues.

### Algorithm Design: Multi-Node Non-MPI Steps

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

The Dask-aware step hook should be the path for individual non-MPI steps that
need workers across multiple nodes. Phase 2 should prove the hook with a
synthetic step but does not require a production Polaris workflow to adopt it
immediately.

### Algorithm Design: Compact Graphical Progress View

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

N/A. A compact graphical or timeline-style view can be derived later from the
structured event stream without changing the core scheduling algorithm.

### Algorithm Design: Measurable Speedup on Representative Suites

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

Speedup should be reported when available, but the Phase 2 algorithm should
optimize first for correctness, determinism and debuggability. Greedy stable
packing may leave some performance on the table compared with more complex
scheduling, but it reduces early implementation and validation risk.

### Algorithm Design: Richer Scheduling Diagnostics

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

Richer diagnostics should be produced from the same structured event stream
used for required observability. Waiting reasons should distinguish dependency
blocks, resource limits, execution-kind barriers, ineligibility, failed
prerequisites and worker-pool transitions.

## Testing

### Testing and Validation: Phase-2 Non-MPI Parallelism

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

Synthetic integration tests should include independent non-MPI steps that
sleep or perform controlled work long enough to prove overlap. Tests should
verify that eligible non-MPI steps run concurrently when resources allow and
that MPI or explicitly ineligible steps do not overlap with other work.

Unit tests should cover default non-MPI eligibility, explicit unsafe metadata,
execution-kind overrides and deterministic greedy packing.

### Testing and Validation: Dask-Aware Non-MPI Steps

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

Phase 2 should include a synthetic Dask-aware step that receives an assigned
Dask client/resource lease and submits internal work to multiple workers. The
test should verify that the step can use more than one worker, that the worker
allocation is reflected in structured events and that ordinary atomic steps
continue to use whole-step execution.

### Testing and Validation: Phase-2 Correctness and Restart

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

Tests should cover failure isolation, blocked dependents, independent progress
after failure, rerun from completed work and preservation of baseline/property
markers. Output-equivalence tests should compare Phase 2 results with
task-serial baselines for deterministic synthetic workflows and representative
real suites.

### Testing and Validation: Cross-Machine Phase-2 Functionality

Date last modified: 2026/05/30

Contributors:

- Xylar Asay-Davis
- Codex

HPC validation should target Chrysalis, Perlmutter and Aurora using synthetic
parallel workflows and representative suites including `omega_pr`,
`omega_nightly` and `mpaso_pr`. Validation should record step overlap,
worker-pool transitions, worker-pool lifecycle timing, resource reservations,
failures, resume behavior and serial-vs-parallel wall time when meaningful.
On systems where worker-pool startup and shutdown are expensive, validation
should include the number of execution-mode switches and an estimate of how
much task-parallel speedup is needed to amortize that overhead.

Validation should start with `omega_pr` on Chrysalis. Broader validation is
needed after the first overlap and failure-isolation behavior is working:
`omega_nightly` and `mpaso_pr` on Chrysalis should increase suite coverage,
and `omega_pr` on Perlmutter and Aurora should test GPU-capable and PBS-based
execution environments. Larger-than-default allocations may be requested
manually for these validation runs when they are needed to demonstrate
resource-limited concurrency.
