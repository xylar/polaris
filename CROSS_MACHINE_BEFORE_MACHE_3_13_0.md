# Before mache #477 is merged: one run on each supported machine

*Written 2026/09/10 by Claude, at Xylar's request. Committed so that it reaches the machines it is about; the commit comes out before merge along with the results it collects.*

## Why this is worth doing before the merge rather than after

mache #477 gives a placement one core list per node, and Polaris's Phase B depends on it. Everything about it has been verified on **Chrysalis and nowhere else** — and Chrysalis is the only one of the five machines that takes the code path the PR most recently changed.

The PR already found one bug of exactly the kind this is guarding against, and found it by running rather than by reading. Slurm applies a `--cpu-bind` mask list to each node's tasks by their index *on that node*, restarting the list for every node, so a three-node launch given 48 masks used the first node's 16 on all three. Every launch succeeded, on cores nobody had chosen, and nothing reported anything. It was caught only because a standing check asks the ranks where they actually landed.

That check now runs on every step of every concurrent run, which is what makes these runs cheap to interpret: each machine either says nothing, or names the steps that did not get what they were given.

## What differs between the machines, and where the risk sits

| machine | launcher | mechanism | what mache renders |
| --- | --- | --- | --- |
| Chrysalis | Slurm 20.02 | CPU_BINDING | `--cpu-bind=mask_cpu:` one node's masks |
| Perlmutter CPU | Slurm 20.11+ | SCHEDULER | `--exact --gres=none` |
| Perlmutter GPU | Slurm 20.11+ | SCHEDULER | `--exact --gpus=N` |
| Frontier | Slurm 20.11+ | SCHEDULER | `--exact`, GPUs |
| Aurora | PBS / PALS | CPU_BINDING | `--hosts`, `--cpu-bind list:`, `ZE_AFFINITY_MASK` |

**Aurora is the one to run first.** Its `--cpu-bind list:` is built from one entry per task across *all* nodes -- structurally the same shape as the Slurm bug above. If PALS also applies its list per node, mache is rendering something wrong on Aurora today and the placement check will say so on the first multi-node step. This was left alone in #477 deliberately, because it cannot be measured from Chrysalis, and the PR says so.

**Perlmutter and Frontier carry a different untested risk: GPUs.** Nothing has exercised `--gpus=N` through a placement, and the pool's rule that a placement spanning nodes leaves GPU indices unnamed -- because indices are node-local while the count is a total -- has never met a real GPU allocation.

**Chrysalis is done.** Jobs 1283412, 1284023 and 1284024, recorded on the branch under `utils/concurrency_check/results/chrysalis/`.

## What to run

Any concurrent suite exercises the check, but it has to be one that **spans nodes**, since a single-node placement cannot show the bug this is looking for.

```bash
polaris suite -c ocean -t omega_pr --model omega --concurrent_steps \
    -p <build> -w <work dir>
# then, in the work directory
sbatch job_script.omega_pr.sh     # or qsub on Aurora
```

`--concurrent_steps` is the whole of it; there is no config file to write. It sets `[job] concurrent_steps`, so the choice is also recorded in the config the run was set up with.

On Chrysalis that gave 30 placed launches over 3 nodes, 9 of them spanning. Anything of similar shape will do.

**Perlmutter GPU runs the same Omega suite as everywhere else, with `gnugpu`.** The known open item there is `intelgpu` specifically, which is deployed but does not build Omega; `gnugpu` works. Do not substitute an MPAS-Ocean suite for this: MPAS-Ocean does not use GPUs, so it would exercise nothing of the GPU placement path while looking like coverage.

## What to look for

**In the run's own summary**, one line if anything went wrong:

```
N step(s) did not get the part of the allocation they were placed on, ...
```

Silence there is the pass. If it fires, each step's own log carries the detail, in this shape:

```
On chr-0495 the launch was allowed 19 cores (2-6,14,23-24,27-29,32-33,57-62)
                          but was given 19 (4-11,36-46).
```

Read it as: *allowed* is where the ranks actually ran, *given* is the placement. Equal counts with different numbers is the signature of a launcher applying the wrong list -- which is what Chrysalis did.

**Also worth capturing**, since these runs are the only chance to collect it cheaply:

- The allocation report at the top of the run, which says what each node credits for memory against what the machine's config claims. On Aurora the configured 960000 MB is marked provisional in mache's own config and has never been checked against `pbsnodes -a`.
- Whether any step reports `placement: not checked` -- that means the probe launch could not run, which is a different failure from a mismatch and should not be read as a pass.


## Reporting what you find

Commit the results to this branch, so that they come back to whoever is holding Phase B rather than living in a scratch directory on your machine.

```bash
cd <your polaris worktree on add-task-parallelism-phase-b>
git pull --rebase origin add-task-parallelism-phase-b   # or from xylar's fork
mkdir -p utils/concurrency_check/results/<machine>/<jobid>
cp <work dir>/polaris_<suite>.o<jobid> \
   <work dir>/<suite>_events.jsonl \
   utils/concurrency_check/results/<machine>/<jobid>/
git add utils/concurrency_check/results/<machine>/<jobid>
git commit --no-verify -m "Record what <machine> answered (job <jobid>)"
```

`--no-verify` matters: the trailing-whitespace hook rewrites captured output, and recorded evidence should be the bytes the machine produced.

Two files are enough. The job output carries the allocation report, the per-step results and the placement summary; the event stream carries what started when and what each step held. If a step *did* report a mismatch, add its log from `case_outputs/` as well, since that is where the detail is.

In the commit message, say plainly:

- which machine, which suite, how many nodes, and which compiler where it matters (`gnugpu` on pm-gpu, say)
- whether the placement summary line appeared at all
- anything that said `placement: not checked`, which is not a pass
- what each node credited for memory against the machine's configured figure

Then say so on mache PR #477, so the merge decision has the evidence beside it.

**These commits come out before merge.** `utils/concurrency_check/` is rebased out of the branch's history entirely, so anything that should outlive Phase B has to reach the design document or the pull request first. Recording numbers in the commit message as well as in the files is what makes that possible later.

## What would block the merge

- A mismatch reported on any machine.
- A probe that cannot run at all on a machine, since that leaves placement unverified there.
- A GPU step that gets devices it was not given, on Perlmutter GPU or Frontier.

None of these need a fix in this PR to be *known*; the point is to know before the version is cut, rather than after a release has to be corrected.
