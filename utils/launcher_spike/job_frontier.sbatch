#!/bin/bash
# Launcher spike on Frontier.  Submit with:  sbatch job_frontier.sbatch
#
# Frontier runs a post-20.11 Slurm, so it should show the same job-step
# exclusivity behavior as Perlmutter.  If Frontier and Perlmutter agree,
# that is good evidence the effect is the Slurm version rather than
# anything site specific.
#
# 56 cores and 8 GPUs (GCDs)/node, account cli115, batch partition with the
# debug qos capped at 2 hours (mache frontier.cfg).  Cray `cc` provides the
# MPI compiler.

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

module load cray-python cmake

# 4 x 2 x 4 = 32 of 56 cores; the GPU test uses 4 slots x 2 ranks x 1 GPU,
# which is exactly the 8 GCDs on a node.
export SPIKE_SLOTS=4
export SPIKE_RANKS=2
export SPIKE_CPUS=4
export SPIKE_SLEEP=15
export SPIKE_OUTDIR="${SLURM_SUBMIT_DIR}/spike_results_${SLURM_JOB_ID}"

# If --gpus-per-task turns out to conflict with --overlap here, rerun with
# SPIKE_SKIP_GPU=1 to get the CPU answer, which is the one that matters most.
./spike_slurm.sh

# Compute nodes usually have no outbound network, so record from a login
# node once the job finishes:
echo
echo "To record these results on the branch, from a LOGIN node:"
echo "  cd ${SLURM_SUBMIT_DIR}/utils/launcher_spike 2>/dev/null || cd ${SLURM_SUBMIT_DIR}"
echo "  ./record_results.sh ${SPIKE_OUTDIR} ${SLURM_SUBMIT_DIR}/launcher_spike.o${SLURM_JOB_ID}"
