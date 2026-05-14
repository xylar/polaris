# Task Parallelism in Polaris: Phase 4

Creation date: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

## Summary

Phase 4 removes the barrier between MPI and non-MPI execution phases that was preserved
through Phases 2 and 3. In Phase 4, eligible non-MPI steps and MPI steps may run
simultaneously within the same allocation when their dependencies are satisfied and resources
can be safely partitioned between them. This is the most capable and most complex execution
mode in the task-parallel roadmap.

Resource management in Phase 4 is fully dynamic. Polaris tracks all resource usage — CPUs,
GPUs, nodes, and memory — and schedules any ready step into whatever resources are currently
available, regardless of whether the step is a non-MPI or MPI step. When both kinds of work
are ready and competing for the same resources, MPI steps are given scheduling priority.
This priority reflects the typical structure of Polaris workflows, where MPI steps are
usually on the critical path and non-MPI steps are good candidates for filling spare capacity.

Phase 4 introduces memory as a required schedulable resource. All steps that participate in
Phase 4 concurrent execution must declare their memory requirements, and the scheduler must
avoid memory oversubscription when steps run simultaneously. This requirement was deferred
from Phase 3 because the oversubscription risk from concurrent non-MPI and MPI work, which
may use memory in very different ways, is higher than from concurrent MPI-only work.

Resource isolation in Phase 4 is enforced by the scheduler: MPI and non-MPI steps shall
never share the same CPUs, GPUs, or nodes. The scheduler is responsible for guaranteeing this
non-overlap at the time of scheduling rather than relying on operating system isolation alone.

The Phase 3 barriered execution model is retired in Phase 4. Phase 4 does not fall back to
barriered execution when the full concurrent schedule cannot be achieved; instead, it degrades
by serializing steps whose resource requirements conflict until resources become available.
Phase 4 remains opt-in through the setup workflow; making task-parallel execution the new
default is a desired capability rather than a requirement of this phase.

Success in Phase 4 means that `polaris run`:

- runs eligible non-MPI and MPI steps simultaneously when dependencies and resources allow,
- schedules MPI steps before non-MPI steps when both are ready and competing for resources,
- guarantees that MPI and non-MPI steps never share CPUs, GPUs, or nodes,
- uses declared memory requirements to avoid memory oversubscription for all concurrent steps,
- tracks all resource usage dynamically and schedules steps as resources become available,
- preserves dependency correctness, restart and rerun behavior, and exact final outputs, and
- works correctly on Chrysalis, Perlmutter and Aurora with a higher validation bar than
  Phase 3, demonstrating that simultaneous MPI and non-MPI execution is correct on all three.

## Requirements

### Requirement: Concurrent Non-MPI and MPI Execution

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Phase 4 shall allow eligible non-MPI steps and MPI steps to run simultaneously within the
same allocation when their dependencies are satisfied and sufficient resources are available.

Eligibility for non-MPI steps is inherited from Phase 2. MPI step eligibility is inherited
from Phase 3. Steps that are ineligible for concurrent execution shall continue to be
serialized according to the rules established in earlier phases.

### Requirement: Dynamic Resource Partitioning

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Phase 4 shall use dynamic resource partitioning to schedule concurrent steps.

Polaris shall track all resource usage — CPUs, GPUs, nodes, and memory — and shall schedule
any ready step into whatever resources are currently available. The allocation shall not be
divided into a fixed partition for MPI work and a fixed partition for non-MPI work. Instead,
the available resource pool shall be updated as steps start and finish, and new steps shall be
scheduled whenever resources become available and ready steps are waiting.

### Requirement: MPI Step Scheduling Priority

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

When both MPI steps and non-MPI steps are ready and competing for the same resources, Phase 4
shall schedule MPI steps first.

This priority does not prevent non-MPI steps from running concurrently with MPI steps. It
means that, under contention, the scheduler shall prefer to assign available resources to
ready MPI steps before assigning them to ready non-MPI steps. Non-MPI steps shall run when
resources remain available after MPI scheduling decisions are made.

