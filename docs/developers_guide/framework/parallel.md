(dev-parallel)=

# Parallel

Polaris now uses `mache.parallel` for parallel-system selection, resource
discovery and launcher command construction.

Within Polaris, a component stores a `mache.parallel.ParallelSystem`
instance with {py:meth}`polaris.Component.set_parallel_system`, then uses it
for:

- resource queries through {py:meth}`polaris.Component.get_available_resources`
- command execution through
  {py:meth}`polaris.Component.run_parallel_command`

This change adds stronger GPU support, supports compiler-specific parallel
sections (`[parallel.<compiler>]`) and avoids modifying config options during
runtime.

## Public API

The key APIs are now:

- {py:func}`mache.parallel.get_parallel_system`
- {py:class}`mache.parallel.ParallelSystem`
- {py:meth}`polaris.Component.set_parallel_system`
- {py:meth}`polaris.Component.get_available_resources`
- {py:meth}`polaris.Component.run_parallel_command`

`ParallelSystem.get_parallel_command()` supports both CPU and GPU resources
through `cpus_per_task` and `gpus_per_task`.

## Compiler-specific parallel configs

`mache.parallel` combines options in `[parallel]` with
`[parallel.<compiler>]` (if present), where `<compiler>` comes from
`[build] compiler`.

This lets machine configs specify different launcher flags and resource options
for different compiler toolchains without requiring a single machine-wide
parallel configuration.

## GPU resources

Polaris step resources now include GPU requirements (`gpus_per_task` and
`min_gpus_per_task`) in addition to CPU requirements. Resource constraints use
both CPU and GPU availability when determining whether a step can run.

For ocean model steps with dynamic sizing, Omega runs on GPU-capable compiler
configs use:

- `goal_cells_per_gpu` (target; default 8000)
- `max_cells_per_gpu` (minimum required resources; default 80000)

## Scheduler Resources and Dask Runtime

The `polaris run` command uses the same step resource fields as
`polaris serial`: `ntasks`, `min_tasks`, `cpus_per_task`,
`min_cpus_per_task`, `gpus_per_task`, `min_gpus_per_task` and `max_memory`.
In the first task-parallel phase, the scheduler reserves resources for only
one Polaris step at a time, then releases them before selecting the next step.
This deliberately preserves task-serial execution while proving the resource
model that later phases will use for concurrent steps.

Steps that can use the active Dask runtime should implement `run_with_dask()`.
When `polaris run` executes such a step, the framework passes a Dask client and
a lightweight resource lease that includes the assigned core, worker, node, GPU
and memory counts. Steps that do not implement Dask-aware work continue to run
through the regular `run()` method or command-line parallel arguments.

`polaris run` starts a Dask Distributed runtime for the duration of the task or
suite. On a single node, or when an allocation-scoped launcher is unavailable,
the command uses a local Dask backend. When a multi-node allocation and process
launcher are available, Polaris can start an allocation-scoped Dask scheduler
and workers. The selected backend, worker count, scheduler address and fallback
reason are recorded in each task's `schedule_events.jsonl` file.

## Supported Parallel Systems

The active system is still selected from `[parallel] system` and environment
context (`slurm`, `pbs`, `single_node`, `login`) but implementation is in
`mache.parallel`:

- {py:class}`mache.parallel.SingleNodeSystem`
- {py:class}`mache.parallel.LoginSystem`
- {py:class}`mache.parallel.SlurmSystem`
- {py:class}`mache.parallel.PbsSystem`

## Notes

- Polaris no longer provides a `polaris.parallel` module.
- Runtime no longer rewrites `cores_per_node` in config files.
- Machine and compiler config should provide the desired parallel options.
