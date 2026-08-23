#!/bin/bash
# Placement check on Perlmutter GPU.  Submit with:  sbatch job_pm-gpu.sbatch
#
# This is one of the two runs that matter most.  It is where "a launch that
# says nothing about GPUs is read as claiming every GPU on the node" was
# first seen, and where the difference between --gpus=N and --gpus-per-task=1
# was measured.  What is being checked now is that mache renders the working
# form.
#
# Deploy with the machine default compiler (gnugpu).  Deploying with --compiler
# gnu instead would report zero GPUs per node, since mache's [parallel.gnu]
# section on this machine sets gpus_per_node = 0, and the GPU checks would
# silently be skipped.
#
# 64 cores and 4 GPUs/node, account e3sm, qos debug (mache pm-gpu.cfg).

#SBATCH --job-name=placement_check
#SBATCH --account=e3sm
#SBATCH --nodes=1
#SBATCH --output=placement_check.o%j
#SBATCH --exclusive
#SBATCH --time=00:30:00
#SBATCH --qos=debug
#SBATCH --constraint=gpu
#SBATCH --gpus-per-node=4

cd "${SLURM_SUBMIT_DIR}"

export PLACE_JOB_LOG="${SLURM_SUBMIT_DIR}/placement_check.o${SLURM_JOB_ID}"
export PLACE_OUTDIR="${SLURM_SUBMIT_DIR}/placement_results_${SLURM_JOB_ID}"

# 4 x 2 x 4 = 32 of 64 cores; 4 slots x 1 GPU = the node's 4 GPUs.
export PLACE_SLOTS=4
export PLACE_NTASKS=2
export PLACE_CPUS=4
export PLACE_SLEEP=15

./run_check.sh
