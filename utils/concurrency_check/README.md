# Concurrency checks for Phase B

> **This directory is temporary and will not survive Phase B.** When Phase B development finishes, the branch is rebased and `utils/concurrency_check` is removed from the git history entirely -- not deleted in a later commit, but taken out as though it had never been committed. It is useful now and is not something to carry in the history for the long haul.
>
> Two consequences worth acting on *before* that rebase, not after:
>
> - **Recorded results go with it.** Anything measured here has to be somewhere durable first -- the findings in [the Phase B design document](../../docs/design_docs/task_parallelism_phase_b.md), the verdicts in the pull request -- or the evidence disappears along with the directory.
> - **Anything worth keeping has to move.** A check that should keep running belongs in `tests/`, not here.
>
> This is the same arrangement Phase A used for `utils/placement_check`, for the same reason, recorded at the top rather than in working notes because it is easy to discover too late.

## What is here so far

### `probe_node_memory.sh` -- what a node says about its memory

Phase B's scheduler decides what may start by keeping a memory budget itself, since nothing below Polaris will schedule memory for it. The design requires the scheduler to credit each node with **what that node reports** rather than with one configured figure per machine, because nodes can differ -- Aurora's do -- and because over-admitting memory kills a job rather than merely slowing it.

That leaves a question the design does not answer: *which* number a node reports is the one to credit. There are at least four, and they do not agree:

| number | what it means | where it comes from |
| --- | --- | --- |
| `MemTotal` | what the hardware has | `/proc/meminfo` |
| `MemAvailable` | what the kernel thinks can still be allocated | `/proc/meminfo` |
| the cgroup limit | what this job may use here, enforced by the kernel | `/sys/fs/cgroup/...` |
| `RealMemory` | what the batch system schedules against | `scontrol show node` |

The last of these is where `mache`'s configured `memory_per_node` comes from, and on Chrysalis it is a survey rather than a sample: the config says the smallest node in the partition agrees with 253000 MB.

The probe reads all four and prints them as `key=value` lines, one node at a time, so the choice gets made from numbers.

Run it inside an allocation, once per node:

```bash
sbatch utils/concurrency_check/job_chrysalis.sbatch
```

Two nodes, five minutes, Chrysalis's `debug` partition, no account needed at that size.

## What Chrysalis answered

Job 1283361, nodes chr-0495 and chr-0496, recorded under `results/chrysalis/1283361/`. Both nodes agreed to within 0.03%, so one column tells the story:

| figure | MiB | against the configured 253000 |
| --- | --- | --- |
| `MemTotal` | 257155 | config is 1.6% **below** it |
| `MemAvailable` | 239633 | config is 5.6% **above** it |
| Slurm `RealMemory` | 253000 | exactly the configured figure |
| Slurm `FreeMem` | 240274 | config is 5.3% above it |

Three findings, and the middle one is the reason to read nodes at run time at all.

**The configured figure is `RealMemory`, exactly.** No surprise, since that is where the config says it came from, but it does mean the config is not an estimate that reading `RealMemory` per node would improve on. On a machine whose nodes are identical it would return the number Polaris already has.

**What a job may actually use is about 5.6% less than that.** `MemAvailable` is 239633 MiB against a configured 253000, so roughly 13 GB per node is spoken for by the kernel, daemons and unreclaimable cache before a step starts. Polaris budgeting against 253000 is over-crediting every Chrysalis node by that much -- small, but in the direction that kills jobs rather than the direction that wastes them. Neither the configured figure nor `RealMemory` can see this; only a reading taken on the node can.

**Nothing enforces memory here.** The step's cgroup is `/system.slice/slurmd.service` with a limit of 2^63-1, so job steps are not placed in a memory-limited cgroup at all. That confirms from the other direction what Phase A measured when a step told to take 4 GB under a 1024 MB cap ran to completion. It also means Chrysalis offers no cgroup figure to credit, so `MemAvailable` is the best available answer here.

### What this settles for the scheduler

Credit each node with what it reports at job start: the job's cgroup limit where the site sets one, since that is exactly what the kernel will kill against, and `MemAvailable` otherwise. Keep `MemTotal`, `RealMemory` and the configured figure beside it in the log, because a node whose figures sit well below its neighbours' is a node with something wrong, and that is worth saying whichever number the budget uses.

`MemAvailable` is a snapshot and it moves. Taken at the start of a run it describes what the allocation actually has when the scheduler begins packing, which is the moment the budget is for.

### The other machines do not need a job of their own

Two questions are still open and neither is worth a standalone run, because the reader answers both as a side effect of any Phase B run on the machine. It logs all four figures per node at the start of every concurrent run, so the cross-machine validation this phase needs anyway is what collects them.

- **Aurora.** Its nodes differ by about 12.7% between classes, and its configured 960000 MB is marked provisional in `mache`'s own config -- the comment there asks for `resources_available.mem` from `pbsnodes -a` and has not been acted on. The first Phase B run there both exercises the reader and produces the figure that would correct it.
- **Perlmutter or Frontier.** Both enforce `--mem`, so unlike Chrysalis they should put a step in a memory-limited cgroup, which is the branch of the reader Chrysalis cannot exercise. Again, the first run there shows it.

This probe stays because it is how the Chrysalis answer was arrived at and because it can be run on its own if one of those readings ever looks wrong, not because anything is waiting on it.

## What omega_pr answered

