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

### Still worth running elsewhere

- **Aurora**, most of all. Its nodes differ by about 12.7% between classes, and its configured 960000 MB is marked provisional in `mache`'s own config -- the comment there asks for `resources_available.mem` from `pbsnodes -a` and has not been acted on. A run here would both check the reader and let that figure be corrected.
- **Perlmutter or Frontier**, to see a real cgroup limit. Both enforce `--mem`, so unlike Chrysalis they should put a step in a limited cgroup, and that is the branch of the reader Chrysalis cannot exercise.

## Traps carried over from Phase A

- **Do not edit a script while a job is running it.** Bash reads scripts incrementally, so rewriting one underneath a running job makes it resume mid-token.
- **Do not let pre-commit rewrite recorded results.** The trailing-whitespace hook edits captured output. Commit recorded evidence with `--no-verify`.
- **A job in `CG` state has ended, not necessarily successfully.** Check the exit code.
- **Check overlap in time before checking disjointness.** A "these were disjoint" verdict means nothing if peak concurrency was 1.
