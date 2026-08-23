#!/bin/bash
# Launcher spike on Perlmutter GPU.  Submit with:  sbatch job_pm-gpu.sbatch
#
# Same questions as pm-cpu, plus whether --gpus-per-task survives concurrent
# job steps.  That combination is what Phase 3 would need and is the least
# exercised path in the existing Polaris work.
#
# 64 cores and 4 GPUs/node, account e3sm, qos debug (mache pm-gpu.cfg).
# max_mpi_tasks_per_node is the GPU count, so one rank per slot keeps the
# GPU test at 4 slots x 1 GPU = 4 GPUs.

#SBATCH --job-name=launcher_spike
#SBATCH --account=e3sm
#SBATCH --nodes=2
#SBATCH --output=launcher_spike.o%j
#SBATCH --exclusive
#SBATCH --time=00:30:00
#SBATCH --qos=debug
#SBATCH --constraint=gpu
#SBATCH --gpus-per-node=4

cd "${SLURM_SUBMIT_DIR}"

# No module loading here on purpose.  spike_slurm.sh sources the worktree's
# load_polaris_*.sh, which is what mache.deploy generated for this machine
# and is the only sanctioned source of the compiler, MPI and POLARIS_MACHINE.
# It needs ./deploy.py to have been run in this worktree (or set
# SPIKE_LOAD_SCRIPT to another worktree's load script).
export SPIKE_JOB_LOG="${SLURM_SUBMIT_DIR}/launcher_spike.o${SLURM_JOB_ID}"
export SPIKE_OUTDIR="${SLURM_SUBMIT_DIR}/spike_results_${SLURM_JOB_ID}"
export SPIKE_SLEEP=15

# 4 x 1 x 8 = 32 of 64 cores; 4 slots x 1 GPU = 4 of 4 GPUs.
export SPIKE_SLOTS=4
export SPIKE_RANKS=1
export SPIKE_CPUS=8
export SPIKE_CORE_LIST="0-63"

./spike_slurm.sh
