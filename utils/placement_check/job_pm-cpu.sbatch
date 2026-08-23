#!/bin/bash
# Placement check on Perlmutter CPU.  Submit with:  sbatch job_pm-cpu.sbatch
#
# Slurm 25.11, so mache renders --exact plus an explicit --gres=none.  The
# --gres=none is the interesting part even here, where there are no GPUs:
# it is the flag that stops a launch claiming the node's GPUs, and it should
# be harmless on a machine that has none.
#
# 128 cores/node, account e3sm, qos debug allows 8 nodes for 30 minutes
# (mache pm-cpu.cfg).

#SBATCH --job-name=placement_check
#SBATCH --account=e3sm
#SBATCH --nodes=1
#SBATCH --output=placement_check.o%j
#SBATCH --exclusive
#SBATCH --time=00:30:00
#SBATCH --qos=debug
#SBATCH --constraint=cpu

cd "${SLURM_SUBMIT_DIR}"

export PLACE_JOB_LOG="${SLURM_SUBMIT_DIR}/placement_check.o${SLURM_JOB_ID}"
export PLACE_OUTDIR="${SLURM_SUBMIT_DIR}/placement_results_${SLURM_JOB_ID}"

# 4 x 2 x 4 = 32 of 128 cores.
export PLACE_SLOTS=4
export PLACE_NTASKS=2
export PLACE_CPUS=4
export PLACE_SLEEP=15
export PLACE_SKIP_GPU=1   # use job_pm-gpu.sbatch for the GPU case

./run_check.sh
