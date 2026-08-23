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

# --overlap and --exact were added in Slurm 20.11, along with the change that
# made job steps exclusive by default.  Older sites (Chrysalis runs 20.02)
# already let steps share a node, but reject those flags outright, so the
# spike has to ask for concurrency and placement differently there.
slurm_version="$(srun --version 2>/dev/null | awk '{print $2}')"
slurm_major="${slurm_version%%.*}"
slurm_rest="${slurm_version#*.}"
slurm_minor="${slurm_rest%%.*}"
if [ "${slurm_major:-0}" -gt 20 ] 2>/dev/null || {
        [ "${slurm_major:-0}" -eq 20 ] && [ "${slurm_minor:-0}" -ge 11 ]
   } 2>/dev/null; then
    placement_mode="overlap-exact"
    overlap_flags=(--overlap --exact)
else
    placement_mode="cpu-bind-mask"
    overlap_flags=()
fi

core_spec="${SPIKE_CORE_LIST:-0-$(( ${SLURM_CPUS_ON_NODE:-1} - 1 ))}"

mkdir -p "${outdir}"
export SPIKE_OUTDIR="${outdir}"
export SPIKE_SLEEP="${sleep_seconds}"

echo "=== Polaris launcher spike (Slurm) ==="
echo "slurm version:  ${slurm_version:-unknown}"
echo "placement:      ${placement_mode}"
echo "job id:         ${SLURM_JOB_ID}"
echo "nodes:          ${nnodes} (${SLURM_JOB_NODELIST})"
echo "target node:    ${node}"
echo "cores on node:  ${SLURM_CPUS_ON_NODE:-unknown}"
echo "slots:          ${slots} x ${cpus} cpus"
echo "mpi ranks:      ${ranks} per launch"
echo "payload sleep:  ${sleep_seconds}s"
echo "results:        ${outdir}"
echo

# Write the run metadata before anything that can fail, so that an aborted
# run is still recordable.  The MPI payload and polaris env are appended
# once they are known.
{
    echo "machine=${POLARIS_MACHINE:-${LMOD_SYSTEM_NAME:-$(hostname -s)}}"
    echo "scheduler=slurm"
    echo "scheduler_version=${slurm_version:-unknown}"
    echo "placement_mode=${placement_mode}"
    echo "job_id=${SLURM_JOB_ID}"
    echo "hostname=$(hostname -f)"
    echo "nodes=${nnodes}"
    echo "nodelist=${SLURM_JOB_NODELIST}"
    echo "target_node=${node}"
    echo "cores_on_node=${SLURM_CPUS_ON_NODE:-unknown}"
    echo "gpus_on_node=${SLURM_GPUS_ON_NODE:-0}"
    echo "core_list=${core_spec}"
    echo "slots=${slots}"
    echo "ranks=${ranks}"
    echo "cpus=${cpus}"
    echo "sleep=${sleep_seconds}"
    echo "seq_n=${seq_n}"
    echo "started=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "status=incomplete"
} > "${outdir}/meta.kv"


# Build the MPI payload if we can; otherwise fall back to the shell payload
# launched at MPI width (still exercises step creation and placement, but
# not PMI bootstrap).
# The compiler and MPI for a Polaris machine come from mache.deploy, via the
# load_polaris_<machine>_<compiler>_<mpi>.sh script that `./deploy.py`
# generates in the worktree.  Never hand-pick modules here: that script is
# the only source of truth for what a machine is supposed to provide, and it
# also exports POLARIS_MACHINE, which labels the recorded results.
load_polaris_env () {
    local root script prev
    root="$(git -C "${here}" rev-parse --show-toplevel 2>/dev/null || true)"
    script="${SPIKE_LOAD_SCRIPT:-}"
    if [ -z "${script}" ] && [ -n "${root}" ]; then
        script="$(ls -1 "${root}"/load_polaris_*.sh 2>/dev/null | head -1 || true)"
    fi
    if [ -z "${script}" ] || [ ! -f "${script}" ]; then
        echo "ERROR: no load_polaris_*.sh found in ${root:-<not a git repo>}." >&2
        echo "  The spike needs the real Polaris environment for a compiler," >&2
        echo "  an MPI and POLARIS_MACHINE.  Run ./deploy.py in this worktree," >&2
        echo "  or point SPIKE_LOAD_SCRIPT at another worktree's load script." >&2
        echo "  Set SPIKE_NO_ENV=1 to run anyway (MPI tests will be degraded)." >&2
        return 1
    fi
    # The load script verifies that the polaris it can import matches the
    # deployed version, so it has to be sourced from its own worktree.
    prev="${PWD}"
    cd "$(dirname "${script}")" || return 1
    # Neither the generated load script nor the conda activate.d hooks it
    # runs are written to be safe under `set -u`, so relax it while sourcing.
    set +u
    # shellcheck disable=SC1090
    source "${script}"
    set -u
    cd "${prev}" || return 1
    echo "polaris env:    ${script}"
}

