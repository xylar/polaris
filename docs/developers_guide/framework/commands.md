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

In suite-wide Phase 1 scheduling, task runtime summaries are expected to become
the sum of executed step runtimes for each task, while the suite runtime should
remain wall-clock time for the whole suite. Until that accounting is hardened,
developers should use per-step runtime lines and structured step events when
comparing `polaris run` timing with `polaris serial`.

(dev-cache)=

## cache module

The {py:func}`polaris.cache.update_cache()` function is used by
`polaris cache` to copy step outputs to the `polaris_cache` database on
the LCRC server and to update `<component>_cached_files.json` files that
contain a mapping between these cached files and the original outputs.  This
functionality enables running steps with {ref}`dev-step-cached-output`, which
can be used to skip time-consuming initialization steps for faster development
and debugging.
