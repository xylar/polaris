#!/bin/bash
# Launcher spike on Perlmutter CPU.  Submit with:  sbatch job_pm-cpu.sbatch
#
# This is the machine the whole spike exists for.  Watch for:
#   A_sequential_overlap fast  + B_concurrent_overlap peak == 4
#     -> job-step exclusivity was the problem, --overlap --exact fixes it
#   A_sequential_overlap slow (~1/min)
#     -> genuine launch throttling; Tier A is out, Flux is worth evaluating
#
# 128 cores/node, account e3sm, qos debug allows 8 nodes for 30 minutes
# (mache pm-cpu.cfg).  Cray `cc` provides the MPI compiler.

#SBATCH --job-name=launcher_spike
#SBATCH --account=e3sm
#SBATCH --nodes=2
#SBATCH --output=launcher_spike.o%j
#SBATCH --exclusive
#SBATCH --time=00:30:00
#SBATCH --qos=debug
#SBATCH --constraint=cpu

cd "${SLURM_SUBMIT_DIR}"

module load cray-python cmake

# 4 x 4 x 4 = 64 of 128 cores.
export SPIKE_SLOTS=4
export SPIKE_RANKS=4
export SPIKE_CPUS=4
export SPIKE_SLEEP=15
export SPIKE_OUTDIR="${SLURM_SUBMIT_DIR}/spike_results_${SLURM_JOB_ID}"
export SPIKE_SKIP_GPU=1   # use job_pm-gpu.sbatch for the GPU case

./spike_slurm.sh

# Compute nodes usually have no outbound network, so record from a login
# node once the job finishes:
echo
echo "To record these results on the branch, from a LOGIN node:"
echo "  cd ${SLURM_SUBMIT_DIR}/utils/launcher_spike 2>/dev/null || cd ${SLURM_SUBMIT_DIR}"
echo "  ./record_results.sh ${SPIKE_OUTDIR} ${SLURM_SUBMIT_DIR}/launcher_spike.o${SLURM_JOB_ID}"
