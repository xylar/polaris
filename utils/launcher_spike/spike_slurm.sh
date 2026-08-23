#!/usr/bin/env bash
# Launcher spike for Slurm machines (Perlmutter, Chrysalis, Frontier).
#
# Answers two questions that the Polaris task-parallelism design depends on:
#
#   1. Is `srun` itself rate limited, or is it only *concurrent* job steps
#      that are blocked?  (test A vs test B)
#   2. Does `--overlap --exact` let several placed job steps coexist, at
#      single-task and at MPI width?  (tests B, C, D vs the B0 control)
#
# Run inside an allocation, e.g.
#   salloc -N 2 -t 00:30:00 -C cpu -q interactive -A <acct>
#   ./spike_slurm.sh

set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

slots="${SPIKE_SLOTS:-4}"
cpus="${SPIKE_CPUS:-8}"
ranks="${SPIKE_RANKS:-4}"
sleep_seconds="${SPIKE_SLEEP:-15}"
seq_n="${SPIKE_SEQ_N:-10}"
outdir="${SPIKE_OUTDIR:-${PWD}/spike_results_$(date +%Y%m%d_%H%M%S)}"
launch_timeout="${SPIKE_TIMEOUT:-$((sleep_seconds + 120))}"

if [ -z "${SLURM_JOB_ID:-}" ]; then
    echo "ERROR: run this inside a Slurm allocation (salloc or sbatch)." >&2
    exit 1
fi

mapfile -t nodes < <(scontrol show hostnames "${SLURM_JOB_NODELIST}")
node="${SPIKE_NODE:-${nodes[0]}}"
nnodes="${#nodes[@]}"

mkdir -p "${outdir}"
export SPIKE_OUTDIR="${outdir}"
export SPIKE_SLEEP="${sleep_seconds}"

echo "=== Polaris launcher spike (Slurm) ==="
echo "slurm version:  $(srun --version 2>/dev/null || echo unknown)"
echo "job id:         ${SLURM_JOB_ID}"
echo "nodes:          ${nnodes} (${SLURM_JOB_NODELIST})"
echo "target node:    ${node}"
echo "cores on node:  ${SLURM_CPUS_ON_NODE:-unknown}"
echo "slots:          ${slots} x ${cpus} cpus"
echo "mpi ranks:      ${ranks} per launch"
echo "payload sleep:  ${sleep_seconds}s"
echo "results:        ${outdir}"
echo

# Build the MPI payload if we can; otherwise fall back to the shell payload
# launched at MPI width (still exercises step creation and placement, but
# not PMI bootstrap).
mpi_exe="${here}/payload.sh"
mpi_kind="shell-fallback"
if command -v mpicc >/dev/null 2>&1; then
    if mpicc -O0 -o "${outdir}/mpi_payload" "${here}/mpi_payload.c" \
            >"${outdir}/mpicc.log" 2>&1; then
        mpi_exe="${outdir}/mpi_payload"
        mpi_kind="mpicc"
    else
        echo "WARNING: mpicc failed, see ${outdir}/mpicc.log" >&2
    fi
fi
echo "mpi payload:    ${mpi_kind} (${mpi_exe})"
echo

elapsed () {
    awk -v a="$1" -v b="$2" 'BEGIN { printf "%.2f", b - a }'
}

run_sequential () {
    local test_name="$1"; shift
    local -a extra=("$@")
    local t0 t1
    echo "--- ${test_name}: ${seq_n} sequential launches (${extra[*]:-no extra flags})"
    mkdir -p "${outdir}/${test_name}"
    t0="$(date +%s.%N)"
    for i in $(seq 1 "${seq_n}"); do
        local s0 s1 rc
        s0="$(date +%s.%N)"
        timeout "${launch_timeout}" srun "${extra[@]}" -n 1 -c 1 \
            -w "${node}" true \
            >>"${outdir}/${test_name}/launches.out" \
            2>>"${outdir}/${test_name}/launches.err"
        rc=$?
        s1="$(date +%s.%N)"
        echo "launch=${i} rc=${rc} seconds=$(elapsed "${s0}" "${s1}")" \
            >> "${outdir}/${test_name}/timings.kv"
    done
    t1="$(date +%s.%N)"
    echo "    total $(elapsed "${t0}" "${t1}")s for ${seq_n} launches"
}

run_concurrent () {
    local test_name="$1"; shift
    local exe="$1"; shift
    local nranks="$1"; shift
    local -a extra=("$@")
    echo "--- ${test_name}: ${slots} concurrent launches x ${nranks} rank(s)"
    mkdir -p "${outdir}/${test_name}"
    local pids=()
    for slot in $(seq 1 "${slots}"); do
        (
            export SPIKE_TEST="${test_name}"
            export SPIKE_SLOT="${slot}"
            timeout "${launch_timeout}" srun "${extra[@]}" \
                -N 1 -n "${nranks}" -c "${cpus}" -w "${node}" "${exe}" \
                >"${outdir}/${test_name}/slot${slot}.out" \
                2>"${outdir}/${test_name}/slot${slot}.err"
            echo "rc=$?" > "${outdir}/${test_name}/slot${slot}.rc"
        ) &
        pids+=("$!")
    done
    for pid in "${pids[@]}"; do
        wait "${pid}"
    done
    echo "    done"
}

# Test A: is srun itself throttled?  Sequential, one at a time.
run_sequential A_sequential_overlap --overlap --exact
run_sequential A0_sequential_plain

# Test B: do concurrent placed steps coexist?
run_concurrent B_concurrent_overlap "${here}/payload.sh" 1 --overlap --exact
# Test B0 is the control: same thing without --overlap.  Expect this one to
# serialize or emit "step creation temporarily disabled" on Slurm >= 20.11.
run_concurrent B0_concurrent_plain "${here}/payload.sh" 1

# Test C: the Phase 3 primitive -- concurrent launches at MPI width.
run_concurrent C_concurrent_mpi "${mpi_exe}" "${ranks}" --overlap --exact

# Test D: same with GPUs, if this allocation has any.
if [ -n "${SLURM_GPUS_ON_NODE:-}" ] || command -v nvidia-smi >/dev/null 2>&1 \
        || command -v rocm-smi >/dev/null 2>&1; then
    run_concurrent D_concurrent_mpi_gpu "${mpi_exe}" "${ranks}" \
        --overlap --exact --gpus-per-task=1
else
    echo "--- D_concurrent_mpi_gpu: skipped (no GPUs detected)"
fi

echo
echo "=== summary ==="
python3 "${here}/summarize.py" "${outdir}"
