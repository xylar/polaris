#!/bin/bash
#SBATCH --job-name=jigsaw_threads
#SBATCH --nodes=1
#SBATCH --exclusive
#SBATCH --partition=debug
#SBATCH --time=00:55:00
#SBATCH --output=jigsaw_threads_%j.out

# How does JIGSAW scale with threads, and does the answer depend on
# resolution?  See jigsaw_thread_spike.py for why this is being asked.
#
#   sbatch utils/concurrency_check/jigsaw_thread_chrysalis.sbatch
#
# One node, exclusive, so a thread count means what it says.  The debug
# partition on Chrysalis allows this size; the harness keeps its own time
# budget well inside the wall clock and stops between resolutions rather
# than being killed part way through one.

# Slurm runs the batch script from its spool directory, so the submit
# directory is what finds the worktree.
cd "${SLURM_SUBMIT_DIR:-$(dirname "$0")}" || exit 1
TREE="$(cd ../.. && pwd)"
cd "$TREE" || exit 1

export NO_POLARIS_REINSTALL=true
source ./load_polaris_chrysalis_intel_openmpi.sh

WORK="${POLARIS_SCRATCH}/jigsaw_threads_${SLURM_JOB_ID}"
mkdir -p "$WORK"

echo "== node =="
scontrol show hostnames "$SLURM_JOB_NODELIST"
nproc
echo

# --budget stops between resolutions once this many seconds have gone, and
# --timeout caps any single jigsaw call, so one very slow point cannot eat
# the allocation.  Coarse first, so the cheap answers are recorded even if
# the fine ones are never reached.
python "$TREE/utils/concurrency_check/jigsaw_thread_spike.py" \
    --resolutions 240 120 60 30 12 \
    --threads 1 2 4 8 16 32 64 \
    --budget 2100 \
    --timeout 900 \
    --work "$WORK/meshes" \
    --out "$WORK/jigsaw_thread_scaling.txt"

echo
echo "== results =="
cat "$WORK/jigsaw_thread_scaling.txt"
