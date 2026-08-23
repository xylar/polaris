#!/bin/bash
# Placement check on Frontier.  Submit with:  sbatch job_frontier.sbatch
#
# The second machine that matters most, and the one where a plain srun was
# measured to serialize because a step reserves the whole allocation unless
# told otherwise.
#
# IMPORTANT: deploy with --compiler craygnu_mphipcc.  mache's frontier.cfg
# puts gpus_per_node = 8 in [parallel.craygnu_mphipcc] and leaves the base
# [parallel] section at 0, so a deployment with the machine default compiler
# (craygnu) reports no GPUs and the GPU checks are skipped without failing.
# The check says so in its output, but it is much cheaper to get right here.
#
# 56 usable cores and 8 GPUs (GCDs)/node, account cli115, batch partition with
# the debug qos capped at 2 hours (mache frontier.cfg).

#SBATCH --job-name=placement_check
#SBATCH --account=cli115
#SBATCH --nodes=1
#SBATCH --output=placement_check.o%j
#SBATCH --exclusive
#SBATCH --time=00:30:00
#SBATCH --partition=batch
#SBATCH --qos=debug
#SBATCH --gpus-per-node=8

cd "${SLURM_SUBMIT_DIR}"

export PLACE_JOB_LOG="${SLURM_SUBMIT_DIR}/placement_check.o${SLURM_JOB_ID}"
export PLACE_OUTDIR="${SLURM_SUBMIT_DIR}/placement_results_${SLURM_JOB_ID}"

# 4 x 2 x 4 = 32 of 56 cores; 4 slots x 2 GPUs = the node's 8 GCDs.
export PLACE_SLOTS=4
export PLACE_NTASKS=2
export PLACE_CPUS=4
export PLACE_SLEEP=15

./run_check.sh
