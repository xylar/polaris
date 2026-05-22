(dev-command-mods)=

# Modules for polaris commands

(dev-list)=

## list module

The {py:func}`polaris.list.list_cases()`, {py:func}`polaris.list.list_machines()`
and {py:func}`polaris.list.list_suites()` functions are used by the
`polaris list` command to list tasks, supported machines and test
suites, respectively.  These functions are not currently used anywhere else
in polaris.

(dev-setup)=

## setup module

The {py:func}`polaris.setup.setup_tasks()` and {py:func}`polaris.setup.setup_task()`
functions are used by `polaris setup` and `polaris suite` to set up a list
of tasks and a single task, respectively, in a work directory.
Subdirectories will be created for each task and its steps; input,
namelist and streams files will be downloaded, symlinked and/or generated
in the setup process. A [pickle file](https://docs.python.org/3/library/pickle.html)
called `task.pickle` will be written to each task directory
containing the task object for later use in calls to `polaris serial`.
Similarly, a file `step.pickle` containing the step object
will be written to each step directory, allowing the step to be run
on its own with `polaris serial`.  In contrast to {ref}`config-files`, these
pickle files are not intended for users (or developers) to read or modify.
Properties of the task and step objects are not intended to change between
setting up and running a suite, task or step.

Generated job scripts use `polaris serial` by default. Developers can opt in
to the initial task-parallel scheduler command path during setup with
`polaris setup --run_command run` or `polaris suite --run_command run`. Task
and step job scripts generated with this option run `polaris run`; suite job
scripts run `polaris run <suite>`. To verify that the scheduler path was used,
inspect each task work directory for `schedule_events.jsonl` and summarize the
files with
{py:func}`polaris.run.validation.validate_phase1_schedule_event_files()`.

(dev-suite)=

## suite module

The {py:func}`polaris.suite.setup_suite()` function is used by `polaris suite`
to set up a suite in a work directory.  Setting up a suite includes setting up
the tasks (see {ref}`dev-setup`), writing out a {ref}`dev-provenance` file, and
saving a pickle file containing a python dictionary that defines the suite for
later use by `polaris serial` or `polaris run`. The "target" and "minimum"
number of cores required for running the suite are displayed. The "target" is
determined
based on the maximum product of the `ntasks` and `cpus_per_task`
attributes of each step in the suite.  This is the number of cores to run
on to complete the suite as quickly as possible, with the
caveat that many cores may sit idle for some fraction of the runtime.  The
"minimum" number of cores is the maximum of the product of the `min_tasks`
and `min_cpus_per_task` attribute for all steps in the suite, indicating the
fewest cores that the test may be run with before at least some steps in the
suite will fail.

(dev-run)=

## run.serial module

The function {py:func}`polaris.run.serial.run_tasks()` is used to run a
suite or task and {py:func}`polaris.run.serial.run_single_step()` is
used to run a single step using `polaris serial`.  `run_tasks()` performs
setup operations like creating a log file and figuring out the number of tasks
and CPUs per task for each step, then it calls each step's `run()` method.

Suites run from the base work directory with a pickle file starting with the
suite name, or `custom.pickle` if a suite name was not given. Tasks or
steps run from their respective subdirectories with a `task.pickle` or
`step.pickle` file in them. Both of these functions reads the local pickle
file to retrieve information about the suite, task and/or step that
was stored during setup.

If {py:func}`polaris.run.serial.run_tasks()` is used for a suite, it will
run each task in the suite in the order that they are given in the
text file defining the suite (`polaris/<component>/suites/<suite_name>.txt`).
Output from tasks and their steps are stored in log files in the
`case_outputs` subdirectory of the base work directory. If the function is
used for a single task, it will run the steps of that task, writing
output for each step to a log file starting with the step's name. In either
case (suite or individual test), it displays a `SUCCESS` or `ERROR` message for
the execution of each step, indicates whether baseline comparisons `PASS` or
`FAIL` for any steps that include them (and if a baseline was provided),
and finally indicates if the overall task execution was `SUCCESS` or `ERROR`.
Execution times are provided for individual steps, tasks and the suite as a
whole.

{py:func}`polaris.run.serial.run_single_step()` runs only the selected step
from a given task, skipping any others, displaying the output in the terminal
window rather than a log file.

(dev-run-parallel)=

## run.parallel module

The {py:func}`polaris.run.parallel.run_tasks()` function is used to run a
suite or task with `polaris run`. This command path uses a Dask Distributed
runtime and the first task-parallel scheduler.

In the task-parallel rollout, a "task" in "task parallelism" refers to a
Polaris `Step` as the schedulable unit, not a whole Polaris `Task`. The rollout
is divided into four phases so the execution path can be validated before
turning on increasingly broad forms of concurrent execution:

- Phase 1 adds the permanent `polaris run` command path, dependency graph,
  resource accounting, Dask runtime, generated-job-script opt-in and structured
  scheduler events. It still runs only one Polaris step at a time, so its goal
  is correctness and observability rather than speedup.
- Phase 2 enables concurrent execution of eligible non-MPI steps when their
  dependencies and resource requirements allow it. MPI steps and steps marked
  ineligible remain serialized.
- Phase 3 adds concurrent execution of eligible MPI steps. MPI and non-MPI
  work still run in separate phases, so the two classes of work do not overlap.
- Phase 4 removes the MPI/non-MPI barrier and allows all eligible ready steps
  to share the allocation dynamically, with stricter resource accounting.

Thus, references to Phase 1 in this module mean the opt-in `polaris run`
scheduler path that prepares for future task parallelism while intentionally
preserving task-serial step execution.

See the [umbrella task-parallelism design](../../design_docs/task_parallelism.md)
and [Phase 1 design](../../design_docs/task_parallelism_phase1.md) for the
full requirements and rollout plan.

For a suite run, each task work directory contains a `schedule_events.jsonl`
file. These JSON-lines files record graph construction, selected ready steps,
wait reasons, resource feasibility, skipped cached or completed steps, blocked
dependencies, step start/finish/failure events, resource release and Dask
backend state. They are intended for developer validation and for debugging
future task-parallel behavior without scraping free-form logs.

The {py:func}`polaris.run.validation.validate_phase1_schedule_event_files()`
helper can be used in manual or system validation to check these artifacts. A
typical suite validation should compare `polaris run` with `polaris serial`
for outputs, completion markers and validation markers, then parse all
`schedule_events.jsonl` files to confirm that the scheduler path was used, the
Dask runtime was recorded and no event recorded more than one active Polaris
step. Heavy machine-specific suite validation remains a manual/system activity
when it is too expensive or data-dependent for unit tests.

For routine real-task validation of the initial scheduler path, developers
should prefer a small custom suite that exercises real Polaris setup, cached
steps, shared outputs and baseline/property validation without requiring a
large ocean initialization workflow. On a supported HPC system, a typical
comparison is:

```bash
export MACHINE=chrysalis
export COMPONENT_PATH=/path/to/mpas-ocean-or-omega-build
export WORK_ROOT=/path/to/polaris_scratch/task_parallel_phase1
export TASKS="mesh/spherical/icos/base_mesh/240km/task \
e3sm/init/icos240km/topo/remap \
e3sm/init/icos240km/topo/cull"

polaris setup -m ${MACHINE} -p ${COMPONENT_PATH} \
    -w ${WORK_ROOT}/serial -t ${TASKS} \
    --suite_name icos240_phase1_serial
cd ${WORK_ROOT}/serial
polaris serial icos240_phase1_serial

polaris setup -m ${MACHINE} -p ${COMPONENT_PATH} \
    -w ${WORK_ROOT}/run -t ${TASKS} \
    -b ${WORK_ROOT}/serial --run_command run \
    --suite_name icos240_phase1_run
cd ${WORK_ROOT}/run
polaris run icos240_phase1_run
polaris run icos240_phase1_run
```

The first `polaris run` invocation should pass execution and validation for
the selected tasks. The second invocation is a rerun check: steps with
completion markers should be reported as already completed, cached steps should
remain cached and dependent steps should not rerun unnecessarily. The
`polaris serial` and `polaris run` logs are not expected to be byte-for-byte
identical because the scheduler emits dependency and resource information, but
they should contain the same task/step success status and validation outcomes.

After the run, validate the scheduler artifacts from the run work directory:

```bash
python - <<'PY'
from pathlib import Path
from polaris.run.validation import validate_phase1_schedule_event_files

events = sorted(Path('.').glob('**/schedule_events.jsonl'))
summary = validate_phase1_schedule_event_files(
    events, require_dask_runtime=True)
assert summary.scheduler_path_used
assert summary.dask_runtime_used
assert summary.single_active_step
print(f'validated {len(events)} schedule event files')
print(f'scheduler events: {summary.event_count}')
print(f'successful step runtime: {summary.finished_step_runtime:.1f} s')
print(f'failed step runtime: {summary.failed_step_runtime:.1f} s')
print(f'all started-step runtime: {summary.total_step_runtime:.1f} s')
PY
```

Data-heavy tasks, such as global hydrography workflows, can provide useful
additional coverage for Dask-aware Python steps but should remain optional
manual validation unless a particular release or feature change requires them.

Representative suite validation should expand this check from a custom task
list to one or more predefined ocean suites. The priority suites for the
initial scheduler path are:

- `omega_pr`, preferably run in full because it is compact
- `omega_nightly`, either in full or as a subset that includes
  `horiz_press_grad`, manufactured solution, transport and restart tasks
- `mpaso_pr`, either in full or as a subset that includes thread/decomp,
  restart, overflow or internal-wave style MPI tasks and spherical transport
  tasks

The full-suite comparison is:

```bash
export MACHINE=chrysalis
export COMPONENT_PATH=/path/to/mpas-ocean-or-omega-build
export WORK_ROOT=/path/to/polaris_scratch/task_parallel_phase1
export SUITE=omega_pr

polaris suite -c ocean -t ${SUITE} -m ${MACHINE} \
    -p ${COMPONENT_PATH} -w ${WORK_ROOT}/${SUITE}_serial
cd ${WORK_ROOT}/${SUITE}_serial
polaris serial ${SUITE}

polaris suite -c ocean -t ${SUITE} -m ${MACHINE} \
    -p ${COMPONENT_PATH} -w ${WORK_ROOT}/${SUITE}_run \
    -b ${WORK_ROOT}/${SUITE}_serial --run_command run
cd ${WORK_ROOT}/${SUITE}_run
polaris run ${SUITE}
```

For a subset run, replace `polaris suite` with `polaris setup` and provide the
selected tasks explicitly. For example:

```bash
export SUITE=omega_nightly_subset
export TASKS="ocean/column/horiz_press_grad/salinity_gradient \
ocean/planar/manufactured_solution/convergence_both/default \
ocean/spherical/icos/cosine_bell/decomp \
ocean/spherical/icos/cosine_bell/restart"

polaris setup -m ${MACHINE} -p ${COMPONENT_PATH} \
    -w ${WORK_ROOT}/${SUITE}_serial -t ${TASKS} \
    --suite_name ${SUITE}_serial
cd ${WORK_ROOT}/${SUITE}_serial
polaris serial ${SUITE}_serial

polaris setup -m ${MACHINE} -p ${COMPONENT_PATH} \
    -w ${WORK_ROOT}/${SUITE}_run -t ${TASKS} \
    -b ${WORK_ROOT}/${SUITE}_serial --run_command run \
    --suite_name ${SUITE}_run
cd ${WORK_ROOT}/${SUITE}_run
polaris run ${SUITE}_run
```

The representative-suite validation should record the machine, suite or subset
name, model build, selected task list, final task-runtime table and the result
of the `schedule_events.jsonl` validation snippet above. The structured events
should show scheduler graph construction for every task, Dask runtime metadata
for every task and no violation of the single-active-step policy. The task logs
in `case_outputs` should preserve aggregate pass/fail status and make any
baseline or execution failure traceable to the same task names as the serial
comparison.

Phase 1 timing comparisons should be treated as overhead checks, not speedup
claims. Record the `Total runtime` line from `polaris serial` and
`polaris run`, then record the `all started-step runtime` value from the
structured-event snippet above. The difference between `polaris run` wall time
and summed started-step runtime is the scheduler-path overhead plus any idle
time between steps. Expected sources of overhead include Dask startup and
shutdown, scheduler graph construction, runtime config reloads, resource
feasibility and reservation bookkeeping, structured event writing and the
extra schedule-summary output. A modest slowdown is acceptable in Phase 1
because this phase is validating correctness and readiness for later
concurrency. Large unexpected slowdowns should be investigated by comparing the
serial and scheduler task-runtime tables, per-step runtime lines and structured
step-finish or step-failure durations.

### Troubleshooting `polaris run`

Most `polaris run` failures should be debugged from the task log in
`case_outputs` together with the task's `schedule_events.jsonl` file:

- Missing input files usually mean that a selected step did not declare a
  dependency on the step that creates the file, or that an upstream cached or
  completed producer is unavailable in the work directory. The scheduler should
  report the selected order before the failing step starts.
- Resource infeasibility means that a step's minimum CPU, GPU or node request
  cannot be met from the available allocation. Inspect `resource_feasibility`
  events for the requested and available resources.
- Failed dependencies appear as `step_skipped` events with reason
  `blocked_dependency`. These are expected when a prerequisite step fails; the
  dependent step should not run until the prerequisite succeeds in a rerun.
- Backend startup failures or unexpected local fallback should be investigated
  from the `dask_runtime` event. It records the selected backend, worker count,
  scheduler address when available and fallback reason when the local backend
  was selected automatically.
- If a run seems to overlap steps in Phase 1, validate the event files with
  {py:func}`polaris.run.validation.validate_phase1_schedule_event_files()`.
  A valid Phase 1 run should never report more than one active Polaris step.

(dev-cache)=

## cache module

The {py:func}`polaris.cache.update_cache()` function is used by
`polaris cache` to copy step outputs to the `polaris_cache` database on
the LCRC server and to update `<component>_cached_files.json` files that
contain a mapping between these cached files and the original outputs.  This
functionality enables running steps with {ref}`dev-step-cached-output`, which
can be used to skip time-consuming initialization steps for faster development
and debugging.
