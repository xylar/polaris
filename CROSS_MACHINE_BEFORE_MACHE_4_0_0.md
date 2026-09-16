# Before mache #477 is merged: one run on each supported machine

*Written 2026/09/10 by Claude, at Xylar's request, and revised the same day. Committed so that it reaches the machines it is about; the commit comes out before merge along with the results it collects.*

**The release this guards is now v4.0.0, not v3.13.0.** `ResourcePlacement.cores` changed from one flat set of cores to one set per node, and that type is public, documented and shipped in v3.12.0, so the break earns a major version. Nothing about what to run changed with the number.

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

**Perlmutter and Frontier carry a different untested risk: GPUs.** Nothing has exercised `--gpus=N` through a placement, and the pool's rule that a placement spanning nodes leaves GPU indices unnamed -- because indices are node-local while the count is a total -- has never met a real GPU allocation.

## Where each machine stands, and what to run next

| machine | mechanism | state |
| --- | --- | --- |
| Chrysalis | CPU_BINDING, Slurm 20.02 | **done** -- serial/concurrent pair, fork spike, JIGSAW benchmark, node sweeps of `omega_pr` and `omega_nightly` at 3, 5, 8 and 13 nodes |
| Aurora | CPU_BINDING, PALS | **done** -- two clean concurrent runs (8831416, 8831432), 99/0 against the serial baseline, zero mismatches |
| Perlmutter GPU | SCHEDULER + CUDA | **run next** -- ran once (58363551) before the GPU probe and the count rule existed; the GPU axis is unverified |
| Frontier | SCHEDULER + ROCm, Slurm 25.11 | **after that** -- never run |
| Perlmutter CPU | SCHEDULER | optional -- ran once (58363133) before the count rule; one run confirms the whole-node 256/128 case, gates nothing |

All results are on the branch under `utils/concurrency_check/results/<machine>/`.

**What Aurora answered.** PALS restarts `--cpu-bind list:` on every node, exactly as Slurm 20.02 does -- the failure this note predicted -- and mache now renders one node's list there and refuses what cannot be said that way (`bf3e2905` on `xylar/mache`). Polaris was never misplacing steps by that route, because the pool gives a spanning step the same cores on every node. What *was* wrong was the id space: Aurora holds cores 0 and 52 back, and Polaris numbered from zero, so 40 of 115 steps were placed on a core the job did not have. That is fixed -- a node's cores are now read from what the job may use, one id per physical core -- and a run there says so at the top: `A node here keeps back core(s) [0, 52]; steps are placed on the other 102.`

**Why Perlmutter GPU before Frontier.** Two things are new since Perlmutter ran and have never touched a real machine: the placement check now holds a SCHEDULER machine to a *count* of physical cores rather than to particular ids, and it now asks every rank which GPUs it can see. pm-gpu exercises both on a machine whose environment, queue and known failures are already understood, so whatever it reports is about the checks and not the machine. Frontier exercises the same two on a machine this branch has never been deployed to, where a surprise would be ambiguous. So pm-gpu settles the checks, then Frontier is the last new machine.

**What pm-gpu has to capture**, beyond the usual:

- The per-node lines `placement: on <node> the ranks see device(s) ...`. The open question is what `CUDA_VISIBLE_DEVICES` holds under `--exact --gpus=N`: global indices, or `0` for every rank. If every rank reports `0` the GPU check can only ever catch a step seeing *more* than it was given, and the docs should say so; if they report global indices the check can be made exact. This one run decides that.
- Zero core mismatches. The earlier run recorded 32; every one was the count rule not yet existing. Any that appear now are real.
- A serial run on the same nodes, which the earlier visit did not make. Ten property checks failed there against four on pm-cpu, all salt conservation just over tolerance (1.6e-14 against 1e-14), which reads as GPU round-off but was never confirmed against a serial baseline.
- The node count the job script asks for. The sizing rule was changed after that run to take the suite's minima in cores on every machine; it asked pm-gpu for 29 nodes then and should ask for about 3 now.

## What to run

Any concurrent suite exercises the check, but it has to be one that **spans nodes**, since a single-node placement cannot show the bug this is looking for.

First deploy against the mache branch, because the version this needs is **not released** and the pinned one cannot place a launch at all:

```bash
./deploy.py -c <compiler> -i <mpi> \
    --mache-fork xylar/mache --mache-branch add-per-node-placement
```

Without those two flags you get the pinned mache and `polaris setup` refuses to go further, saying what capability is missing. That refusal is the expected behaviour, not a failure of this exercise.

Then set up and submit:

```bash
polaris suite -c ocean -t omega_pr --model omega --concurrent_steps \
    -p <build> -w <work dir>
# then, in the work directory
sbatch job_script.omega_pr.sh     # or qsub on Aurora
```

