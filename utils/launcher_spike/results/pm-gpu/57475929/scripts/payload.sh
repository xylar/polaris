#!/usr/bin/env bash
# Payload executed by every launcher spike task.
#
# Each task records where it landed and when it ran, then sleeps long
# enough that genuine overlap between concurrent launches is unambiguous.
# One file per (slot, rank) keeps concurrent writers from interleaving.

set -u

test_name="${SPIKE_TEST:?SPIKE_TEST must be set}"
slot="${SPIKE_SLOT:?SPIKE_SLOT must be set}"
outdir="${SPIKE_OUTDIR:?SPIKE_OUTDIR must be set}"
sleep_seconds="${SPIKE_SLEEP:-15}"

# Rank id under whichever launcher started us.
rank="${SLURM_PROCID:-${PMI_RANK:-${PALS_RANKID:-${ALPS_APP_PE:-0}}}}"

cpus_allowed="$(awk '/Cpus_allowed_list/ {print $2}' /proc/self/status)"
mems_allowed="$(awk '/Mems_allowed_list/ {print $2}' /proc/self/status)"
ncpus="$(nproc 2>/dev/null || echo unknown)"

gpu_env=""
for var in CUDA_VISIBLE_DEVICES ROCR_VISIBLE_DEVICES ZE_AFFINITY_MASK; do
    value="${!var:-}"
    if [ -n "${value}" ]; then
        gpu_env="${gpu_env}${var}=${value};"
    fi
done

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
    echo "cpus_allowed=${cpus_allowed}"
    echo "mems_allowed=${mems_allowed}"
    echo "nproc=${ncpus}"
    echo "gpu_env=${gpu_env}"
    echo "t_start=${start}"
    echo "t_end=${end}"
} > "${out}"
