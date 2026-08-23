#!/bin/bash
# Launcher spike on Perlmutter CPU.  Submit with:  sbatch job_pm-cpu.sbatch
#
# This is the machine the spike exists for.  Watch for:
#   A_sequential_overlap fast + B_concurrent_overlap peak == 4
#     -> job-step exclusivity was the problem, --overlap --exact fixes it
#   A_sequential_overlap slow (~1/min)
#     -> genuine launch throttling; Tier A is out, Flux is worth evaluating
#
# 128 cores/node, account e3sm, qos debug allows 8 nodes for 30 minutes
# (mache pm-cpu.cfg).

#SBATCH --job-name=launcher_spike
#SBATCH --account=e3sm
#SBATCH --nodes=2
#SBATCH --output=launcher_spike.o%j
#SBATCH --exclusive
#SBATCH --time=00:30:00
#SBATCH --qos=debug
#SBATCH --constraint=cpu

cd "${SLURM_SUBMIT_DIR}"

# No module loading here on purpose.  spike_slurm.sh sources the worktree's
# load_polaris_*.sh, which is what mache.deploy generated for this machine
# and is the only sanctioned source of the compiler, MPI and POLARIS_MACHINE.
# It needs ./deploy.py to have been run in this worktree (or set
# SPIKE_LOAD_SCRIPT to another worktree's load script).
export SPIKE_JOB_LOG="${SLURM_SUBMIT_DIR}/launcher_spike.o${SLURM_JOB_ID}"
export SPIKE_OUTDIR="${SLURM_SUBMIT_DIR}/spike_results_${SLURM_JOB_ID}"
export SPIKE_SLEEP=15

# 4 x 4 x 4 = 64 of 128 cores.
export SPIKE_SLOTS=4
export SPIKE_RANKS=4
export SPIKE_CPUS=4
export SPIKE_CORE_LIST="0-127"
export SPIKE_SKIP_GPU=1   # use job_pm-gpu.sbatch for the GPU case

./spike_slurm.sh