if [ "${SPIKE_NO_ENV:-0}" != "1" ]; then
    load_polaris_env || exit 1
    # POLARIS_MACHINE only exists once the load script has been sourced, and
    # meta.kv is deliberately written before that so aborted runs are still
    # recordable.  Correct the label now that the real name is known.
    if [ -n "${POLARIS_MACHINE:-}" ]; then
        sed -i "s|^machine=.*|machine=${POLARIS_MACHINE}|" "${outdir}/meta.kv"
    fi
    echo "polaris_env=${SPIKE_LOAD_SCRIPT:-auto}" >> "${outdir}/meta.kv"
fi

# Cray machines (Perlmutter, Frontier) wrap MPI in `cc`, not `mpicc`.
mpi_exe="${here}/payload.sh"
mpi_kind="shell-fallback"
for candidate in ${SPIKE_MPICC:-} mpicc cc; do
    command -v "${candidate}" >/dev/null 2>&1 || continue
    if "${candidate}" -O0 -o "${outdir}/mpi_payload" \
            "${here}/mpi_payload.c" >>"${outdir}/mpicc.log" 2>&1; then
        mpi_exe="${outdir}/mpi_payload"
        mpi_kind="${candidate}"
        break
    fi
    echo "WARNING: ${candidate} failed, see ${outdir}/mpicc.log" >&2
done

# A shell fallback silently downgrades tests C and D from "does PMI bootstrap
# survive concurrency" to "does step creation survive concurrency", so refuse
# to run that way unless it is asked for explicitly.
if [ "${mpi_kind}" = "shell-fallback" ] \
        && [ "${SPIKE_ALLOW_FALLBACK:-0}" != "1" ]; then
    echo "ERROR: could not build the MPI payload; see ${outdir}/mpicc.log" >&2
    echo "  Tests C and D would not exercise MPI at all.  Check that the" >&2
    echo "  polaris load script provided a compiler and MPI, or rerun with" >&2
    echo "  SPIKE_ALLOW_FALLBACK=1 to accept the weaker result." >&2
    exit 1
fi
echo "mpi payload:    ${mpi_kind} (${mpi_exe})"
echo "mpi_payload=${mpi_kind}" >> "${outdir}/meta.kv"
echo



parse_core_list () {
    local spec="$1"
    local -a out=()
    local chunk lo hi core
    local IFS=','
    for chunk in ${spec}; do
        if [[ "${chunk}" == *-* ]]; then
            lo="${chunk%%-*}"
            hi="${chunk##*-}"
            for ((core = lo; core <= hi; core++)); do
                out+=("${core}")
            done
        else
            out+=("${chunk}")
        fi
    done
    printf '%s\n' "${out[@]}"
}

mapfile -t cores_avail < <(parse_core_list "${core_spec}")

# Without --exact, a pre-20.11 job step takes every CPU on the node, so the
# only way to give concurrent steps disjoint cores is an explicit mask.
# Core numbers can exceed 64, so build the masks in Python rather than in
# bash arithmetic.
mask_cpu_for_slot () {
    local slot="$1"
    python3 -c '
import sys
slot, ranks, cpus = (int(v) for v in sys.argv[1:4])
cores = [int(c) for c in sys.argv[4].split(",") if c]
base = (slot - 1) * ranks * cpus
need = base + ranks * cpus
if need > len(cores):
    sys.exit(f"need {need} cores, core list has {len(cores)}")
masks = []
for r in range(ranks):
    mask = 0
    for c in range(cpus):
        mask |= 1 << cores[base + r * cpus + c]
    masks.append(hex(mask))
print("mask_cpu:" + ",".join(masks))
' "${slot}" "${ranks}" "${cpus}" "$(IFS=,; echo "${cores_avail[*]}")"
}

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

