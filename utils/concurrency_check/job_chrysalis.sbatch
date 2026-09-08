#!/bin/bash
#SBATCH --job-name=node_memory_probe
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=1
#SBATCH --partition=debug
#SBATCH --time=00:05:00
#SBATCH --output=node_memory_%j.out

# What does a node say about the memory a job may use on it?
#
# Two nodes rather than one, so that the per-node reading is exercised
# rather than assumed, and so that a machine whose nodes differ says so.
# Add --account if the site needs one; Chrysalis's debug partition does not
# for a job this size.

# Slurm runs the batch script from its own spool directory, and $0 points
# there rather than at where this was submitted from, so the submit
# directory is what finds the probe.
cd "${SLURM_SUBMIT_DIR:-$(dirname "$0")}" || exit 1

echo "== job ${SLURM_JOB_ID} on ${SLURM_JOB_NODELIST} =="
echo

echo "== what the job's environment says =="
for name in SLURM_JOB_NUM_NODES SLURM_JOB_NODELIST SLURMD_NODENAME \
            SLURM_MEM_PER_NODE SLURM_MEM_PER_CPU; do
    printf '%s=%s\n' "$name" "${!name}"
done
echo

echo "== what each node says =="
srun --ntasks-per-node=1 ./probe_node_memory.sh
echo

echo "== what the controller advertises for these nodes =="
scontrol show nodes "$SLURM_JOB_NODELIST" |
    tr ' ' '\n' |
    grep -E '^(NodeName|RealMemory|AllocMem|FreeMem)=' || true
echo

echo "== what mache's config says, for comparison =="
echo "memory_per_node = 253000 MB  (chrysalis.cfg, surveyed smallest node)"
