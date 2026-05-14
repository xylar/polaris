# Task Parallelism in Polaris: Phase 3

Creation date: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

## Summary

Phase 3 builds directly on the `polaris run` infrastructure from Phase 2. It enables
independent MPI steps to run concurrently when their dependencies are satisfied and sufficient
resources are available. The barriered execution model from Phase 2 is preserved and extended:
at any given time, Polaris runs either eligible non-MPI steps in task-parallel mode or MPI
steps in task-parallel mode, but the two kinds do not yet overlap. The key advance over Phase 2
is that MPI steps are no longer serialized — multiple independent MPI steps may now run
simultaneously, subject to dependency and resource constraints.

All MPI steps are eligible for concurrent execution by default. Step authors may explicitly
mark a step as unsafe for concurrent MPI execution if the step is known to behave incorrectly
when run alongside other MPI work. This conservative default — eligible unless marked unsafe
— is the inverse of the Phase 2 non-MPI eligibility model, which was opt-in. The different
default reflects that MPI steps already express their resource requirements (tasks, cores, and
now GPUs) in a form that makes safe partitioning straightforward when scheduler support is
available.

Resource isolation for concurrent MPI steps defaults to core-level partitioning: each step is
assigned a non-overlapping subset of CPU cores (and GPUs where applicable), which may share a
physical node with another concurrent MPI step. Core-level partitioning is particularly
important for regression testing workloads that include many small MPI steps, where requiring
exclusive node access would be highly wasteful. When the job scheduler or machine configuration
cannot support core-level partitioning, Polaris falls back to node-level partitioning, giving
each concurrent MPI step exclusive access to one or more nodes.

Phase 3 extends the existing resource model with a GPU count attribute so the scheduler can
avoid GPU oversubscription when concurrent MPI steps each require GPUs. Memory-aware scheduling
is deferred to Phase 4, consistent with the broader resource-model roadmap.

The MPI launch mechanism shall be portable across the Slurm and PBS environments used by the
target machines. Specifically, Polaris shall be able to direct each concurrent MPI step to its
assigned subset of resources on Chrysalis (Slurm), Perlmutter (Slurm) and Aurora (PBS). The
exact launchers and flags used are implementation details.

Phase 3 is expected to deliver measurable speedup on workflows that contain many independent
MPI steps, though correctness and robustness remain the primary goals. The phase is validated
on the same representative suites as Phase 2.

Success in Phase 3 means that `polaris run`:

- runs independent MPI steps concurrently when dependencies and resources allow,
- applies core-level resource partitioning where the scheduler supports it, with automatic
  fallback to node-level partitioning otherwise,
- runs the largest feasible concurrent subset of ready MPI steps and serializes the rest when
  resources are insufficient for full concurrency,
- preserves the Phase 2 barriered model so that non-MPI and MPI execution phases do not
  overlap,
- accounts for GPU count in addition to CPU resources when scheduling concurrent MPI steps,
- preserves dependency correctness, restart and rerun behavior, and exact final outputs,
- launches MPI steps on specific resource subsets portably across Slurm and PBS systems, and
- works correctly on representative suites such as `omega_pr`, `omega_nightly` and `mpaso_pr`
  on Chrysalis, Perlmutter and Aurora.

## Requirements

### Requirement: Concurrent MPI Step Execution

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Phase 3 shall enable independent MPI steps to run concurrently when their dependencies are
satisfied and sufficient resources are available.

All MPI steps shall be eligible for concurrent execution by default. Step authors shall be able
to explicitly mark a step as unsafe for concurrent MPI execution; such steps shall be
serialized rather than scheduled concurrently. Unclassified MPI steps shall be treated as
eligible rather than as unsafe.

### Requirement: Resource Isolation for Concurrent MPI

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Phase 3 shall enforce non-overlapping resource assignments for concurrently running MPI steps.

The preferred isolation model is core-level partitioning: concurrent MPI steps may share
physical nodes but shall be assigned non-overlapping CPU core (and GPU) subsets so that they
do not compete for the same processing resources. When the job scheduler or machine
configuration cannot support core-level partitioning, Polaris shall fall back to node-level
partitioning, in which each concurrent MPI step is given exclusive access to one or more nodes.

Polaris shall detect which isolation model is applicable for a given machine and scheduler
configuration and apply it consistently. If neither core-level nor node-level isolation can be
achieved for a given combination of concurrent steps, Polaris shall not run them concurrently.

### Requirement: Extended Resource Model for GPU Count

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Phase 3 shall extend the existing MPI step resource model with a GPU count attribute so that
steps can declare how many GPUs they require.

