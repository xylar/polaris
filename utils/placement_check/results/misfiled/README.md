# Runs that did not happen on the machine they were labelled with

A run records the machine its *deployment* claims, from `POLARIS_MACHINE`. Nothing in it looked at the node. Where a job script for one machine was submitted from a worktree deployed for another, the result is filed under the wrong machine and reads as evidence for it.

## 57518892 -- labelled pm-cpu, ran on a Perlmutter GPU node

Three signals from the hardware, none from configuration, agree:

| | this run | a genuine pm-cpu run |
| --- | --- | --- |
| `job_gpus` | `0,1,2,3` | empty |
| hyperthread siblings | 64 apart -> 64 physical cores | 128 apart -> 128 |
| memory reported | 257200 MB | 515100 MB |

So the job was allocated four GPUs on a 64-core node: a GPU node, under `--constraint=gpu`, while `machine=pm-cpu` and `cores_on_node=128` came from a pm-cpu deployment.

**What it is not evidence for.** Not pm-cpu, because it did not run there. Not pm-gpu either, because it rendered its commands from pm-cpu's config -- a different launcher line, 128 cores per node, no GPUs -- which is not what a pm-gpu run produces. Its placement verdicts are real but they belong to a configuration no machine actually has.

It is kept rather than deleted because it is the only recorded instance of this failure, and because the cross-check that now runs at launch was written from it.

The genuine pm-cpu results are `pm-cpu/57518889` and `pm-cpu/57519870`, which agree with each other at 515100 MB.
