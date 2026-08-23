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
else
    placement_mode="cpu-bind-mask"
fi
# Override for exercising the other branch on a machine that would not
# normally take it.  Only useful for checking the script itself.
placement_mode="${SPIKE_PLACEMENT_MODE:-${placement_mode}}"

core_spec="${SPIKE_CORE_LIST:-0-$(( ${SLURM_CPUS_ON_NODE:-1} - 1 ))}"
mem_per_cpu="${SPIKE_MEM_PER_CPU:-1G}"

mkdir -p "${outdir}"
export SPIKE_OUTDIR="${outdir}"
export SPIKE_SLEEP="${sleep_seconds}"

# Bash reads a script incrementally rather than all at once, so editing
# these files while a job is running shifts the byte offsets underneath it
# and it resumes mid-token.  That has already cost one Chrysalis run.
# Re-exec from a snapshot instead, which also leaves every recorded run with
# a copy of the exact scripts that produced it.
if [ "${SPIKE_SNAPSHOT:-0}" != "1" ]; then
    snapshot="${outdir}/scripts"
    mkdir -p "${snapshot}"
    cp "${here}/spike_slurm.sh" "${here}/payload.sh" "${here}/mpi_payload.c" \
       "${here}/summarize.py" "${snapshot}/"
    chmod +x "${snapshot}/spike_slurm.sh" "${snapshot}/payload.sh" \
        "${snapshot}/summarize.py"
    export SPIKE_SNAPSHOT=1
    # git discovery for the load script has to keep pointing at the real
    # worktree, not at the snapshot inside the results directory.
    export SPIKE_REPO="${here}"
    exec "${snapshot}/spike_slurm.sh"
fi

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
# Deploying the spike worktree on every machine just to get a compiler is a
# lot of build time for a throwaway branch, and a load script generated in
# any worktree on this machine describes the same machine.  So look in this
# worktree first, then across the repo's other worktrees, newest first.
find_load_script () {
    local root worktree
    if [ -n "${SPIKE_LOAD_SCRIPT:-}" ]; then
        printf '%s' "${SPIKE_LOAD_SCRIPT}"
        return 0
    fi
    root="$(git -C "${SPIKE_REPO:-${here}}" rev-parse --show-toplevel 2>/dev/null || true)"
    if [ -n "${root}" ]; then
        local own
        own="$(ls -1t "${root}"/load_polaris_*.sh 2>/dev/null | head -1 || true)"
        if [ -n "${own}" ]; then
            printf '%s' "${own}"
            return 0
        fi
    fi
    while read -r worktree; do
        [ -n "${worktree}" ] || continue
        local found
        found="$(ls -1t "${worktree}"/load_polaris_*.sh 2>/dev/null | head -1 || true)"
        if [ -n "${found}" ]; then
            printf '%s' "${found}"
            return 0
        fi
    done < <(git -C "${SPIKE_REPO:-${here}}" worktree list --porcelain 2>/dev/null \
             | awk '/^worktree /{print $2}')
    return 1
}

load_polaris_env () {
    local script prev
    script="$(find_load_script || true)"
    if [ -z "${script}" ] || [ ! -f "${script}" ]; then
        echo "ERROR: no load_polaris_*.sh found in this worktree or any" >&2
        echo "  sibling worktree of this repo.  The spike needs the real" >&2
        echo "  Polaris environment for a compiler, an MPI and" >&2
        echo "  POLARIS_MACHINE.  Deploy any worktree on this machine with" >&2
        echo "  ./deploy.py, or set SPIKE_LOAD_SCRIPT to a load script." >&2
        echo "  Set SPIKE_NO_ENV=1 to run anyway (MPI tests degraded)." >&2
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
    # The load script exports its own path; record what was actually used.
    echo "polaris_env=${POLARIS_LOAD_SCRIPT:-${SPIKE_LOAD_SCRIPT:-unknown}}" \
        >> "${outdir}/meta.kv"
fi

# The Polaris environment on GPU machines sets MPICH_GPU_SUPPORT_ENABLED=1,
# which makes Cray MPICH abort unless the binary is linked against the GTL
# library.  The spike payload deliberately does no GPU work -- it only
# reports where it landed -- so turn GPU-aware MPI off for it rather than
# linking a library we have no use for.  Test D still requests GPUs through
# the launcher; this only affects what MPICH expects of the binary.
export MPICH_GPU_SUPPORT_ENABLED=0

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

gpu_var="CUDA_VISIBLE_DEVICES"
if command -v rocm-smi >/dev/null 2>&1; then
    gpu_var="ROCR_VISIBLE_DEVICES"
fi

# Contiguous share of the node's GPUs for one slot, as a comma list.
gpus_for_slot () {
    local slot="$1"
    awk -v slot="${slot}" -v slots="${slots}" \
        -v total="${SLURM_GPUS_ON_NODE:-0}" 'BEGIN {
        per = int(total / slots)
        if (per < 1) { per = 1 }
        out = ""
        for (i = 0; i < per; i++) {
            gpu = ((slot - 1) * per + i) % (total > 0 ? total : 1)
            out = (i == 0) ? gpu : out "," gpu
        }
        printf "%s", out
    }'
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
    local label="${extra[*]:-(no extra flags)}"
    case "${use_mask}" in
        mask)
            label="${extra[*]:-} --cpu-bind=mask_cpu:<per slot>"
            ;;
        mask+gpu)
            label="${extra[*]:-} --cpu-bind=mask_cpu:<per slot>"
            label="${label} ${gpu_var}=<per slot>"
            ;;
    esac
    echo "--- ${test_name}: ${slots} concurrent launches x ${nranks} rank(s)" \
         "${label}"
    mkdir -p "${outdir}/${test_name}"
    local pids=()
    for slot in $(seq 1 "${slots}"); do
        (
            export SPIKE_TEST="${test_name}"
            export SPIKE_SLOT="${slot}"
            local -a slot_flags=("${extra[@]}")
            case "${use_mask}" in
                mask|mask+gpu)
                    local mask
                    mask="$(mask_cpu_for_slot "${slot}")" || exit 1
                    slot_flags+=("--cpu-bind=${mask}")
                    ;;
            esac
            if [ "${use_mask}" = "mask+gpu" ]; then
                # Aurora gets disjoint GPUs by setting the visible-device
                # variable per slot rather than asking the launcher for a
                # share, so try the same thing here.
                export "${gpu_var}=$(gpus_for_slot "${slot}")"
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

