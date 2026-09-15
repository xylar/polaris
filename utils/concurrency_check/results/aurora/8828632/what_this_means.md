# What job 8828632 does and does not show

Written after the fact, correcting the commit that recorded the job.

## PALS behaves exactly as Slurm does

The measurement stands: PALS applies `--cpu-bind list:` to each node's
tasks by their index *on that node* and starts again at the beginning of
the list for every node. Three of four cases showed it, the passing one
being the single-node control.

That was the open question. It could not be asked from Chrysalis, mache
 #477 deliberately left it alone, and the answer is that Aurora's launcher
has the same semantics as Chrysalis's Slurm 20.02 rather than different
ones.

## It is not a defect in what Polaris runs

The spike builds a placement whose nodes ask for *different* core numbers
-- node 0 gets 1-4, node 1 gets 61-64. Polaris never asks for one.
`Give a spanning step the same core numbers on every node` made the pool
take a spanning step's cores from the set free on every node it lands on,
precisely because Slurm restarts the list. That fix was reasoned from
Slurm's behaviour and this result shows it covers Aurora too, for the same
reason and without change.

So a concurrent Polaris run on Aurora is not placing steps wrongly today,
and nothing here blocks mache #477.

## The gap it does find is in mache's PBS renderer

`slurm.py` guards the case this spike constructs:
`_check_one_mask_list_serves_every_node()` raises rather than render a
placement whose later nodes want cores the first node's list will not give
them. `pbs.py` has no equivalent -- it calls the flat `split_cores()` and
joins the result -- so the same placement renders silently and the launch
runs on cores nobody chose.

Polaris does not hit this because the pool no longer asks. Any other
caller does, and so would a future Polaris that relaxed the pool rule on
the grounds that it was a Slurm-specific workaround. Mirroring the Slurm
guard into `pbs.py` closes it.

## Two Polaris-side findings, which do stand

Both are in `polaris/run/allocation.py` and neither is about placement:

- The per-node memory reader probes core 0, which PALS refuses on Aurora
  (`affinity setup: object has too few CPUs for rank`, exit 139). The run
  falls back to the configured figure and says so.
- It keys readings by the name in `PBS_NODEFILE`, which on Aurora is fully
  qualified, against a probe that reports the short hostname, so every node
  would read as silent even once the probe could run.
  `polaris/run/confinement.py` does not have this bug.
