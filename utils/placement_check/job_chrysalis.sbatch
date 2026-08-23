#!/bin/bash
# Placement check on Chrysalis.  Submit with:  sbatch job_chrysalis.sbatch
#
# Chrysalis runs Slurm 20.02, which predates the 20.11 change that made job
# steps reserve what they ask for and added --exact.  Those flags are
# rejected outright here, so mache renders an explicit --cpu-bind=mask_cpu
# instead.  That makes this the machine that exercises the other half of
# mache's Slurm rendering, and the only one where the cores a launch is given
# should come back exactly as asked rather than as a count Slurm satisfies
# however it likes.
#
# 64 physical cores/node, debug partition allows up to 10 nodes
# (mache chrysalis.cfg).

#SBATCH --job-name=placement_check
#SBATCH --nodes=1
#SBATCH --output=placement_check.o%j
#SBATCH --exclusive
#SBATCH --time=00:30:00
#SBATCH --partition=debug

cd "${SLURM_SUBMIT_DIR}"

# No module loading here on purpose.  run_check.sh sources this worktree's
# load_polaris_*.sh, which is what mache.deploy generated for this machine and
# is the only sanctioned source of the compiler, the MPI and POLARIS_MACHINE.
export PLACE_JOB_LOG="${SLURM_SUBMIT_DIR}/placement_check.o${SLURM_JOB_ID}"
export PLACE_OUTDIR="${SLURM_SUBMIT_DIR}/placement_results_${SLURM_JOB_ID}"

# 4 x 2 x 4 = 32 of 64 cores, so the unplaced control cannot fail for lack of
# capacity.  All four slots share one node on purpose: sharing a node is the
# hard case for placement, and spreading across nodes is not what we are
# unsure about.
export PLACE_SLOTS=4
export PLACE_NTASKS=2
export PLACE_CPUS=4
export PLACE_SLEEP=15
export PLACE_SKIP_GPU=1   # no GPUs on Chrysalis

./run_check.sh