# use_mask="mask" adds a per-slot --cpu-bind=mask_cpu:, which is how a
# pre-20.11 site has to express placement.
run_concurrent () {
    local test_name="$1"; shift
    local exe="$1"; shift
    local nranks="$1"; shift
    local use_mask="$1"; shift
    local -a extra=("$@")
    echo "--- ${test_name}: ${slots} concurrent launches x ${nranks} rank(s)" \
         "${extra[*]:-(no extra flags)}"
    mkdir -p "${outdir}/${test_name}"
    local pids=()
    for slot in $(seq 1 "${slots}"); do
        (
            export SPIKE_TEST="${test_name}"
            export SPIKE_SLOT="${slot}"
            local -a slot_flags=("${extra[@]}")
            if [ "${use_mask}" = "mask" ]; then
                local mask
                mask="$(mask_cpu_for_slot "${slot}")" || exit 1
                slot_flags+=("--cpu-bind=${mask}")
            fi
            timeout "${launch_timeout}" srun "${slot_flags[@]}" \
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

# Round 1 on Perlmutter (Slurm 25.11) showed that --overlap is the wrong
# flag: without it, concurrent steps already run and Slurm hands each one
# disjoint cores, while --overlap explicitly lets steps share CPUs and
# reintroduces collisions.  So the plain and --exact variants are the
# candidates now, and --overlap is kept only as a control.

run_sequential A_sequential_plain

if [ "${placement_mode}" = "overlap-exact" ]; then
    run_sequential A_sequential_exact --exact

    run_concurrent B_concurrent_plain "${here}/payload.sh" 1 nomask
    run_concurrent B_concurrent_exact "${here}/payload.sh" 1 nomask --exact
    # Control: expected to show core collisions.  If it does not, the
    # meaning of --overlap on this site is not what we think it is.
    run_concurrent B_overlap_control "${here}/payload.sh" 1 nomask \
        --overlap --exact

    run_concurrent C_concurrent_mpi_plain "${mpi_exe}" "${ranks}" nomask
    run_concurrent C_concurrent_mpi_exact "${mpi_exe}" "${ranks}" nomask --exact
    gpu_flags=(--exact --gpus-per-task=1)
else
    # Pre-20.11: --overlap and --exact do not exist, steps already share a
    # node by default, and the only way to get disjoint cores is an explicit
    # mask.  B_concurrent_plain is expected to show every slot seeing every
    # core; B_concurrent_mask is the one that matters.
    run_concurrent B_concurrent_plain "${here}/payload.sh" 1 nomask
    run_concurrent B_concurrent_mask "${here}/payload.sh" 1 mask
    run_concurrent C_concurrent_mpi_mask "${mpi_exe}" "${ranks}" mask
    gpu_flags=(--gpus-per-task=1)
fi

if [ "${SPIKE_SKIP_GPU:-0}" = "1" ]; then
    echo "--- D_concurrent_mpi_gpu: skipped (SPIKE_SKIP_GPU=1)"
elif [ -n "${SLURM_GPUS_ON_NODE:-}" ] || command -v nvidia-smi >/dev/null 2>&1 \
        || command -v rocm-smi >/dev/null 2>&1; then
    if [ "${placement_mode}" = "overlap-exact" ]; then
        run_concurrent D_concurrent_mpi_gpu "${mpi_exe}" "${ranks}" nomask \
            "${gpu_flags[@]}"
    else
        run_concurrent D_concurrent_mpi_gpu "${mpi_exe}" "${ranks}" mask \
            "${gpu_flags[@]}"
    fi
else
    echo "--- D_concurrent_mpi_gpu: skipped (no GPUs detected)"
fi

sed -i "s/^status=incomplete$/status=complete/" "${outdir}/meta.kv"
echo
echo "=== summary ==="
python3 "${here}/summarize.py" "${outdir}"

echo
echo "To record these results on the branch, from a LOGIN node:"
echo "  cd ${here}"
echo "  ./record_results.sh ${outdir} ${SPIKE_JOB_LOG:-<job-log>}"
