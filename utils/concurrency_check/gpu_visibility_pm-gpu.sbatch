#!/bin/bash
#SBATCH --job-name=gpu_visibility
#SBATCH --account=e3sm
#SBATCH --nodes=1
#SBATCH --output=gpu_visibility.o%j
#SBATCH --exclusive
#SBATCH --time=00:10:00
#SBATCH --qos=debug
#SBATCH --constraint=gpu
#SBATCH --gpus-per-node=4

# What does a rank see under `srun --exact --gpus=N`, and which GPU does it
# actually get?
#
# The placement check asks each rank for CUDA_VISIBLE_DEVICES and unions the
# answers per node.  Every step it probed in omega_pr was given all four of
# a node's GPUs, and the one-GPU steps -- four to a node, the case where a
# wrong answer would put two steps on one GPU -- are single-task and never
# probed.  So the union `0,1,2,3` decided nothing.  This asks per rank, and
# asks the driver for the device's UUID beside the variable, so that "what
# the variable says" and "which GPU the rank really has" are answered
# separately.

set -u
cd "$SLURM_SUBMIT_DIR"

probe() {
    # one line per rank: label, host, rank, the variable, then what the
    # driver says this process can reach -- index and UUID of each device.
    # nvidia-smi inside a confined launch lists only the devices the
    # process's cgroup lets it open, so this is the physical answer, held
    # beside what the variable claims.  Plain shell, because the compute
    # node's python3 is old enough to lack subprocess.run(capture_output).
    local smi
    smi=$(nvidia-smi --query-gpu=index,uuid --format=csv,noheader 2>&1 | tr '\n' '|')
    echo "${LABEL:-?} host=$(hostname) rank=${SLURM_PROCID:-?} CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:--} nvidia-smi=[${smi}]"
}
export -f probe

echo "=== node view: every GPU the job has ==="
srun -N 1 -n 1 nvidia-smi --query-gpu=index,uuid --format=csv,noheader
echo

echo "=== A: one launch, 4 tasks, --gpus=4 (what every probed step looked like) ==="
LABEL=A srun --label -c 1 -N 1 -n 4 --exact --gpus=4 --cpu-bind=cores bash -c probe
echo

echo "=== B: one launch, 2 tasks, --gpus=2 (a partial node, never probed) ==="
LABEL=B srun --label -c 1 -N 1 -n 2 --exact --gpus=2 --cpu-bind=cores bash -c probe
echo

echo "=== C: four concurrent launches, 1 task each, --gpus=1 (the one-GPU steps' shape) ==="
for i in 1 2 3 4; do
    LABEL=C$i srun --label -c 1 -N 1 -n 1 --exact --gpus=1 --cpu-bind=cores bash -c probe &
done
wait
echo

echo "=== D: two concurrent launches, 2 tasks each, --gpus=2 ==="
for i in 1 2; do
    LABEL=D$i srun --label -c 1 -N 1 -n 2 --exact --gpus=2 --cpu-bind=cores bash -c probe &
done
wait
echo "done"
