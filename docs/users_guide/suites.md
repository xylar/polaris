(suites)=

# Suites

In polaris, suites are simply lists of tasks to be run together
in one operation.  One common reason for running a suite is to check for
changes in performance or output data compared with a previous run of the
same suite.  This type of
[regression testing](https://en.wikipedia.org/wiki/Regression_testing) is one
of the primary reasons that polaris exists. Another reason to define a test
suite is simply to make it easier to run a sequence of tasks that are often run
together.

Suites are defined by their MPAS core and name.  As you can see by
running:

```bash
polaris list --suites
```

the current set of available suites is:

```none
Suites:
  -c ocean -t cosine_bell
  -c ocean -t cosine_bell_cached_init
  -c ocean -t nightly
  -c ocean -t pr
```

As an example, the ocean `nightly` suite includes the tasks used
for regression testing of MPAS-Ocean.  Here are the tasks included:

```none
ocean/planar/baroclinic_channel/10km/threads
ocean/planar/baroclinic_channel/10km/decomp
ocean/planar/baroclinic_channel/10km/restart
ocean/planar/inertial_gravity_wave
```

:::{note}
Some tasks have "cached" steps, meaning those steps (or the entire test
case if no specific steps are listed) aren't run but instead the results
of a previous run are simply downloaded.  This is used to skip steps that
are prohibitively time-consuming during regression testing, but where the
results are needed to run subsequent tasks.  An example in the 
`cosine_bell_cached_init` suite listed above is the
`spherical/icos/cosine_bell` and `spherical/qu/cosine_bell` 
tasks from the `ocean` component.  These tasks take several minutes to
create their meshes and initial conditions, so to speed things up we sometimes
run with cached meshes and initial conditions.
:::

Including the `-v` verbose argument to `polaris list --suites` will
print the tasks belonging to each given suite.

## Running Suites with `polaris serial` or `polaris run`

Generated job scripts use `polaris serial` by default, and this remains the
recommended compatibility path for routine production and regression testing.
Use `polaris run` when you want to validate the task-parallel scheduler path,
debug dependency ordering or collect structured scheduler diagnostics. In the
first phase of the task-parallel rollout, `polaris run` still runs only one
Polaris step at a time, so it is not expected to speed up a suite.

To make generated job scripts use `polaris run`, opt in during setup:

```bash
polaris suite -c ocean -t omega_pr -w /path/to/work \
    --run_command run
```

For custom task lists, use the same option with `polaris setup`:

```bash
polaris setup -t ocean/spherical/icos/cosine_bell/decomp \
    -w /path/to/work --run_command run
```

You can also run the scheduler path directly from an existing work directory:

```bash
cd /path/to/work
polaris run <suite_name>
```

When `polaris run` is used for a suite, each task work directory contains a
`schedule_events.jsonl` file. These files are primarily for developers and
record the dependency graph, selected step order, resource checks, skipped or
blocked steps and the Dask backend used by the run.
