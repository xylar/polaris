#!/bin/bash
# Launcher spike on Frontier.  Submit with:  sbatch job_frontier.sbatch
#
# Frontier runs a post-20.11 Slurm, so it should show the same job-step
# exclusivity behavior as Perlmutter.  If the two agree, that is good
# evidence the effect is the Slurm version rather than anything site
# specific.
#
# 56 usable cores and 8 GPUs (GCDs)/node, account cli115, batch partition
# with the debug qos capped at 2 hours (mache frontier.cfg).

#SBATCH --job-name=launcher_spike
#SBATCH --account=cli115
#SBATCH --nodes=2
#SBATCH --output=launcher_spike.o%j
#SBATCH --exclusive
#SBATCH --time=00:30:00
#SBATCH --partition=batch
#SBATCH --qos=debug
#SBATCH --gpus-per-node=8

cd "${SLURM_SUBMIT_DIR}"

# No module loading here on purpose.  spike_slurm.sh sources the worktree's
# load_polaris_*.sh, which is what mache.deploy generated for this machine
# and is the only sanctioned source of the compiler, MPI and POLARIS_MACHINE.
# It needs ./deploy.py to have been run in this worktree (or set
# SPIKE_LOAD_SCRIPT to another worktree's load script).
export SPIKE_JOB_LOG="${SLURM_SUBMIT_DIR}/launcher_spike.o${SLURM_JOB_ID}"
export SPIKE_OUTDIR="${SLURM_SUBMIT_DIR}/spike_results_${SLURM_JOB_ID}"
export SPIKE_SLEEP=15

# 4 x 2 x 4 = 32 of 56 cores; the GPU test uses 4 slots x 2 ranks x 1 GPU,
# which is exactly the 8 GCDs on a node.
export SPIKE_SLOTS=4
export SPIKE_RANKS=2
export SPIKE_CPUS=4
export SPIKE_CORE_LIST="0-55"

./spike_slurm.sh
