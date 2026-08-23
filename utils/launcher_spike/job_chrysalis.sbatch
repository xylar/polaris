#!/bin/bash
# Launcher spike on Chrysalis.  Submit with:  sbatch job_chrysalis.sbatch
#
# Chrysalis runs Slurm 20.02, which predates the 20.11 job-step exclusivity
# change, so concurrent job steps are expected to work here even without
# --overlap.  That makes this the control machine: if B and B0 both show
# full concurrency, the flags are not what matters and the Perlmutter
# result is about Slurm version, not about Polaris.
#
# 64 cores/node (mache chrysalis.cfg), debug partition allows up to 10 nodes.

#SBATCH --job-name=launcher_spike
#SBATCH --nodes=2
#SBATCH --output=launcher_spike.o%j
#SBATCH --exclusive
#SBATCH --time=00:30:00
#SBATCH --partition=debug

cd "${SLURM_SUBMIT_DIR}"

source /etc/bashrc
module load python cmake

# 4 x 2 x 4 = 32 of 64 cores, leaving headroom so the no-flag control
# cannot fail for capacity reasons.
export SPIKE_SLOTS=4
export SPIKE_RANKS=2
export SPIKE_CPUS=4
export SPIKE_SLEEP=15
export SPIKE_OUTDIR="${SLURM_SUBMIT_DIR}/spike_results_${SLURM_JOB_ID}"
export SPIKE_SKIP_GPU=1   # no GPUs on Chrysalis

./spike_slurm.sh

# Compute nodes usually have no outbound network, so record from a login
# node once the job finishes:
echo
echo "To record these results on the branch, from a LOGIN node:"
echo "  cd ${SLURM_SUBMIT_DIR}/utils/launcher_spike 2>/dev/null || cd ${SLURM_SUBMIT_DIR}"
echo "  ./record_results.sh ${SPIKE_OUTDIR} ${SLURM_SUBMIT_DIR}/launcher_spike.o${SLURM_JOB_ID}"