`--concurrent_steps` is the whole of it; there is no config file to write. It sets `[job] concurrent_steps`, so the choice is also recorded in the config the run was set up with.

The job script sizes itself. A concurrent suite now asks for the larger of the old rule -- the geometric mean of the widest step's target and minimum -- and the minimum cores of every step added together, so `omega_pr` asks for 5 nodes on a 64-core machine where it used to ask for 3. Machines with different cores per node will land elsewhere, which is fine: what matters is that steps span nodes, not the particular count. On Chrysalis at 3 nodes it gave 30 placed launches, 9 of them spanning.

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

**That shape only appears where the launcher binds cores explicitly** (Chrysalis, Aurora). On Perlmutter and Frontier the scheduler reserves a *count* and picks the cores itself, so particular numbers were never promised there and the check holds only the count. The pm-cpu and pm-gpu runs were made before the check knew that, which is why they recorded 39 and 32 "mismatches" that were nothing of the kind. A real one on those machines now reads:

```
On nid004492 the launch was allowed 128 cores but was given 12. This machine
reserves cores by count and chooses which, so only the count is held.
```

A launch given a whole node is allowed that node's hardware threads as well -- 256 for 128 on Perlmutter CPU -- and that is not reported.

**GPUs are now asked about too**, which they were not when pm-gpu ran. Each rank reports the devices it can see, and the step log says so per node:

```
placement: on nid001600 the ranks see device(s) 0,1,2,3
```

A step seeing *more* devices than its placement gave is a mismatch and appears in the summary line like any other. Seeing *fewer* is logged and not held against, because a launcher may number each rank's devices from zero. And a GPU step whose ranks report no device variable at all is said to be `GPUs not checked` -- **that is not a pass**, and on Frontier it would mean `ROCR_VISIBLE_DEVICES` was not what the ranks were given. Capture those per-node lines: they are what decides whether the GPU check can be tightened.

**Step failures are a separate question from placement.** `omega_pr` has failures on `main` that have nothing to do with any of this, and they will appear on your machine too. At the time of writing the property checks on `ocean/column/ekman/forward_constant` and the three `ocean/column/vmix_unstable/forward*` steps fail on `main`, with a fix in flight on `fix-omega-pr-failures`. A crash in the single-column viz steps was fixed by #750 and a race in `ocean/spherical/icos/cosine_bell/restart/restart_run` by #761, so neither should reappear -- if either does, that *is* worth reporting.

If you are unsure whether a failure is yours or the suite's, run the same suite serially on the same machine and compare. Only the placement summary line speaks to what this exercise is testing; a step can fail for its own reasons while placement is perfect, and placement can be wrong while every step passes, which is exactly how the Chrysalis bug hid.

**Also worth capturing**, since these runs are the only chance to collect it cheaply:

- The allocation report at the top of the run, which says what each node credits for memory against what the machine's config claims. Aurora's is now read: nodes report 1128000-1147000 MiB against the configured 960000, so that figure is conservative by about 16%, and one node was credited from a cgroup limit, the first time that branch of the reader has run on a real allocation.
- Whether any step reports `placement: not checked` -- that means the probe launch could not run or did not answer, which is a different failure from a mismatch and should not be read as a pass. It is also not, on its own, evidence about the machine: Aurora job 8831416 had two, both single-node steps started in a burst of nineteen on one node, and the next run had none. The check's own wording, `this machine may not pass a payload through`, overstates what one silent probe can show. Two in a burst is output lost under load; the same step silent twice is worth chasing.


## Reporting what you find

Commit the results to this branch, so that they come back to whoever is holding Phase B rather than living in a scratch directory on your machine.

The branch lives on **xylar's fork**, not on `E3SM-Project/polaris`:

```bash
git remote add xylar git@github.com:xylar/polaris.git   # if you have not already
git fetch xylar add-task-parallelism-phase-b
```

**This branch is rebased often**, so do not `git pull --rebase` onto an older copy of it -- take the fetched tip instead, and commit your results on top of that:

```bash
cd <your polaris worktree on add-task-parallelism-phase-b>
git fetch xylar add-task-parallelism-phase-b
git reset --hard xylar/add-task-parallelism-phase-b     # discards local commits
mkdir -p utils/concurrency_check/results/<machine>/<jobid>
cp <work dir>/polaris_<suite>.o<jobid> \
   <work dir>/<suite>_events.jsonl \
   utils/concurrency_check/results/<machine>/<jobid>/
git add utils/concurrency_check/results/<machine>/<jobid>
git commit --no-verify -m "Record what <machine> answered (job <jobid>)"
git push xylar HEAD:refs/heads/results-<machine>
```

Push to a **branch of your own** (`results-<machine>`) rather than onto the shared branch: it is being rebased, so a push to it would either be rejected or lose someone's work. Say where you pushed and the results get picked up from there.

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
