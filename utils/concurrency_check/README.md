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

## What forking answered

`fork_spike.py` and `fork_spike_chrysalis.sbatch` ask whether starting a step by forking costs less than starting it by exec, and whether a forked step still runs correctly. They exist because the subprocess model measured above cost 36.6 s per step and made the concurrent run slower than the serial one.

Job 1283951, three Chrysalis nodes, 33:25, exit 0, recorded under `results/chrysalis/fork_spike_1283951/`. Part 1 runs the whole suite to populate the work directory; part 2 runs four steps both ways in that same allocation, minutes apart, so the difference between them is the mechanism rather than the machine. The order alternates per step, because the first run of a step warms the filesystem cache for the second.

| step | cores | fork | exec | saved |
| --- | --- | --- | --- | --- |
| `ocean/column/inertial/analysis` | 1 | 3.7 s | 56.3 s | 52.6 s |
| `ocean/column/thermo/conservation_summary` | 1 | 0.1 s | 46.8 s | 46.7 s |
| `.../convergence_both/del4/analysis` | 1 | 1.9 s | 46.2 s | 44.3 s |
| `.../baroclinic_channel/10km/restart/full_run` | 4 | 2.4 s | 51.4 s | 49.0 s |

32 trials, all exiting cleanly, over two passes: thread pools as they come, saving 48.2 s per step, and held to one thread, saving 49.5 s. A forked child produced output identical to the subprocess digit for digit, and the MPI step's child reached `srun` and came back.

Three things the spike found that were not what it was looking for:

- **`import polaris` leaves 129 OS threads**, 128 of them an OpenBLAS pool numpy brings up sized to the visible cores. `threading.enumerate()` reports one, because it sees Python threads only. Any fork-safety check has to read `/proc/self/status`.
- **Copy-on-write sharing is nearly complete.** 24 concurrent children held 347 MiB PSS between them against a parent of 322 MiB. Resident set size reports 7,700 MiB for the same processes and would size a node wrongly by a factor of twenty.
- **A forked child inherits the parent's BLAS pool size whatever its own affinity.** Children confined to 1, 4 and 16 cores returned an identical checksum and the same thread count. Under a subprocess the count tracked the placement instead, which is a candidate explanation for the six baseline comparisons that differed in job 1283412.

All of this is in [the Phase B design document](../../docs/design_docs/task_parallelism_phase_b.md), which is the copy that survives this directory.

## What forking answered on omega_pr

Jobs 1284023 (serial) and 1284024 (concurrent), three Chrysalis nodes, both from the same commit so that the execution path is the only difference, recorded under `results/chrysalis/fork_omega_pr_1284023_1284024/`.

| | |
| --- | --- |
| serial | 17:49 |
| concurrent | 5:22 |
| speedup | **3.3x** |

**Starting a step now costs 0.4 s.** Measured as the time from forking a step to reaping it, less the runtime the step reports for itself, over the 113 steps that report both:

| | median | max |
| --- | --- | --- |
| all steps | 0.4 s | 5.1 s |
| one-core steps | 0.4 s | |
| MPI steps | 0.4 s | |

Against 34.6 s and 37.6 s for the same two categories under the subprocess implementation. That is the quantity the phase was rebuilt around.

**MPI steps run through forked children.** 30 of the 115 steps used more than one core, the widest 192 across all three nodes, and every one succeeded. This was the case the design expected to break first if forking interfered with the launcher.

### The six baseline differences are JIGSAW's thread count, and I had this wrong

Six baseline comparisons differ, as they did before: `mesh/spherical/icos/base_mesh/480km` and the five steps downstream of it. This run settles the cause, and eliminates two hypotheses including the one the design carried.

**It is not run-to-run noise.** Two *serial* runs two days apart, on different nodes and different polaris commits, produce a byte-identical mesh. Serial against concurrent, same commit and same day, differs.

**It is not the BLAS thread count.** Both paths now pin the numerical pools to one, so that mechanism is gone and the difference remains.

**It is JIGSAW, which the icosahedral path does invoke** -- I previously reported that it did not, having read the first half of `jigsawpy.jigsaw.icosahedron`, which builds the icosahedron in pure numpy, and stopped before the end. It finishes by calling `refine()`, which calls `jigsaw(opts, mesh)`: the binary. The chain is then:

1. JIGSAW takes its thread count from `NUMTHREAD` in its own config file, and Polaris never sets it, so it uses what the machine offers -- which respects the CPU affinity mask.
2. A serial step's process is unbound and sees every core on the node. The same step in a forked child is bound to the one core it was placed on, and the log says so: `placement: this process has the 1 cores it was given`.
3. Thread count changes the mesh JIGSAW produces. Measured directly, below.
4. Everything downstream of the mesh differs.

So the fix already agreed for JIGSAW -- the two mesh steps declaring cores and passing the assignment to `opts.numthread` -- also fixes these six, because both paths would then use the same declared number.

## What JIGSAW's thread scaling answered

Job 1284025, one Chrysalis node, exclusive, recorded under `results/chrysalis/jigsaw_threads_1284025/`. Quasi-uniform meshes at five resolutions, `numthread` set explicitly, timing only the `jigsaw` call.

| resolution | 1 thread | best | speedup | knee |
| --- | --- | --- | --- | --- |
| 240 km | 3.1 s | 1.1 s | 2.78x | 4 |
| 120 km | 8.3 s | 3.6 s | 2.33x | 8 |
| 60 km | 34.5 s | 15.8 s | 2.18x | ~16 |
| 30 km | 136.8 s | 61.8 s | 2.21x | ~16 |
| 12 km | 709.8 s | 374.7 s | >=1.89x | >=8, still climbing |

