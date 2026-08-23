# Launcher spike

Throwaway scripts to answer one question before we commit to a design for
task parallelism in Polaris: **can we launch several placed steps at once
inside a single allocation, on both Slurm and PBS?**

Everything else in the roadmap depends on the answer, and it is cheap to
measure directly instead of inferring it.

## What is being tested

| test | what it measures |
| --- | --- |
| `A_sequential_overlap` | Is `srun`/`mpiexec` itself rate limited? Sequential launches, one at a time. |
| `A0_sequential_plain` | Slurm control: same, without `--overlap --exact`. |
| `B_concurrent_overlap` | Do concurrent single-task placed launches coexist? |
| `B0_concurrent_plain` | Slurm control: same, without `--overlap`. Expected to serialize or retry on Slurm >= 20.11. |
| `C_concurrent_mpi` | The Phase 3 primitive: concurrent launches at MPI width, including PMI bootstrap. |
| `D_concurrent_mpi_gpu` | Same with GPU binding (`--gpus-per-task`, or `ZE_AFFINITY_MASK` on Aurora). |

Separating A from B is the point. "Perlmutter only allows about one `srun`
a minute" can mean either a genuine launch-rate limit (A is slow) or
job-step exclusivity making concurrent launches retry on a backoff (A is
fast, B serializes). Those have completely different fixes.

## Running it

There is a ready-made job script per machine, sized from the node counts and
queue policies in mache's machine configs, so there should be no need to
assemble an `salloc` or `qsub` by hand:

```bash
cd utils/launcher_spike
sbatch job_chrysalis.sbatch     # 2 nodes, debug partition
sbatch job_pm-cpu.sbatch        # 2 nodes, qos debug, constraint cpu
sbatch job_pm-gpu.sbatch        # 2 nodes, qos debug, constraint gpu, 4 GPUs
sbatch job_frontier.sbatch      # 2 nodes, batch partition, qos debug
qsub   job_aurora.pbs           # 2 nodes, debug queue, filesystems home:flare
```

Each is 30 minutes of walltime and uses a fraction of each node, so the
no-flag control tests cannot fail merely for lack of capacity. `pm-cpu` is
the run that matters most; `chrysalis` is the control, since its Slurm 20.02
predates the job-step exclusivity change.

To drive it by hand instead, run `./spike_slurm.sh` or `./spike_pals.sh`
inside any allocation.

## Getting results back

Each job prints the exact recorder command when it finishes. Run it from a
**login** node — compute nodes on Frontier and Aurora have no outbound
network, so the push has to happen outside the job:

```bash
./record_results.sh <results-dir> <job-log>
```

With no arguments it takes the newest `spike_results_*` directory. It copies
the raw output plus a regenerated `summary.txt` to
`utils/launcher_spike/results/<machine>/<jobid>/`, commits with the summary
in the message body, and pushes to whichever remote the branch tracks
(override with `SPIKE_REMOTE`, or pass `--no-push`). It rebases and retries
if another machine pushed first, so the five runs can be recorded in any
order.

To re-summarize a directory without recording it:

```bash
./summarize.py spike_results_<timestamp>
```

Knobs, all optional: `SPIKE_SLOTS` (concurrency, default 4), `SPIKE_CPUS`
(cores per rank, 8), `SPIKE_RANKS` (ranks per MPI launch, 4), `SPIKE_SLEEP`
(payload seconds, 15), `SPIKE_SEQ_N` (sequential launches, 10),
`SPIKE_NODE`, `SPIKE_OUTDIR`, `SPIKE_TIMEOUT`, `SPIKE_SKIP_GPU`,
`SPIKE_MPICC`, and `SPIKE_CORE_LIST` (PALS only — the usable core set, since
Aurora reserves core 0 and cores 49-52).

Concurrent slots are all pinned to a single node on purpose — sharing one
node is the hard case for placement. Spreading across nodes is easier and
is not what we are unsure about.

## Reading the results

- **A fast, B peak concurrency == slots** — Tier A works. Add
  `--overlap --exact` (Slurm) / `--cpu-bind list:` + `--hosts` (PALS) to the
  mache launcher and the rest of the design follows.
- **A fast, B peak concurrency == 1** — job-step exclusivity. Compare against
  the B0 control and check whether the flags reached `srun`.
- **A slow (~1 launch/min)** — genuine throttling. Tier A is out on that
  machine; this is the case where Flux as a nested scheduler is worth the
  dependency.
- **C or D worse than B, or core collisions reported** — placement is not
  actually enforced at MPI width, which is the case that matters most.

A small MPI payload is built with `mpicc`, or with `cc` on the Cray machines
(Perlmutter, Frontier) where that is the MPI wrapper. If neither works, the
shell payload is launched at MPI width instead, which still exercises step
creation and placement but not PMI bootstrap. The summary says which was
used, so a `shell-fallback` result should be read as a weaker answer for
tests C and D.

Whatever happens, the scripts avoid polling the batch system — no `squeue`
or `qstat` loops — because NERSC asks that batch-system queries stay to
1-2 per minute aggregate. Any real scheduler we build has to respect the
same limit.
