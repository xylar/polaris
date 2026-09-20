#!/bin/bash
#SBATCH --job-name=gpu_visibility
#SBATCH --account=cli115
#SBATCH --nodes=1
#SBATCH --output=gpu_visibility.o%j
#SBATCH --exclusive
#SBATCH --time=00:10:00
#SBATCH --partition=batch
#SBATCH --qos=debug
#SBATCH --gpus-per-node=8

# The Frontier counterpart of gpu_visibility_pm-gpu.sbatch: what does a rank
# see under `srun --exact --gpus=N`, and which GPU does it actually get?
#
# The placement check reads ROCR_VISIBLE_DEVICES per rank and unions the
# answers per node.  On pm-gpu that union decided nothing, because every
# probed step held all of a node's GPUs and CUDA_VISIBLE_DEVICES turned out
# to be renumbered from zero per launch.  This asks the same questions of
# ROCm: what the variable holds per rank, and which physical device the
# rank can reach, told apart by asking the runtime with the variable
# cleared.  rocminfo lists the GPU agents the process can open, which is
# what the job step's cgroup allows, and each carries a Uuid; with
# ROCR_VISIBLE_DEVICES unset that is the physical answer, and with it kept
# it is what a step's own code would see.
#
# Frontier also has --gpu-bind=closest in mache's config for the GPU
# compilers, which is not what pm-gpu had (gpu_bind = none), so within one
# launch each rank may see one device rather than all of them.  E is the
# same launch as A with that flag added, so the two can be compared.

set -u
cd "$SLURM_SUBMIT_DIR"
module load rocm/6.2.4 >/dev/null 2>&1

probe() {
    # one line per rank: label, host, rank, the variable, then the Uuids
    # the runtime can open with the variable cleared (physical) and with it
    # kept (what the step sees).
    local physical seen
    physical=$(env -u ROCR_VISIBLE_DEVICES rocminfo 2>&1 | grep -E '^\s*Uuid:\s+GPU-' | awk '{print $2}' | tr '\n' ',')
    seen=$(rocminfo 2>&1 | grep -E '^\s*Uuid:\s+GPU-' | awk '{print $2}' | tr '\n' ',')
    echo "${LABEL:-?} host=$(hostname) rank=${SLURM_PROCID:-?} ROCR_VISIBLE_DEVICES=${ROCR_VISIBLE_DEVICES:--} physical=[${physical}] seen=[${seen}]"
}
export -f probe

echo "=== node view: every GPU the job has ==="
srun -N 1 -n 1 bash -c 'env -u ROCR_VISIBLE_DEVICES rocminfo | grep -E "^\s*(Uuid:\s+GPU-|Node:)" ; rocm-smi --showuniqueid --showbus --csv'
echo

echo "=== A: one launch, 8 tasks, --gpus=8 (a whole node) ==="
LABEL=A srun --label -c 1 -N 1 -n 8 --exact --gpus=8 --cpu-bind=cores bash -c probe
echo

echo "=== B: one launch, 2 tasks, --gpus=2 (a partial node) ==="
LABEL=B srun --label -c 1 -N 1 -n 2 --exact --gpus=2 --cpu-bind=cores bash -c probe
echo

echo "=== C: eight concurrent launches, 1 task each, --gpus=1 (the one-GPU steps' shape) ==="
for i in 1 2 3 4 5 6 7 8; do
    LABEL=C$i srun --label -c 1 -N 1 -n 1 --exact --gpus=1 --cpu-bind=cores bash -c probe &
done
wait
echo

echo "=== D: two concurrent launches, 2 tasks each, --gpus=2 ==="
for i in 1 2; do
    LABEL=D$i srun --label -c 1 -N 1 -n 2 --exact --gpus=2 --cpu-bind=cores bash -c probe &
done
wait
echo

echo "=== E: as A, with --gpu-bind=closest (what mache renders for Omega here) ==="
LABEL=E srun --label -c 1 -N 1 -n 8 --exact --gpus=8 --cpu-bind=cores --gpu-bind=closest bash -c probe
echo

echo "=== F: two concurrent launches, 4 tasks each, --gpus=4 --gpu-bind=closest ==="
for i in 1 2; do
    LABEL=F$i srun --label -c 1 -N 1 -n 4 --exact --gpus=4 --cpu-bind=cores --gpu-bind=closest bash -c probe &
done
wait
echo "done"