**The knee rises with resolution and the ceiling does not.** JIGSAW returns about 2-2.8x however many threads it is given, and past roughly 16 nothing improves at any resolution measured. Eight to sixteen cores captures nearly all of what is available.

**Thread count changes the mesh.** The point counts are not stable across thread counts:

```
     120 km    41154 points at 1, 2, 4 threads
               41155 points at 8, 16, 32, 64
      30 km   656719, 656721, 656723, 656727, 656723  as threads rise
```

Not round-off in coordinates -- a different number of points, and not monotonic. So the quasi-uniform and unified base meshes are not reproducible across machines or allocation shapes today, independently of task parallelism, and pinning `numthread` is a correctness fix rather than tidiness.

The 12 km sweep reached 8 threads before the job hit its wall clock. The harness checked its time budget only between resolutions, so one resolution's sweep could outrun the whole budget; it now checks between thread counts as well and rewrites its results after every point, so a run that is killed still leaves what it measured.

## Pinning the BLAS pools is not free, and I said it was

Holding `OMP_NUM_THREADS` and friends to one costs about five minutes on a serial `omega_pr`: 733 s of task time became 1066 s. Almost all of it is one step, `mesh/spherical/icos/base_mesh/480km`, which went from roughly 40 s to 5:41. Everything else is within a couple of seconds.

I had surveyed the `np.linalg` and `np.matmul` call sites and judged them too small to benefit -- "a stack of tiny per-point matrices". That was reasoning from the shape of the call rather than measuring, and it was wrong. Given the finding above, some of that cost is JIGSAW being held to one thread rather than numpy, which the declaration will give back.

## What the omega_nightly node sweep answered

Jobs 1284171-5, Chrysalis, `omega_nightly` (178 steps), recorded under `results/chrysalis/nightly_scaling_1284171-5/`. One serial run at the default sizing as an anchor, and concurrent runs at four node counts.

| run | nodes | wall | vs serial@5 | node-hours | efficiency |
| --- | --- | --- | --- | --- | --- |
| serial | 5 | 1:31:48 | 1.00x | 7.7 | 1.00x |
| concurrent | 3 | 1:30:03 | 1.02x | **4.5** | **1.70x** |
| concurrent | 5 | 1:10:40 | 1.30x | 5.9 | 1.30x |
| concurrent | 8 | 0:54:38 | 1.68x | 7.3 | 1.05x |
| concurrent | 13 | 0:43:56 | 2.09x | 9.5 | 0.80x |

**Wall time and hardware efficiency pull in opposite directions.** Three nodes matches serial-on-five's wall clock for 40% fewer node-hours -- the same work, the same elapsed time, less machine. Thirteen nodes is twice as fast and *less* efficient than running serially.

**At equal node counts, concurrency buys 1.30x**, far below `omega_pr`'s 3.3x. The reason is visible in the step times: five 60 km forwards take about 460 s each at 13 nodes and account for roughly 38 of the 44 minutes. Each wants the whole allocation, so they run one after another however many nodes are added, and they scale poorly on their own -- 1.96x for 2.6x the cores.

So more nodes buy wall time at a worsening exchange rate, and nothing buys concurrency for those five steps. Eight nodes is a reasonable stopping point if wall clock is what matters; three is the best deal if throughput is. The geometric mean's five sits between them.

**Caveat on the 13-node figure.** That run had the task-distribution bug below still active, so it was overlapping more than its accounting believed and 43:56 may be optimistic.

### The sweep found a bug a single node count could not

Seven launches across the 5- and 13-node runs reported a placement mismatch of a new shape: the launch was allowed *more* cores than it was given, both ranges contiguous.

The pool modelled the spread of tasks over nodes as fill-each-node-then-remainder. Launchers balance instead:

```
52 cores over 3 nodes    old model [18, 18, 16]    slurm [18, 17, 17]
800 cores over 13 nodes  old model [62 x 12, 56]   slurm [62 x 7, 61 x 6]
```

So a node reserved 16 cores was given 17 tasks, and one reserved 56 was given 61. The accounting was wrong in the direction that matters -- it believed cores were free that were in use, and placed other steps on them.

It appears only when a *step's* tasks do not divide evenly over the nodes it was placed on. That is a per-step property, not a per-allocation one: 320 cores divides evenly by 5 nodes, which is why the 5-node run looked like it should have been safe, while the 52-core step inside it did not divide by 3.

**One step also segfaulted at 13 nodes** -- `cosine_bell/restart/restart_run`, which passed at 5 and 8. Consistent with cores being oversubscribed against the accounting, and not evidence of it. A rerun now that the distribution is fixed would say.

This is the second silent, wrong-accounting bug the standing placement check has caught on real hardware, after mache reusing mask lists per node. Both produced runs that succeeded while the bookkeeping was wrong, and neither would have been found by reading code.

## Traps carried over from Phase A

- **Do not edit a script while a job is running it.** Bash reads scripts incrementally, so rewriting one underneath a running job makes it resume mid-token.
- **Do not let pre-commit rewrite recorded results.** The trailing-whitespace hook edits captured output. Commit recorded evidence with `--no-verify`.
- **A job in `CG` state has ended, not necessarily successfully.** Check the exit code.
- **Check overlap in time before checking disjointness.** A "these were disjoint" verdict means nothing if peak concurrency was 1.
