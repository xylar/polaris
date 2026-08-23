#!/usr/bin/env bash
# Launcher spike for PBS/PALS machines (Aurora).
#
# ALCF documents concurrent mpiexec within one PBS job as supported, so the
# open questions here are narrower than on Slurm: does explicit placement
# (--hosts + --cpu-bind list:) actually give disjoint cores, does PMI
# bootstrap survive several simultaneous launches, and what does a launch
# cost?
#
# Run inside a PBS job, e.g.
#   qsub -I -l select=2 -l walltime=00:30:00 -A <acct> -q debug
#   ./spike_pals.sh

set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

slots="${SPIKE_SLOTS:-4}"
cpus="${SPIKE_CPUS:-8}"
ranks="${SPIKE_RANKS:-4}"
sleep_seconds="${SPIKE_SLEEP:-15}"
seq_n="${SPIKE_SEQ_N:-10}"
outdir="${SPIKE_OUTDIR:-${PWD}/spike_results_$(date +%Y%m%d_%H%M%S)}"
launch_timeout="${SPIKE_TIMEOUT:-$((sleep_seconds + 120))}"

if [ -z "${PBS_NODEFILE:-}" ]; then
    echo "ERROR: run this inside a PBS job (PBS_NODEFILE is unset)." >&2
    exit 1
fi

mapfile -t nodes < <(sort -u "${PBS_NODEFILE}")
node="${SPIKE_NODE:-${nodes[0]}}"
nnodes="${#nodes[@]}"

mkdir -p "${outdir}"
export SPIKE_OUTDIR="${outdir}"
export SPIKE_SLEEP="${sleep_seconds}"
# Required on Aurora so ranks can query the runtime for job information.
export PALS_PMI="${PALS_PMI:-pmix}"

echo "=== Polaris launcher spike (PBS/PALS) ==="
echo "job id:         ${PBS_JOBID:-unknown}"
echo "nodes:          ${nnodes}"
echo "target node:    ${node}"
echo "cores on node:  $(nproc 2>/dev/null || echo unknown)"
echo "slots:          ${slots} x ${ranks} rank(s) x ${cpus} cpus"
echo "PALS_PMI:       ${PALS_PMI}"
echo "payload sleep:  ${sleep_seconds}s"
echo "results:        ${outdir}"
echo

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

# Cores for one slot, as a PALS --cpu-bind list: each rank gets a contiguous
# chunk of `cpus` cores, and slots never share a core.
cpu_bind_list () {
    local slot="$1"
    awk -v slot="${slot}" -v ranks="${ranks}" -v cpus="${cpus}" 'BEGIN {
        base = (slot - 1) * ranks * cpus
        out = ""
        for (r = 0; r < ranks; r++) {
            entry = ""
            for (c = 0; c < cpus; c++) {
                core = base + r * cpus + c
                entry = (c == 0) ? core : entry "," core
            }
            out = (r == 0) ? entry : out ":" entry
        }
        printf "list:%s", out
    }'
}

# Aurora has 6 GPUs x 2 tiles per node; give each slot its own tile.
affinity_mask () {
    local slot="$1"
    awk -v slot="${slot}" 'BEGIN { printf "%d.%d", int((slot - 1) / 2), (slot - 1) % 2 }'
}

echo "--- A_sequential: ${seq_n} sequential launches"
mkdir -p "${outdir}/A_sequential"
t0="$(date +%s.%N)"
for i in $(seq 1 "${seq_n}"); do
    s0="$(date +%s.%N)"
    timeout "${launch_timeout}" mpiexec -n 1 --ppn 1 --hosts "${node}" true \
        >>"${outdir}/A_sequential/launches.out" \
        2>>"${outdir}/A_sequential/launches.err"
    rc=$?
    s1="$(date +%s.%N)"
    echo "launch=${i} rc=${rc} seconds=$(elapsed "${s0}" "${s1}")" \
        >> "${outdir}/A_sequential/timings.kv"
done
t1="$(date +%s.%N)"
echo "    total $(elapsed "${t0}" "${t1}")s for ${seq_n} launches"

run_concurrent () {
    local test_name="$1"; shift
    local exe="$1"; shift
    local nranks="$1"; shift
    local use_gpu="$1"; shift
    echo "--- ${test_name}: ${slots} concurrent launches x ${nranks} rank(s)"
    mkdir -p "${outdir}/${test_name}"
    local pids=()
    for slot in $(seq 1 "${slots}"); do
        (
            export SPIKE_TEST="${test_name}"
            export SPIKE_SLOT="${slot}"
            if [ "${use_gpu}" = "gpu" ]; then
                export ZE_ENABLE_PCI_ID_DEVICE_ORDER=1
                export ZE_AFFINITY_MASK="$(affinity_mask "${slot}")"
            fi
            timeout "${launch_timeout}" mpiexec -n "${nranks}" \
                --ppn "${nranks}" --hosts "${node}" \
                --cpu-bind "$(cpu_bind_list "${slot}")" "${exe}" \
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

run_concurrent B_concurrent_single "${here}/payload.sh" 1 nogpu
run_concurrent C_concurrent_mpi "${mpi_exe}" "${ranks}" nogpu
run_concurrent D_concurrent_mpi_gpu "${mpi_exe}" "${ranks}" gpu

echo
echo "=== summary ==="
python3 "${here}/summarize.py" "${outdir}"
