#!/bin/bash
# Launcher spike on Chrysalis.  Submit with:  sbatch job_chrysalis.sbatch
#
# Chrysalis runs Slurm 20.02, which predates the 20.11 change that made job
# steps exclusive by default and added --overlap/--exact.  Those flags are
# rejected outright here, so the driver detects the version and asks for
# placement with --cpu-bind=mask_cpu instead.  That makes this the control
# machine: it tells us whether an old-Slurm site can place concurrent steps
# at all, which mache's launcher would have to handle separately.
#
# 64 physical cores/node, debug partition allows up to 10 nodes
# (mache chrysalis.cfg).

#SBATCH --job-name=launcher_spike
#SBATCH --nodes=2
#SBATCH --output=launcher_spike.o%j
#SBATCH --exclusive
#SBATCH --time=00:30:00
#SBATCH --partition=debug

cd "${SLURM_SUBMIT_DIR}"

# No module loading here on purpose.  spike_slurm.sh sources the worktree's
# load_polaris_*.sh, which is what mache.deploy generated for this machine
# and is the only sanctioned source of the compiler, MPI and POLARIS_MACHINE.
# It needs ./deploy.py to have been run in this worktree (or set
# SPIKE_LOAD_SCRIPT to another worktree's load script).
export SPIKE_JOB_LOG="${SLURM_SUBMIT_DIR}/launcher_spike.o${SLURM_JOB_ID}"
export SPIKE_OUTDIR="${SLURM_SUBMIT_DIR}/spike_results_${SLURM_JOB_ID}"
export SPIKE_SLEEP=15

# 4 x 2 x 4 = 32 of 64 physical cores, leaving headroom so the no-flag
# control cannot fail for capacity reasons.
export SPIKE_SLOTS=4
export SPIKE_RANKS=2
export SPIKE_CPUS=4
export SPIKE_CORE_LIST="0-63"   # physical cores only; the node reports 128 with HT
export SPIKE_SKIP_GPU=1         # no GPUs on Chrysalis

./spike_slurm.sh