### Requirement: Scheduler-Enforced Resource Non-Overlap

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Phase 4 shall guarantee that MPI and non-MPI steps never share the same CPUs, GPUs, or nodes
at the same time.

The scheduler shall enforce this non-overlap at the time of scheduling, before a step is
launched. Polaris shall not rely solely on operating-system isolation or job-scheduler
defaults to prevent resource sharing between concurrent MPI and non-MPI work.

If the scheduler cannot find a valid assignment that avoids resource overlap, it shall not
launch the competing step. It shall instead defer the step until resources become available.

### Requirement: Memory-Aware Scheduling

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Phase 4 shall introduce memory as a required schedulable resource for all steps that
participate in concurrent execution.

All steps — both MPI and non-MPI — that run in Phase 4 concurrent mode shall declare their
memory requirements. The scheduler shall use those requirements to avoid memory
oversubscription when steps run simultaneously. If the declared memory requirements of all
ready steps cannot be satisfied concurrently, Polaris shall defer steps until sufficient
memory is available.

Steps that do not declare a memory requirement shall not be eligible to participate in Phase 4
concurrent execution with other steps.

### Requirement: Dependency-Correct Execution

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Phase 4 shall preserve dependency-correct scheduling when non-MPI and MPI steps run
concurrently.

A step shall not begin until all of its dependencies have completed successfully. Concurrent
execution shall not weaken or bypass the dependency graph established in Phase 1.

### Requirement: Restart and Rerun Behavior

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Phase 4 shall preserve restart and rerun behavior after concurrent execution.

Completed steps shall remain skippable on rerun. If a step fails, dependent steps shall not
run, and users shall be able to resume from successfully completed work without repeating
completed steps unnecessarily.

### Requirement: Failure Isolation and Independent Progress

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Phase 4 shall prevent dependent steps from running after a prerequisite step fails.

A failure in one step shall not prevent independent steps from running, regardless of whether
the failing step and the independent step are both MPI, both non-MPI, or one of each.
Independent work that does not depend on the failed step shall remain eligible for execution.

### Requirement: Task-Parallel Output Equivalence

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Phase 4 shall produce the same final outputs as task-serial execution for deterministic Polaris
workflows.

Representative suites shall be compared against baselines produced by task-serial execution
using the existing Polaris baseline-comparison capability.

### Requirement: Cross-Machine Phase-4 Functionality

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Phase 4 shall function correctly on Chrysalis, Perlmutter and Aurora, with a higher validation
bar than Phase 3.

Higher validation bar means that simultaneous MPI and non-MPI execution must be demonstrated
to be correct on all three machines, not only that the command runs without error. Validation
shall include resource accounting, memory scheduling, isolation enforcement and output
equivalence. Representative validation shall include at least the `omega_pr`, `omega_nightly`
and `mpaso_pr` suites.

### Requirement: Opt-In Task-Parallel Setup

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Phase 4 shall remain opt-in from the setup workflow.

`polaris setup` and `polaris suite` shall continue to provide a way to set up tasks and suites
to use the task-parallel infrastructure. Task-serial setup shall remain the default in Phase 4.

### Requirement: Task-Serial Compatibility

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Phase 4 shall keep `polaris serial` available and unchanged as the task-serial compatibility
baseline.

Users shall be able to fall back to the existing serial execution path if task-parallel
execution is not appropriate for a workflow or machine.

### Desired: Frontier Support

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Frontier support and validation in Phase 4 would be valuable, even if it is not required.

Full concurrent non-MPI and MPI execution is the most demanding portability target in the
task-parallel roadmap. Validating Phase 4 on Frontier would confirm that the dynamic resource
partitioning and isolation mechanisms work correctly on its job launch environment.

### Desired: Path to Task-Parallel Default

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

A mechanism for making `polaris run` the default execution path — so that task-parallel
execution does not require an explicit opt-in — would be valuable in or after Phase 4.