The GPU count shall be treated as a schedulable resource alongside the existing CPU-related
attributes (ntasks, cpus_per_task, min_tasks, openmp_threads). The scheduler shall use GPU
count to avoid GPU oversubscription when running concurrent MPI steps on GPU-equipped machines.
Steps that do not declare a GPU count shall be treated as requiring no GPUs.

Memory-aware scheduling is not required in Phase 3. The Phase 3 resource model shall not
preclude later addition of memory as a schedulable resource.

### Requirement: Barriered MPI and Non-MPI Execution

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Phase 3 shall preserve the barriered execution model from Phase 2.

At any given time, `polaris run` shall run either eligible non-MPI steps in task-parallel mode
or MPI steps in task-parallel mode, but not both simultaneously. When ready MPI steps and ready
non-MPI steps are both available, Polaris shall complete the current task-parallel phase before
beginning the other kind of work.

This requirement does not change the Phase 2 non-MPI task-parallel behavior. It extends the
Phase 2 MPI phase from task-serial to task-parallel while keeping the MPI and non-MPI phases
separate. Concurrent non-MPI and MPI execution is deferred to Phase 4.

### Requirement: Resource-Constrained MPI Scheduling

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Phase 3 shall schedule the largest feasible concurrent subset of ready MPI steps within the
available resources.

When the combined resource requirements of all ready MPI steps exceed what is available, Polaris
shall run as many as will fit and defer the remaining ready steps until resources become
available. Polaris shall not require full concurrency to be achievable; it shall degrade
gracefully by serializing steps that cannot run alongside the currently executing set.

If a step's minimum resource requirements cannot be satisfied by the available allocation,
Polaris shall report that the step cannot run rather than weakening its resource requirements
below the declared minimum.

### Requirement: Portable MPI Launch Mechanism

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Phase 3 shall support launching MPI steps on specific subsets of nodes or cores across the
Slurm and PBS job scheduling environments used by target machines.

Polaris shall be able to direct each concurrent MPI step to its assigned resource subset on
Chrysalis (Slurm), Perlmutter (Slurm) and Aurora (PBS). The exact launchers, flags and
mechanisms used are implementation details and shall not be specified in this requirement. The
requirement is that isolation and resource targeting are achieved portably across these
environments, not that a single launcher or interface is used.

### Requirement: Dependency-Correct Concurrent MPI Execution

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Phase 3 shall preserve dependency-correct scheduling when MPI steps run concurrently.

A step shall not begin until all of its dependencies have completed successfully. Independent
MPI steps may run concurrently, but task parallelism shall not weaken or bypass the dependency
graph established in Phase 1.

### Requirement: Restart and Rerun Behavior

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Phase 3 shall preserve restart and rerun behavior after concurrent MPI execution.

Completed steps shall remain skippable on rerun. If a step fails, dependent steps shall not
run, and users shall be able to resume from successfully completed work without repeating
completed steps unnecessarily.

### Requirement: Failure Isolation and Independent Progress

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Phase 3 shall prevent dependent steps from running after a prerequisite step fails.

A failure in one MPI step shall not prevent independent MPI steps from running. Independent
work that does not depend on the failed step shall remain eligible for execution, whether or
not it was already running when the failure occurred.

### Requirement: Task-Parallel Output Equivalence

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Phase 3 shall produce the same final outputs as task-serial execution for deterministic Polaris
workflows.

Representative suites shall be compared against baselines produced by task-serial execution
using the existing Polaris baseline-comparison capability.

### Requirement: Cross-Machine Phase-3 Functionality

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Phase 3 shall function correctly on Chrysalis, Perlmutter and Aurora.

Support for all three machines is required because each exposes different constraints for
concurrent MPI execution, including GPU-related considerations on Perlmutter and Aurora and
scheduler differences between Slurm and PBS. Representative validation shall include at least
the `omega_pr`, `omega_nightly` and `mpaso_pr` suites.

### Requirement: Opt-In Task-Parallel Setup

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Phase 3 shall remain opt-in from the setup workflow.

`polaris setup` and `polaris suite` shall continue to provide a way to set up tasks and suites
to use the task-parallel infrastructure. Task-serial setup shall remain the default in Phase 3.

### Requirement: Task-Serial Compatibility

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Phase 3 shall keep `polaris serial` available and unchanged as the task-serial compatibility
baseline.

Users shall be able to fall back to the existing serial execution path if task-parallel
execution is not appropriate for a workflow or machine.

### Desired: Frontier Support

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Frontier support and validation in Phase 3 would be valuable, even if it is not required.

Frontier is of particular interest for concurrent MPI execution because its job launch
constraints may affect how resource subsets are targeted. Validating Phase 3 on Frontier early
would reduce the risk of unexpected portability issues in later phases.

