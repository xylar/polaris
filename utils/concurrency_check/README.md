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

The last of these is where `mache`'s configured `memory_per_node` comes from, and on Chrysalis it is a survey rather than a sample: the config says so, and says the smallest node in the partition agrees with 253000 MB. So the configured figure is not a loose estimate to be improved on by reading the node -- it is already the site's own number, and `MemTotal` is **larger** than it. Crediting `MemTotal` would raise every node's budget above what Polaris uses today, which loosens admission control rather than tightening it, and that is the opposite of what the requirement exists for.

The probe reads all four and prints them as `key=value` lines, one node at a time, so the choice can be made from numbers rather than from argument.

Run it inside an allocation, once per node:

```bash
sbatch utils/concurrency_check/job_chrysalis.sbatch
```

Two nodes, five minutes, Chrysalis's `debug` partition, no account needed at that size. The job also prints what the job's own environment carries, what the controller advertises for the same nodes, and the configured figure, so that all of them are in one place.

### What each answer would mean

- **A cgroup limit is set, and it matches the configured figure.** Then that is the number to credit: it is local, it is per node, it is exactly what the kernel will kill against, and reading it costs no batch-system query. Chrysalis may well not set one -- its Slurm predates the release that enforces `--mem`, and a step there was measured to survive exceeding one -- so a null result here is informative rather than a failure.
- **No cgroup limit, and `RealMemory` matches the configured figure.** Then the configured figure is already the node's own number, and the honest reading of the requirement is that Polaris should read `RealMemory` per node rather than trust one figure per machine. That is a batch-system query, though only one per run.
- **`MemTotal` is well above both.** Expected, and the reason not to credit it.

Whatever the answer, `MemTotal` and `MemAvailable` are worth recording alongside, because a node whose `MemTotal` is far below its neighbours' is a node with a hardware fault, and that is worth saying out loud whichever figure the budget uses.

## Traps carried over from Phase A

- **Do not edit a script while a job is running it.** Bash reads scripts incrementally, so rewriting one underneath a running job makes it resume mid-token.
- **Do not let pre-commit rewrite recorded results.** The trailing-whitespace hook edits captured output. Commit recorded evidence with `--no-verify`.
- **A job in `CG` state has ended, not necessarily successfully.** Check the exit code.
- **Check overlap in time before checking disjointness.** A "these were disjoint" verdict means nothing if peak concurrency was 1.
