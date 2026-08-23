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

Slurm (Perlmutter, Chrysalis, Frontier):

```bash
salloc -N 2 -t 00:30:00 -A <acct>       # plus machine-specific flags
cd utils/launcher_spike
./spike_slurm.sh
```

PBS/PALS (Aurora):

```bash
qsub -I -l select=2 -l walltime=00:30:00 -A <acct> -q debug
cd utils/launcher_spike
./spike_pals.sh
```

Both print a summary at the end and leave raw output in
`spike_results_<timestamp>/`. To re-summarize later:

```bash
./summarize.py spike_results_<timestamp>
```

Knobs, all optional: `SPIKE_SLOTS` (concurrency, default 4), `SPIKE_CPUS`
(cores per rank, 8), `SPIKE_RANKS` (ranks per MPI launch, 4), `SPIKE_SLEEP`
(payload seconds, 15), `SPIKE_SEQ_N` (sequential launches, 10),
`SPIKE_NODE`, `SPIKE_OUTDIR`, `SPIKE_TIMEOUT`.

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

Note that `mpicc` is used to build a small MPI payload if it is available;
otherwise the shell payload is launched at MPI width instead, which still
exercises step creation and placement but not PMI bootstrap. The summary
line for the run says which was used.

Whatever happens, the scripts avoid polling the batch system — no `squeue`
or `qstat` loops — because NERSC asks that batch-system queries stay to
1-2 per minute aggregate. Any real scheduler we build has to respect the
same limit.
