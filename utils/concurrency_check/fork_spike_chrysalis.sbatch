#!/bin/bash
#SBATCH --job-name=fork_spike
#SBATCH --nodes=3
#SBATCH --exclusive
#SBATCH --partition=debug
#SBATCH --time=00:50:00
#SBATCH --output=fork_spike_%j.out

# Does forking a step cost less than exec-ing one, and does a forked step
# still run correctly?
#
# Phase B starts each step as a fresh `polaris serial` subprocess, which
# spends about 35 s importing Python before it does any work.  On omega_pr
# that consumed the entire speedup: 13:04 concurrent against 12:15 serial.
# The proposal is to fork from the scheduler instead, so that a child
# inherits the modules the parent already imported and the live Step object
# it already holds.
#
# The job runs in two parts.
#
# 1. `polaris parallel` over the whole suite, which populates the work
#    directory so that the steps the spike re-runs have their inputs, and
#    which re-measures the concurrent wall time as a side effect.
# 2. `fork_spike.py`, which runs four steps both ways in this same
#    allocation, minutes apart, so the difference between the two is the
#    mechanism rather than the machine.
#
# Submit from the spike work directory, which `setup_spike.sh` made:
#
#   sbatch --export=ALL,SPIKE_WORK=<work_dir> fork_spike_chrysalis.sbatch

set -e

WORK="${SPIKE_WORK:?set SPIKE_WORK to the spike work directory}"
BRANCH=/gpfs/fs1/home/ac.xylar/e3sm_work/polaris/add-task-parallelism-phase-b

echo "== job ${SLURM_JOB_ID} on ${SLURM_JOB_NODELIST} =="
echo "== work dir ${WORK} =="
echo

cd "${WORK}"
source ./load_polaris_env.sh

echo "======================================================================"
echo "part 1: populate the work directory, and re-measure the exec path"
echo "======================================================================"
date +'start %H:%M:%S'
# `|| true` because the spike is worth running even if a step fails here;
# part 2 reports what it could and could not run
polaris parallel omega_pr || true
date +'end   %H:%M:%S'
echo

echo "======================================================================"
echo "part 2: the same steps, forked and exec-ed"
echo "======================================================================"

# Four steps, chosen from what job 1283412 measured.  The first three are
# 1-core Python steps whose whole recorded duration was about the startup,
# so fork should make them nearly free -- they are where the effect is
# largest and easiest to read.  The fourth is an MPI step, which is the one
# that answers whether a forked child can reach `srun` and come back.  It
# matters most: MPI work is 79% of this suite.
STEPS="ocean/column/inertial/analysis
ocean/column/thermo/conservation_summary
ocean/planar/manufactured_solution/convergence_both/del4/analysis
ocean/planar/baroclinic_channel/10km/restart/full_run"

# The spike is run twice, because a probe on the login node found `import
# numpy` bringing up an OpenBLAS thread pool of one thread per core.  Those
# threads are invisible to `threading.enumerate()` and they are exactly what
# makes a fork unsafe: only the forking thread survives, so a lock held by
# any of the others is held forever in the child.
#
# The first pass forks with the pool present, which is what the scheduler
# would do today.  The second holds the pool to one thread before anything
# is imported, which is the mitigation -- the scheduler does no numerical
# work, so it costs it nothing.  If the first pass hangs and the second does
# not, the mitigation is required rather than prudent.

for pass_name in as_is single_threaded; do
    echo
    echo "---------------------------------------------------------------"
    echo "pass: ${pass_name}"
    echo "---------------------------------------------------------------"
    if [ "${pass_name}" = single_threaded ]; then
        export OMP_NUM_THREADS=1
        export OPENBLAS_NUM_THREADS=1
        export MKL_NUM_THREADS=1
    fi
    # a hang is the failure this is looking for, so bound it rather than
    # letting it eat the allocation
    timeout 900 "${BRANCH}/utils/concurrency_check/fork_spike.py" \
        -w "${WORK}" \
        -s omega_pr \
        --repeat 2 \
        --fanout 24 \
        --steps ${STEPS} || echo "pass ${pass_name} exited ${?}"
done

echo
date +'done  %H:%M:%S'
