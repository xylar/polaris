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

The central new capability in Phase 2 is that independent non-MPI steps that
Phase 1 identifies as eligible for task parallelism may run at the same time,
subject to dependency and resource constraints. Steps that are not eligible
for concurrent execution, including MPI steps, remain task serial in this
phase. Mixed workflows are still required because many Polaris suites contain
both MPI and non-MPI work, but Phase 2 uses a conservative barriered model:
at any given time, Polaris runs either eligible non-MPI work in task-parallel
mode or non-eligible work in task-serial mode.

Phase 2 is expected to reveal testing and debugging issues that were not
visible in Phase 1, even though the enabling software change may be small.
The goal is therefore correctness and robust validation rather than a required
speedup threshold. Speedup is expected for workflows with enough eligible
independent work, but a successful Phase 2 primarily means that task-parallel
execution preserves Polaris results, dependency behavior, restart behavior and
resource limits on realistic workflows and target machines.

Success in Phase 2 means that `polaris run`:

- runs eligible non-MPI steps concurrently when dependencies and resources
  allow,
- keeps ineligible, unclassified and MPI steps from running concurrently with
  other work,
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

Date last modified: 2026/04/28

Contributors:

- Xylar Asay-Davis
- Codex

Phase 2 shall enable concurrent execution of non-MPI steps that the Phase 1
eligibility mechanism identifies as safe for task parallelism.

Steps that are MPI steps, ineligible or unclassified shall not run
concurrently with other steps in Phase 2. The exact mechanism for recording
and applying eligibility is inherited from Phase 1 and refined in the Phase 2
algorithm design and implementation.

### Requirement: Mixed MPI and Non-MPI Workflow Support

Date last modified: 2026/04/28

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

Date last modified: 2026/04/28

Contributors:

- Xylar Asay-Davis
- Codex

Phase 2 shall preserve restart and rerun behavior after task-parallel
execution.

Completed steps shall remain skippable on rerun. If a step fails, dependent
steps shall not run, and users shall be able to resume from successfully
completed work without repeating completed steps unnecessarily.

### Requirement: Failure Isolation and Independent Progress

Date last modified: 2026/04/28

Contributors:

- Xylar Asay-Davis
- Codex

Phase 2 shall prevent dependent steps from running after a prerequisite step
fails.

A failure in one step shall not prevent independent steps from running.
Independent work that does not depend on the failed step shall remain eligible
for execution, whether or not it was already running when the failure occurred.

### Requirement: Resource-Constrained Non-MPI Scheduling

Date last modified: 2026/04/28

Contributors:

- Xylar Asay-Davis
- Codex

Phase 2 shall schedule concurrent eligible non-MPI steps within the available
resources.

Steps eligible for parallel scheduling in Phases 1 and 2 shall specify the
resources needed for effective scheduling. Phase 2 shall use those resource
requirements to avoid oversubscribing the available allocation when running
eligible non-MPI steps concurrently.

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

Date last modified: 2026/04/28

Contributors:

- Xylar Asay-Davis
- Codex

Phase 2 shall provide human-readable progress output that allows users and
developers to follow task-parallel execution.

The progress output shall make it possible to understand which steps are
running, which steps have completed, which steps are waiting and how
concurrent execution is progressing. The exact format and location of this
output are design details.

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

Date last modified: 2026/04/28

Contributors:

- Xylar Asay-Davis
- Codex

Phase 2 shall remain opt-in from the setup workflow.

`polaris setup` and `polaris suite` shall provide a way to set up tasks and
suites to use the task-parallel infrastructure. Task-serial setup shall remain
the default in Phase 2. A later phase may change the default so task-parallel
setup becomes the normal path and task-serial setup requires an explicit
option.

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

Date last modified: 2026/04/28

Contributors:

- Xylar Asay-Davis
- Codex

It would be valuable for individual non-MPI steps to be able to use resources
spanning multiple nodes, especially for large Polaris meshes. This is not
required for Phase 2, where the required multi-node capability is at the level
of concurrently running multiple eligible steps.

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
