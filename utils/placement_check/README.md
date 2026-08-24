# mache placement check

> **This directory is temporary and will not survive Phase A.** When Phase A development finishes, the branch is rebased and `utils/placement_check` is removed from the git history entirely -- not deleted in a later commit, but taken out as though it had never been committed. It is useful now and is not something to carry in the history for the long haul.
>
> Two consequences worth acting on *before* that rebase, not after:
>
> - **The recorded results go with it.** Everything under `results/` is the evidence that mache pull request #470 renders commands which behave on real machines. That evidence has to be somewhere durable first -- the verdicts in the mache pull request, and the measurements in [the umbrella design document](../../docs/design_docs/task_parallelism.md) -- or the case for the merge disappears along with the directory.
> - **Anything worth keeping has to move.** The standing single-confined-step check that the design asks to keep "as a small standing test rather than a one-off" is the obvious candidate, and it belongs in `tests/` rather than here if it is to outlive this branch.
>
> The Polaris-side unit tests under `tests/` are not affected; they are ordinary tests and they stay.

Phase A of task parallelism lets Polaris run a step on a named part of its allocation. It does not build the launch command itself: `mache` does, and the `mache` change that adds placement is [pull request #470](https://github.com/E3SM-Project/mache/pull/470), which is open and unmerged.

Xylar's condition for merging it is that Polaris testing first confirms the rendered commands behave as intended on real machines. **That is what this directory is for, and it is the only thing gating that merge.**

## The claim being checked

The mechanisms Phase A relies on were already measured on Chrysalis, Perlmutter (CPU and GPU), Frontier and Aurora, and the results are in [the umbrella design document](../../docs/design_docs/task_parallelism.md). Those measurements used commands **written by hand**.

What is checked here is that the commands **`mache` renders** produce the same behavior. That is a different claim, and it is the one that has not been made yet.

Nothing here decides which resources a step should get. The placements are laid out by `check_placement.py` so that concurrent launches cannot overlap, deliberately, so that a failure is attributable to `mache` rather than ambiguous between `mache` and a Polaris scheduler that does not exist yet.

## What it runs

Each check launches a payload that records the cores and GPUs it can actually see, and when it ran, then sleeps long enough that genuine overlap is unambiguous.

| check | what it launches | what it answers |
| --- | --- | --- |
| `A_unplaced` | one launch, no placement | what Polaris does today; the control that shows placement changes something |
| `B_placed_alone` | one placed launch, `gpus=0` | is a placement honored at all? does an explicit "no GPUs" reach the payload? |
| `C_placed_alone_gpu` | one placed launch, `gpus=N` | does a GPU placement confine a launch to those GPUs? (GPU machines only) |
| `D_concurrent` | four placed launches at once, `gpus=0` | do they overlap in time, on disjoint cores? |
| `E_concurrent_gpu` | four placed launches at once, `gpus=N` | the same, with disjoint GPUs too (GPU machines only) |
| `F_memory_limit` | one placed launch allowed 1 GB, allocating 4 GB | does anything actually enforce a memory allowance? |

The single-launch checks come first on purpose. A concurrency verdict is meaningless until placement is known to be honored, because launches that never overlapped trivially have disjoint cores. That mistake was made once already during the earlier launcher spike.

All four concurrent slots share one node, also on purpose: sharing a node is the hard case, and spreading across nodes is not what anyone is unsure about.

## The memory question, and why it rides along

Polaris does not hand memory to the launcher, and the design says it should not: asking for memory was measured to change nothing observable. But that evidence shows memory was not what serialized concurrent steps. It does not show that a memory request is *inert*, and nobody has checked whether exceeding an allowance gets a step killed. Those lead to different designs, so `F_memory_limit` settles it while these machines are being visited anyway.

It launches one placed step with `--mem=1024M`, has it allocate 4 GB, and records how far it got. Two answers, both useful:

- **Nothing enforces.** Memory is a budget Polaris keeps and nothing below it will help. The design is on the right footing.
- **Something enforces.** Then a step can be capped on that machine, and -- by exactly the argument that applies to GPUs -- a launch that says nothing about memory may be read as claiming all of it. That would be the same trap in a second place, and worth chasing.

Two things about this check are deliberate and worth knowing before reading the code:

- **It adds a flag `mache` does not render.** Every other check runs exactly what `mache` produced; this one appends `--mem` itself. `mache` carries no memory on purpose, and the question here is what the batch system does rather than what `mache` emits. It is the only place the harness reaches past the renderer.
- **Being killed is a result, not a failed run.** A launch stopped for exceeding its allowance is the finding, so it does not count towards the job's exit code. Everything else still does.

The payload writes its progress out and flushes after every 64 MB, because a process killed for exceeding a limit dies without warning and anything left in a buffer is exactly the evidence that would be lost.

On PBS with PALS there is no per-launch memory request to make, so the check is skipped and says so.

## While we are there: what a node's memory is

Every run records what the site says a node's memory is -- `sinfo -o "%m"` on Slurm, `pbsnodes` on PBS -- into `meta.kv` as `memory_per_node_mb`, with the source beside it. Those are the numbers `mache` needs for its `memory_per_node` config option, and collecting them costs one query on a machine somebody is already standing on. Chrysalis reports 253000 MB.

It is the site's figure and not the kernel's on purpose: what belongs in a config is the memory a job may actually use, which is a few percent below what the hardware has, and the smaller number is the one a caller must not exceed. Where neither command works the run falls back to `MemTotal` and labels it, so that nobody copies it into a config believing it is the other thing.

## Before submitting anything: read the commands

`preview_commands.py` renders every command the check would run, for every machine, without an allocation and without launching anything. It runs anywhere, a login node included.

```bash
./preview_commands.py                      # all five machines
./preview_commands.py --machines frontier  # just one
```

Slurm machines are rendered for both eras, since the flags differ completely across the 20.11 change and the preview cannot know which a remote machine runs.

One caveat: `placement_support` is probed from the launcher actually installed where the preview runs, not from the machine's config. Previewing Aurora from anywhere else therefore reports `none`. Run the preview on the machine itself to see its real answer.

## Deploying

This check needs the `mache` branch that adds placement, so it cannot borrow another worktree's environment the way the earlier spike could. Deploy **this** worktree:

```bash
./deploy.py --mache-fork xylar/mache --mache-branch parallel-placement
```

`deploy.py` and `deploy/cli_spec.json` are contract files shared with `mache` and already support this; nothing in Polaris needs changing.

**The compiler matters on two machines.** `mache` puts the GPU count in `[parallel.<compiler>]`, and the machine default compiler reports zero GPUs on Frontier and Aurora, which makes the GPU checks skip without failing:

| machine | deploy with | why |
| --- | --- | --- |
| chrysalis | default | no GPUs |
| pm-cpu | default | no GPUs |
| pm-gpu | default (`gnugpu`) | the base `[parallel]` section already has `gpus_per_node = 4` |
| frontier | `--compiler craygnu_mphipcc` | the default `craygnu` has `gpus_per_node = 0` |
| aurora | `--compiler oneapi-ifxgpu` | the default `oneapi-ifx` has `gpus_per_node = 0` |

The check says loudly when it is about to skip the GPU checks, but it is much cheaper to get this right before submitting.

## Submitting

There is a job script per machine, sized from the node counts and queue policies in `mache`'s machine configs, so there should be no need to assemble an `salloc` or `qsub` by hand. Each asks for one node for 30 minutes and uses a fraction of it.

```bash
cd utils/placement_check
sbatch job_chrysalis.sbatch    # 1 node, debug partition
sbatch job_pm-cpu.sbatch       # 1 node, qos debug, constraint cpu
sbatch job_pm-gpu.sbatch       # 1 node, qos debug, constraint gpu, 4 GPUs
sbatch job_frontier.sbatch     # 1 node, batch partition, qos debug, 8 GCDs
qsub   job_aurora.pbs          # 1 node, debug queue, filesystems home:flare
```

To drive it by hand instead, run `./run_check.sh` inside any allocation.

## Getting results back

Each job prints the exact recorder command when it finishes. Run it from a **login** node: compute nodes on Frontier and Aurora have no outbound network, so the push has to happen outside the job.

```bash
./record_results.sh <results-dir> <job-log>
```

With no arguments it takes the newest `placement_results_*` directory. It copies the raw output plus a regenerated `summary.txt` to `utils/placement_check/results/<machine>/<jobid>/`, commits with the summary in the message body, and pushes to whichever remote the branch tracks (override with `PLACE_REMOTE`, or pass `--no-push`). It rebases and retries if another machine pushed first, so the five runs can be recorded in any order.

To re-summarize a directory without recording it:

```bash
./summarize.py placement_results_<jobid>
```

## Reading the results

Read them in this order.

1. **Was the placement honored?** `B_placed_alone` and `C_placed_alone_gpu` say so directly. The verdict differs by mechanism, and the summary applies the right one: where the batch system reserves resources (Slurm 20.11 and newer) it picks *which* cores satisfy the count, so only the count is ours to check; where the launcher binds explicitly (older Slurm, PALS) the exact set should come back as asked.
2. **Did the concurrent launches actually overlap?** `D_concurrent` and `E_concurrent_gpu` report peak concurrency. A peak of 1 means they serialized, which means the command reserves more than it asked for. The summary withholds any disjointness verdict when the peak is 1, since it would be meaningless.
3. **Were they disjoint?** Only then does "cores are disjoint" or "GPUs are disjoint" mean anything.

A launch that says nothing about GPUs is read as claiming every GPU on the node, which is what serialized concurrent launches on the GPU machines. `CUDA_VISIBLE_DEVICES` is renumbered per launch, so four launches on four different GPUs all report device `0`; the payload records `SLURM_STEP_GPUS`, which carries the global ids, and the summary prefers it.

## The one open question this can settle

For a launch asking for no GPUs on PALS, `mache` emits `--env=ZE_AFFINITY_MASK=`. An empty `CUDA_VISIBLE_DEVICES` is the documented way to say "no devices", but Level Zero may read an empty mask as "no mask", which is every tile. Nothing on PALS serializes on a GPU claim, so it is safe either way; what is in doubt is whether the explicitness is real.

`B_placed_alone` on Aurora reports exactly this: look at what its `gpu_env` field contains and whether the summary says `ZE_AFFINITY_MASK set but empty`. One line in the Aurora run settles it.

## Traps already hit, on the earlier spike

- **Do not edit a script while a job is running it.** Bash reads scripts incrementally, so rewriting one underneath a running job makes it resume mid-token. `run_check.sh` snapshots itself into the results directory and re-execs from there, which also leaves every recorded run with a copy of the exact scripts that produced it.
- **Do not let pre-commit rewrite recorded results.** The trailing-whitespace hook was editing captured `srun` stderr. `record_results.sh` commits with `--no-verify` for that reason.
- **A job in `CG` state has ended, not necessarily succeeded.** Check the exit code.

## Knobs

All optional, all read by `run_check.sh`: `PLACE_SLOTS` (concurrency, default 4), `PLACE_NTASKS` (MPI ranks per launch, 2), `PLACE_CPUS` (cores per rank, 4), `PLACE_SLEEP` (payload seconds, 15), `PLACE_OUTDIR`, `PLACE_CORE_LIST` (the usable cores on a node, normally read from the machine config), `PLACE_SKIP_GPU`, `PLACE_SKIP_MEMORY`, `PLACE_MEM_ALLOWANCE_MB` (1024), `PLACE_MEM_TARGET_MB` (4096), `PLACE_DRY_RUN` (render and write out the commands, launch nothing), `PLACE_MPICC`, `PLACE_ALLOW_FALLBACK` (accept the shell payload if the MPI one will not build), and `PLACE_LOAD_SCRIPT`.

The MPI payload is built with `mpicc`, or with `cc` on the Cray machines where that is the MPI wrapper. If neither works the run **aborts**, because falling back to the shell payload would quietly downgrade the concurrent checks from "does an MPI job survive concurrency" to "does step creation survive concurrency".

Whatever happens, none of these scripts poll the batch system -- no `squeue` or `qstat` loops -- because NERSC asks that batch-system queries stay to 1-2 per minute aggregate. Any scheduler built on this has to respect the same limit.