### Desired: Speedup Reporting

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Automated speedup reporting comparing concurrent MPI execution to the Phase 2 barriered
task-serial MPI baseline would be valuable in Phase 3.

Such reporting would make it easy to quantify the benefit of Phase 3 on representative
workflows and would provide a reference point for evaluating further improvements in Phase 4.

## Algorithm Design

### Algorithm Design: Phase-1/2 Architecture Alignment

Date last modified: 2026/05/14

Contributors:

- Xylar Asay-Davis
- Codex

Phase 3 should extend the dynamic resource-pool model, execution-kind metadata
and structured schedule/resource events introduced in Phases 1 and 2. It
should not introduce a separate scheduler architecture for MPI work. Instead,
MPI scheduling should become another policy applied to the same dependency
graph and resource pool.

The Phase 2 barrier between MPI and non-MPI execution should remain in Phase
3. Eligible non-MPI phases should continue to use the Dask worker-pool
lifecycle from Phase 2. MPI phases should add concurrent MPI resource
partitioning and launch behavior while keeping non-MPI workers stopped during
MPI execution.

Execution-kind metadata from Phase 1 should be the source of truth for
distinguishing MPI, non-MPI and explicitly ineligible steps. The structured
event stream should be extended to record MPI resource assignments,
partitioning mode and launch details so Phase 3 diagnostics are comparable
with Phase 2 diagnostics.

## Testing

### Testing and Validation: Concurrent MPI Step Execution

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Unit tests shall verify that MPI steps are treated as eligible for concurrent execution by
default and that steps marked unsafe are serialized. Tests shall confirm that the eligibility
mechanism correctly identifies concurrent-capable and non-concurrent steps.

Integration tests shall use synthetic MPI steps with controlled resource requirements to
demonstrate concurrent execution on representative workflows. These tests shall verify that
independent MPI steps begin before their predecessors finish and that dependent steps wait
correctly.

### Testing and Validation: Resource Isolation for Concurrent MPI

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Unit tests shall verify that core-level partitioning produces non-overlapping resource
assignments and that the fallback to node-level partitioning is triggered correctly when the
scheduler cannot support core-level isolation.

HPC tests shall verify that concurrent MPI steps do not share CPU cores or GPUs on each target
machine, using synthetic steps that report their assigned resources. Tests shall confirm that
the appropriate isolation model is applied on each machine.

### Testing and Validation: Extended Resource Model for GPU Count

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Unit tests shall verify GPU count resource accounting, including cases where all concurrent
steps fit within the available GPUs and cases where GPU oversubscription would occur. Tests
shall confirm that the scheduler avoids GPU oversubscription and defers steps when necessary.

HPC tests on GPU-equipped machines (Perlmutter, Aurora) shall verify that concurrent MPI steps
do not share GPUs and that GPU resource accounting is correct.

### Testing and Validation: Barriered MPI and Non-MPI Execution

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Integration tests shall verify that MPI and non-MPI execution phases do not overlap. Tests
shall include workflows that alternate between non-MPI and MPI phases, and shall confirm that
no MPI step begins while non-MPI steps are still running and vice versa.

HPC tests shall include mixed workflows and shall record step start and end times to confirm
that the barriered model is maintained in practice.

### Testing and Validation: Resource-Constrained MPI Scheduling

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

Unit tests shall verify scheduling behavior when the combined resource requirements of ready
MPI steps exceed available resources, including cases where only a subset can run concurrently
and cases where a step cannot meet its minimum resource requirements.

Integration tests shall use synthetic steps with varying resource requirements to confirm that
the scheduler runs the largest feasible concurrent subset and defers the rest.

### Testing and Validation: Portable MPI Launch Mechanism

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

HPC tests shall verify that MPI steps are successfully launched on specific resource subsets
on Chrysalis, Perlmutter and Aurora. Tests shall confirm that each concurrent step runs on its
assigned resources and does not use resources assigned to another concurrent step.

### Testing and Validation: Cross-Machine Phase-3 Functionality

Date last modified: 2026/05/11

Contributors:

- Xylar Asay-Davis
- Claude

HPC validation shall be conducted on Chrysalis, Perlmutter and Aurora using at least the
`omega_pr`, `omega_nightly` and `mpaso_pr` suites. Each system-specific validation shall record
step timing, evidence of step overlap, node and core use, resource isolation model applied,
failure behavior and resume behavior.

Speedup relative to the Phase 2 barriered MPI baseline shall be recorded on each machine to
assess the benefit of concurrent MPI execution on realistic workflows.
