#!/usr/bin/env bash
# Driver for the mache placement check.
#
# Sources the Polaris environment, builds the MPI payload, and runs
# check_placement.py.  Run it inside an allocation, e.g. through one of the
# job scripts in this directory, or by hand:
#
#   salloc -N 1 -t 00:30:00 ...
#   ./run_check.sh

set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

slots="${PLACE_SLOTS:-4}"
ntasks="${PLACE_NTASKS:-2}"
cpus="${PLACE_CPUS:-4}"
sleep_seconds="${PLACE_SLEEP:-15}"
outdir="${PLACE_OUTDIR:-${PWD}/placement_results_$(date +%Y%m%d_%H%M%S)}"

mkdir -p "${outdir}"

# Bash reads a script incrementally rather than all at once, so editing these
# files while a job is running shifts the byte offsets underneath it and it
# resumes mid-token.  That has already cost one run on Chrysalis.  Re-exec
# from a snapshot instead, which also leaves every recorded run with a copy of
# the exact scripts that produced it.
if [ "${PLACE_SNAPSHOT:-0}" != "1" ]; then
    snapshot="${outdir}/scripts"
    mkdir -p "${snapshot}"
    cp "${here}/run_check.sh" "${here}/check_placement.py" \
       "${here}/payload.sh" "${here}/mpi_payload.c" \
       "${here}/summarize.py" "${snapshot}/"
    chmod +x "${snapshot}/run_check.sh" "${snapshot}/payload.sh" \
        "${snapshot}/check_placement.py" "${snapshot}/summarize.py"
    export PLACE_SNAPSHOT=1
    export PLACE_OUTDIR="${outdir}"
    # git discovery for the load script has to keep pointing at the real
    # worktree, not at the snapshot inside the results directory
    export PLACE_REPO="${here}"
    exec "${snapshot}/run_check.sh"
fi

repo_root="$(git -C "${PLACE_REPO:-${here}}" rev-parse --show-toplevel)"

# The compiler and MPI for a Polaris machine come from mache.deploy, via the
# load_polaris_<machine>_<compiler>_<mpi>.sh script that ./deploy.py writes in
# the worktree.  Never hand-pick modules here.
#
# Unlike the earlier launcher spike, this check cannot borrow a sibling
# worktree's load script: it needs the mache branch that adds placement, so it
# has to be the environment deployed for *this* worktree.
load_script="${PLACE_LOAD_SCRIPT:-}"
if [ -z "${load_script}" ]; then
    load_script="$(ls -1t "${repo_root}"/load_polaris_*.sh 2>/dev/null \
                   | head -1 || true)"
fi
if [ -z "${load_script}" ] || [ ! -f "${load_script}" ]; then
    echo "ERROR: no load_polaris_*.sh in ${repo_root}." >&2
    echo "  Deploy this worktree against the mache branch that adds" >&2
    echo "  placement:" >&2
    echo "    ./deploy.py --mache-fork xylar/mache \\" >&2
    echo "                --mache-branch parallel-placement" >&2
    exit 1
fi

# The load script verifies that the polaris it can import matches the
# deployed version, so it has to be sourced from its own worktree.  Neither
# it nor the activation hooks it runs are written to be safe under `set -u`.
prev="${PWD}"
cd "$(dirname "${load_script}")" || exit 1
set +u
# shellcheck disable=SC1090
source "${load_script}"
set -u
cd "${prev}" || exit 1

# A released mache has no ResourcePlacement, and the whole point of this
# check is what mache renders, so stop here rather than fail obscurely later.
if ! python -c 'from mache.parallel import ResourcePlacement' 2>/dev/null; then
    echo "ERROR: the deployed mache has no ResourcePlacement, so it" >&2
    echo "  predates the placement work this check exists to verify." >&2
    echo "  Redeploy with:" >&2
    echo "    ./deploy.py --mache-fork xylar/mache \\" >&2
    echo "                --mache-branch parallel-placement" >&2
    exit 1
fi

echo "polaris env:    ${POLARIS_LOAD_SCRIPT:-${load_script}}"

# The Polaris environment on GPU machines sets MPICH_GPU_SUPPORT_ENABLED=1,
# which makes Cray MPICH abort unless the binary is linked against the GTL
# library.  This payload deliberately does no GPU work -- it only reports
# where it landed -- so turn GPU-aware MPI off for it rather than linking a
# library we have no use for.  The GPU checks still request GPUs through the
# launcher; this only affects what MPICH expects of the binary.
export MPICH_GPU_SUPPORT_ENABLED=0

# Cray machines (Perlmutter, Frontier) wrap MPI in `cc`, not `mpicc`.
payload="${here}/payload.sh"
payload_kind="shell-fallback"
for candidate in ${PLACE_MPICC:-} mpicc cc; do
    command -v "${candidate}" >/dev/null 2>&1 || continue
    if "${candidate}" -O0 -o "${outdir}/mpi_payload" \
            "${here}/mpi_payload.c" >>"${outdir}/mpicc.log" 2>&1; then
        payload="${outdir}/mpi_payload"
        payload_kind="${candidate}"
        break
    fi
    echo "WARNING: ${candidate} failed, see ${outdir}/mpicc.log" >&2
done

# A shell payload downgrades the concurrent checks from "does an MPI job
# survive concurrency" to "does step creation survive concurrency", so refuse
# to run that way unless it is asked for explicitly.
if [ "${payload_kind}" = "shell-fallback" ] \
        && [ "${PLACE_ALLOW_FALLBACK:-0}" != "1" ]; then
    echo "ERROR: could not build the MPI payload; see ${outdir}/mpicc.log" >&2
    echo "  Rerun with PLACE_ALLOW_FALLBACK=1 to accept the weaker" >&2
    echo "  result, or check that the load script provided a compiler." >&2
    exit 1
fi
echo "mpi payload:    ${payload_kind} (${payload})"
echo

python "${here}/check_placement.py" \
    --outdir "${outdir}" \
    --payload "${payload}" \
    --slots "${slots}" \
    --ntasks "${ntasks}" \
    --cpus-per-task "${cpus}" \
    --sleep "${sleep_seconds}" \
    ${PLACE_CORE_LIST:+--core-list "${PLACE_CORE_LIST}"} \
    ${PLACE_SKIP_GPU:+--skip-gpu} \
    ${PLACE_DRY_RUN:+--dry-run}
status=$?

echo
echo "=== summary ==="
python "${here}/summarize.py" "${outdir}" | tee "${outdir}/summary.txt"

echo
echo "To record these results on the branch, from a LOGIN node:"
echo "  cd ${repo_root}/utils/placement_check"
echo "  ./record_results.sh ${outdir} ${PLACE_JOB_LOG:-<job-log>}"

exit ${status}
