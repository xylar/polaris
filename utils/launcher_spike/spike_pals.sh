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
core_spec="${SPIKE_CORE_LIST:-0-$(( $(nproc) - 1 ))}"

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
echo "cores needed:   $(( slots * ranks * cpus )) of usable set ${core_spec}"
echo "PALS_PMI:       ${PALS_PMI}"
echo "payload sleep:  ${sleep_seconds}s"
echo "results:        ${outdir}"
echo

# Write the run metadata before anything that can fail, so that an aborted
# run is still recordable.  The MPI payload and polaris env are appended
# once they are known.
{
    echo "machine=${POLARIS_MACHINE:-${LMOD_SYSTEM_NAME:-$(hostname -s)}}"
    echo "scheduler=pbs"
    echo "scheduler_version=$(mpiexec --version 2>&1 | head -1 || echo unknown)"
    echo "job_id=${PBS_JOBID:-unknown}"
    echo "hostname=$(hostname -f)"
    echo "nodes=${nnodes}"
    echo "target_node=${node}"
    echo "cores_on_node=$(nproc 2>/dev/null || echo unknown)"
    echo "core_list=${core_spec}"
    echo "pals_pmi=${PALS_PMI}"
    echo "slots=${slots}"
    echo "ranks=${ranks}"
    echo "cpus=${cpus}"
    echo "sleep=${sleep_seconds}"
    echo "seq_n=${seq_n}"
    echo "started=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "status=incomplete"
} > "${outdir}/meta.kv"


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
    root="$(git -C "${here}" rev-parse --show-toplevel 2>/dev/null || true)"
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
    done < <(git -C "${here}" worktree list --porcelain 2>/dev/null \
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



elapsed () {
    awk -v a="$1" -v b="$2" 'BEGIN { printf "%.2f", b - a }'
}

# Usable cores, as a spec like "1-48,53-100".  Aurora reserves core 0 and
# cores 49-52, so the usable set is not contiguous and cannot be assumed to
# start at 0 -- see the cpu_bind list in mache's aurora.cfg.
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

# Cores for one slot, as a PALS --cpu-bind list: each rank gets a contiguous
# chunk of `cpus` usable cores, and slots never share a core.
cpu_bind_list () {
    local slot="$1"
    local base=$(( (slot - 1) * ranks * cpus ))
    local out="" entry="" r c idx
    for ((r = 0; r < ranks; r++)); do
        entry=""
        for ((c = 0; c < cpus; c++)); do
            idx=$(( base + r * cpus + c ))
            if [ "${idx}" -ge "${#cores_avail[@]}" ]; then
                echo "ERROR: slots x ranks x cpus needs $(( slots * ranks * cpus ))" \
                     "cores but SPIKE_CORE_LIST has ${#cores_avail[@]}" >&2
                return 1
            fi
            if [ -z "${entry}" ]; then
                entry="${cores_avail[idx]}"
            else
                entry="${entry},${cores_avail[idx]}"
            fi
        done
        if [ -z "${out}" ]; then
            out="${entry}"
        else
            out="${out}:${entry}"
        fi
    done
    printf 'list:%s' "${out}"
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
if [ "${SPIKE_SKIP_GPU:-0}" = "1" ]; then
    echo "--- D_concurrent_mpi_gpu: skipped (SPIKE_SKIP_GPU=1)"
else
    run_concurrent D_concurrent_mpi_gpu "${mpi_exe}" "${ranks}" gpu
fi

sed -i "s/^status=incomplete$/status=complete/" "${outdir}/meta.kv"
echo
echo "=== summary ==="
python3 "${here}/summarize.py" "${outdir}"

echo
echo "To record these results on the branch, from a LOGIN node:"
echo "  cd ${here}"
echo "  ./record_results.sh ${outdir} ${SPIKE_JOB_LOG:-<job-log>}"
