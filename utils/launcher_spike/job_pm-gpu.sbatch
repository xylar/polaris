#!/bin/bash
# Launcher spike on Perlmutter GPU.  Submit with:  sbatch job_pm-gpu.sbatch
#
# Same questions as pm-cpu, plus whether --gpus-per-task survives concurrent
# job steps.  That combination is the one Phase 3 would need and is the
# least exercised path in the existing Polaris work.
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

module load cray-python cmake

# 4 x 1 x 8 = 32 of 64 cores; 4 slots x 1 GPU = 4 of 4 GPUs.
export SPIKE_SLOTS=4
export SPIKE_RANKS=1
export SPIKE_CPUS=8
export SPIKE_SLEEP=15
export SPIKE_OUTDIR="${SLURM_SUBMIT_DIR}/spike_results_${SLURM_JOB_ID}"

./spike_slurm.sh

# Compute nodes usually have no outbound network, so record from a login
# node once the job finishes:
echo
echo "To record these results on the branch, from a LOGIN node:"
echo "  cd ${SLURM_SUBMIT_DIR}/utils/launcher_spike 2>/dev/null || cd ${SLURM_SUBMIT_DIR}"
echo "  ./record_results.sh ${SPIKE_OUTDIR} ${SLURM_SUBMIT_DIR}/launcher_spike.o${SLURM_JOB_ID}"