Jobs 1283410 (serial, from `main`) and 1283412 (concurrent, from this branch), three Chrysalis nodes, one shared Omega build, recorded under `results/chrysalis/omega_pr_1283410_1283412/`.  Both sides were set up from the same polaris config file by `utils/benchmark`, so the only difference between the runs is which polaris ran them.

**The concurrent run was not faster.  12:15 serial against 13:04 concurrent.**

That is not a scheduling failure, and the event stream says so.  `concurrency.py` beside this file reconstructs each step's span from its start timestamp and its duration:

| | |
| --- | --- |
| steps | 115 |
| wall | 784 s |
| peak concurrency | 48 |
| mean concurrency | 7.5 |
| mean cores busy | 151 of 192 (79%) |

The machine was busy.  What it was busy doing is the problem.

### Two thirds of the run was Python starting up

Every step reports its own runtime in its log, and the scheduler measures the subprocess from `Popen` to exit.  Across the 113 steps that report both:

| | |
| --- | --- |
| measured by the scheduler | 5807 s |
| the steps' own work | 1668 s |
| overhead outside the work | 4139 s (71%) |
| overhead per step | 36.6 s |

The overhead is flat rather than proportional, which is what identifies it.  A one-core init step doing 6 s of work took 36.3 s.  Timed directly, in a step's own work directory:

| | |
| --- | --- |
| `import polaris` | 11.5 s, 967 modules |
| first unpickle of `step.pickle` | 17.3 s, **1826 more modules** |
| second unpickle, same process | 0.10 s |

The unpickle is not reading 4.6 MB of data.  It is importing cartopy, matplotlib, dask, jigsawpy, mpas_tools, pyremap, scipy, shapely, pyproj and the rest of the tree that a step's classes reach, and the warm repeat proves it: 0.10 s once the modules are loaded.  A step subprocess imports about 2,880 modules from a parallel filesystem before it does anything, and 115 of them do it at once.

Deferring the CLI's own imports was measured and is not the answer.  `polaris.__main__` eagerly imports `list`, `setup`, `suite` and `cache`; importing only `polaris.run.serial` instead ends at 2,880 modules against 2,894, because the unpickle pulls in nearly the same set either way.

So this is not a scheduler problem and not a mache problem.  With the import cost removed the arithmetic lands where the design predicted -- 1668 s of work at this concurrency is a few hundred seconds of wall -- which is the 2.5-3x estimate.  Getting there means making the imports lazy across the task tree, or amortizing them across steps, which is Phase C's in-process execution.

### The placement check earned its keep on the first run

It ran on all 115 steps and was never unable to run:

| | |
| --- | --- |
| bound locally, had exactly its cores | 90 |
| placed across nodes, so not bound | 25 |
| launch probed and inside its placement | 27 |
| launch probed and **outside** it | 3 |
| could not be checked | 0 |

The three are worth reading, because they are not what was expected:

```
On chr-0495 the launch was allowed 19 cores (2-6,14,23-24,27-29,32-33,57-62)
                          but was given 19 (4-11,36-46).
```

Slurm honored the *count* and chose its own cores.  Polaris believes those cores are free and will place another step on them, so two steps that the accounting says are disjoint can be sharing hardware.  Three launches in thirty on Chrysalis.  Nothing about this would have been visible without the check: every one of those steps succeeded.

### Results against the serial baseline

93 baseline comparisons passed and 6 failed.  The six are one chain: `mesh/spherical/icos/base_mesh/480km` and the five steps downstream of it.  The differences are small and everywhere -- 0.01% in `areaCell`, 0.02% in `dcEdge`, 4e-5 rad in `latCell` -- and they do not disappear when the fields are sorted, so it is a slightly different mesh rather than a reordered one.

The step declares one core and got one core in both runs, so placement *width* is not the variable.  Two explanations remain and this pair of runs cannot separate them: mesh-generator noise that a serial rerun would show equally, or a generator that adapts to the cores it can see, which differ because the concurrent run binds the process to one core and the serial run leaves it seeing all 192.  A serial-versus-serial rerun of that one step settles it and is cheap.

The 4 property-check failures are identical on both sides -- `ekman/forward_constant` and three `vmix_unstable` forwards -- as are the two task failures, which are an `nVertLevels` against `nVertLevelsP1` indexing bug in `single_column/viz.py` on `main`.  None of them are concurrency.

### Two defects this run found in the branch itself

**The event stream could not answer the question it exists for.** `record()` stamped every record with the seconds since the run began, and then let a caller's own field overwrite it; `step_finished` passed the step's *duration* under that name.  Nothing failed -- overlap computed from the stream was simply wrong, which is how the peak concurrency reported for job 1283383 came to be wrong.  `record()` now refuses a caller-supplied `seconds`.

**`polaris parallel` never added up the comparisons.** Each step compares itself against the baseline in its own process and leaves the verdict beside its log, exactly as the serial path does, but nothing summed them, so a 115-step run against a baseline gave 115 separate answers and no answer.  The numbers above had to be counted off the filesystem by hand.

## Traps carried over from Phase A

- **Do not edit a script while a job is running it.** Bash reads scripts incrementally, so rewriting one underneath a running job makes it resume mid-token.
- **Do not let pre-commit rewrite recorded results.** The trailing-whitespace hook edits captured output. Commit recorded evidence with `--no-verify`.
- **A job in `CG` state has ended, not necessarily successfully.** Check the exit code.
- **Check overlap in time before checking disjointness.** A "these were disjoint" verdict means nothing if peak concurrency was 1.