This would involve `polaris setup` and `polaris suite` using the task-parallel infrastructure
by default, with `polaris serial` remaining available as an explicit opt-out for users who
need the original serial execution path. Whether this transition happens at the end of Phase 4
or in a later phase is a decision for the relevant design documents.

## Algorithm Design

### Algorithm Design: Phase-1/2 Architecture Alignment

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

Phase 4 should remove the Phase 2-3 MPI/non-MPI barrier by changing the
scheduling policy, not by replacing the core architecture. The dependency
graph, execution-kind metadata, dynamic resource pool, Dask worker lifecycle
and structured schedule/resource events from earlier phases should remain the
foundation.

The Phase 1 dynamic resource-pool model should become fully active in Phase 4:
ready MPI and non-MPI steps should draw from the same pool, resources should
be released as steps finish and the scheduler should launch newly feasible
work without waiting for a full barrier. MPI priority under contention should
be implemented as a scheduling policy on this shared pool.

The Dask-aware non-MPI step hook introduced in Phase 1-2 should remain the
path for Python steps that can consume multiple workers. Phase 4 should extend
the resource lease attached to that hook with any memory, CPU, GPU or node
isolation data needed to prevent overlap with concurrently running MPI work.

## Testing

### Testing and Validation: Concurrent Non-MPI and MPI Execution

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Integration tests shall verify that eligible non-MPI and MPI steps run simultaneously when
resources allow and that steps that are ineligible for concurrent execution continue to be
serialized. Tests shall include workflows that mix MPI and non-MPI work across multiple
concurrent phases.

HPC tests shall use controlled synthetic workloads to demonstrate simultaneous MPI and non-MPI
execution on Chrysalis, Perlmutter and Aurora and shall record step timing and overlap.

### Testing and Validation: Dynamic Resource Partitioning

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Unit tests shall verify that the resource pool is updated correctly as steps start and finish
and that newly available resources are assigned to waiting steps promptly. Tests shall cover
cases where multiple steps finish at different times and cases where resources become
fragmented.

Integration tests shall confirm that dynamic scheduling does not miss opportunities to run
ready steps concurrently and that resource accounting remains consistent throughout a run.

### Testing and Validation: MPI Step Scheduling Priority

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Unit tests shall verify that the scheduler assigns available resources to ready MPI steps
before ready non-MPI steps when both are waiting and resources are limited. Tests shall
include cases where there are enough resources for all waiting steps and cases where only a
subset can run concurrently.

### Testing and Validation: Scheduler-Enforced Resource Non-Overlap

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Unit tests shall verify that the scheduler never assigns the same CPU, GPU, or node to
concurrently running MPI and non-MPI steps. Tests shall include adversarial cases designed to
expose any gap between logical resource accounting and actual resource assignment.

HPC tests shall use synthetic steps that report their assigned resources so that non-overlap
can be verified empirically on each target machine.

### Testing and Validation: Memory-Aware Scheduling

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Unit tests shall verify that the scheduler avoids memory oversubscription when steps run
concurrently, that steps without declared memory requirements are not eligible for Phase 4
concurrent execution, and that the scheduler correctly defers steps when insufficient memory
is available.

Integration tests shall use synthetic steps with varying memory requirements to confirm that
concurrent scheduling respects memory limits in practice.

### Testing and Validation: Cross-Machine Phase-4 Functionality

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

HPC validation shall be conducted on Chrysalis, Perlmutter and Aurora using at least the
`omega_pr`, `omega_nightly` and `mpaso_pr` suites. Validation shall confirm resource
accounting, memory scheduling, isolation enforcement, output equivalence, failure behavior and
resume behavior on each machine.

Results shall be compared with the Phase 3 barriered baseline to assess both the correctness
and the performance benefit of Phase 4 concurrent execution. Any machine-specific limitations
that affect the viability of simultaneous MPI and non-MPI execution shall be documented.