has_gpu=0
if [ "${SPIKE_SKIP_GPU:-0}" != "1" ] \
        && { [ -n "${SLURM_GPUS_ON_NODE:-}" ] \
             || command -v nvidia-smi >/dev/null 2>&1 \
             || command -v rocm-smi >/dev/null 2>&1; }; then
    has_gpu=1
fi

run_sequential A_sequential_plain

if [ "${placement_mode}" = "overlap-exact" ]; then
    run_sequential A_sequential_exact --exact

    # Anchors: both of these already serialize on Frontier and pm-gpu.
    run_concurrent B_concurrent_plain "${here}/payload.sh" 1 nomask
    run_concurrent B_concurrent_exact "${here}/payload.sh" 1 nomask --exact

    # --exact bounds a step's CPUs but says nothing about its memory, and a
    # step that does not name a memory figure can take the job's whole
    # allocation, which would make every later step wait.  pm-cpu, the one
    # machine where plain steps do run concurrently, may simply have a
    # different DefMemPerCPU.
    run_concurrent B_exact_mem "${here}/payload.sh" 1 nomask \
        --exact "--mem-per-cpu=${mem_per_cpu}"

    if [ "${has_gpu}" = "1" ]; then
        # --gpus-per-task=1 did not partition anything on Frontier.  Ask for
        # no GRES at all instead: if these CPU-only steps then run
        # concurrently, the GPU claim is what serializes them.
        run_concurrent B_exact_gres_none "${here}/payload.sh" 1 nomask \
            --exact --gres=none

        # pm-gpu proved the GPU claim is what serializes: --gres=none runs
        # four concurrent steps with disjoint cores.  So a step that really
        # wants a GPU has to name its share.  --gpus-per-task=1 did not work
        # on Frontier, so try a per-step total as well and compare.
        run_concurrent B_exact_gpus1 "${here}/payload.sh" 1 nomask \
            --exact --gpus=1
        run_concurrent D_exact_gpus_per_task "${mpi_exe}" "${ranks}" nomask \
            --exact --gpus-per-task=1
        run_concurrent D_exact_gpus_total "${mpi_exe}" "${ranks}" nomask \
            --exact "--gpus=${ranks}"
    fi

    # Control: concurrency, but sharing everything.
    run_concurrent B_overlap_control "${here}/payload.sh" 1 nomask \
        --overlap --exact

    # The combination we have not tried.  --overlap is what actually buys
    # concurrency here, and Chrysalis showed --cpu-bind=mask_cpu enforces
    # placement on its own, so let the launcher stop policing resources and
    # police them ourselves.
    run_concurrent B_overlap_mask "${here}/payload.sh" 1 mask \
        --overlap
    run_concurrent C_overlap_mask "${mpi_exe}" "${ranks}" mask \
        --overlap

    run_concurrent C_concurrent_mpi_exact "${mpi_exe}" "${ranks}" nomask --exact
    gpu_mode="mask+gpu"
    gpu_flags=(--overlap)
else
    # Pre-20.11: --overlap and --exact do not exist, steps already share a
    # node by default, and the only way to get disjoint cores is an explicit
    # mask.  B_concurrent_plain is expected to show every slot seeing every
    # core; B_concurrent_mask is the one that matters.
    run_concurrent B_concurrent_plain "${here}/payload.sh" 1 nomask
    run_concurrent B_concurrent_mask "${here}/payload.sh" 1 mask
    run_concurrent C_concurrent_mpi_mask "${mpi_exe}" "${ranks}" mask
    gpu_mode="mask+gpu"
    gpu_flags=()
fi

if [ "${SPIKE_SKIP_GPU:-0}" = "1" ]; then
    echo "--- D_concurrent_mpi_gpu: skipped (SPIKE_SKIP_GPU=1)"
elif [ "${has_gpu}" = "1" ]; then
    run_concurrent D_overlap_mask_gpu "${mpi_exe}" "${ranks}" "${gpu_mode}" \
        "${gpu_flags[@]}"
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
