#!/usr/bin/env bash
# Payload run by every launch in the placement check.
#
# Each task records where it landed and when it ran, then sleeps long enough
# that genuine overlap between concurrent launches is unambiguous.  One file
# per (slot, rank) keeps concurrent writers from interleaving.
#
# This is the shell payload.  It exercises step creation and placement but
# not PMI bootstrap; mpi_payload.c is used instead whenever it can be built.

set -u

test_name="${PLACE_TEST:?PLACE_TEST must be set}"
slot="${PLACE_SLOT:?PLACE_SLOT must be set}"
outdir="${PLACE_OUTDIR:?PLACE_OUTDIR must be set}"
sleep_seconds="${PLACE_SLEEP:-15}"

# Rank id under whichever launcher started us.
rank="${SLURM_PROCID:-${PMI_RANK:-${PALS_RANKID:-${ALPS_APP_PE:-0}}}}"

cpus_allowed="$(awk '/Cpus_allowed_list/ {print $2}' /proc/self/status)"
ncpus="$(nproc 2>/dev/null || echo unknown)"

# Record every vendor variable, including the ones that are set but empty:
# whether an explicit "no GPUs" is rendered as an empty value or as no value
# at all is one of the things this check exists to find out.
gpu_env=""
for var in CUDA_VISIBLE_DEVICES ROCR_VISIBLE_DEVICES HIP_VISIBLE_DEVICES \
           ZE_AFFINITY_MASK; do
    if [ -n "${!var+set}" ]; then
        gpu_env="${gpu_env}${var}=${!var};"
    fi
done

# CUDA_VISIBLE_DEVICES is renumbered relative to each launch's own GRES
# allocation, so four launches on four different GPUs all report "0".
# SLURM_STEP_GPUS carries the global ids and is what distinguishes them.
step_gpus="${SLURM_STEP_GPUS:-}"
job_gpus="${SLURM_JOB_GPUS:-}"

mkdir -p "${outdir}/${test_name}"
out="${outdir}/${test_name}/slot${slot}_rank${rank}.kv"

start="$(date +%s.%N)"
sleep "${sleep_seconds}"
end="$(date +%s.%N)"

{
    echo "test=${test_name}"
    echo "slot=${slot}"
    echo "rank=${rank}"
    echo "host=$(hostname)"
    echo "pid=$$"
    echo "payload=shell"
    echo "cpus_allowed=${cpus_allowed}"
    echo "nproc=${ncpus}"
    echo "gpu_env=${gpu_env}"
    echo "step_gpus=${step_gpus}"
    echo "job_gpus=${job_gpus}"
    echo "t_start=${start}"
    echo "t_end=${end}"
} > "${out}"
