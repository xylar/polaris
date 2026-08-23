#!/usr/bin/env bash
# Record a spike run back onto the add-launcher-spike branch so results from
# every machine collect in one place.
#
#   ./record_results.sh [results-dir] [job-log]
#
# With no arguments it picks the newest spike_results_* directory in the
# current directory.  Pass the job stdout file as a second argument to
# capture it too (e.g. launcher_spike.o123456).
#
# Results land in utils/launcher_spike/results/<machine>/<jobid>/ and are
# committed and pushed.  Use --no-push to commit only.

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(git -C "${here}" rev-parse --show-toplevel)"
branch="${SPIKE_BRANCH:-add-launcher-spike}"
# On other machines the fork is probably not called "xylar", so prefer the
# remote this branch already tracks.
tracked_remote="$(git -C "${here}" config --get "branch.${branch}.remote" \
    2>/dev/null || true)"
remote="${SPIKE_REMOTE:-${tracked_remote:-origin}}"

push=1
args=()
for arg in "$@"; do
    if [ "${arg}" = "--no-push" ]; then
        push=0
    else
        args+=("${arg}")
    fi
done

results="${args[0]:-}"
job_log="${args[1]:-}"

if [ -z "${results}" ]; then
    results="$(ls -1dt spike_results_* 2>/dev/null | head -1 || true)"
    if [ -z "${results}" ]; then
        echo "ERROR: no results directory given and no spike_results_* found" >&2
        exit 1
    fi
    echo "using newest results directory: ${results}"
fi
results="$(cd "${results}" && pwd)"

# A run that aborted early is exactly the one worth recording, so fall back
# to whatever can be worked out rather than refusing.
machine=""
job_id=""
status="unknown"
if [ -f "${results}/meta.kv" ]; then
    machine="$(awk -F= '/^machine=/ {print $2}' "${results}/meta.kv")"
    job_id="$(awk -F= '/^job_id=/ {print $2}' "${results}/meta.kv")"
    status="$(awk -F= '/^status=/ {print $2}' "${results}/meta.kv")"
else
    echo "WARNING: ${results}/meta.kv not found; the run probably aborted" >&2
    echo "  before it could write one.  Recording what is there anyway." >&2
    machine="${POLARIS_MACHINE:-${LMOD_SYSTEM_NAME:-}}"
    # spike_results_<jobid> is the layout the job scripts use
    case "$(basename "${results}")" in
        spike_results_*) job_id="$(basename "${results}")"
                         job_id="${job_id#spike_results_}" ;;
    esac
    status="aborted"
fi
# PBS job ids look like 12345.aurora-pbs-0001.hostmgmt.cm.aurora.alcf.anl.gov
job_id="${job_id%%.*}"
machine="${SPIKE_MACHINE:-${machine:-unknown}}"
job_id="${job_id:-$(date +%Y%m%d_%H%M%S)}"
if [ "${machine}" = "unknown" ]; then
    echo "NOTE: could not determine the machine name; recording under" >&2
    echo "  'unknown'.  Set SPIKE_MACHINE=<name> to label it properly." >&2
fi

dest="${repo}/utils/launcher_spike/results/${machine}/${job_id}"
if [ -e "${dest}" ]; then
    dest="${dest}_$(date +%H%M%S)"
fi
mkdir -p "${dest}"

# Copy everything except the compiled payload, which is a binary and is
# rebuilt on each run anyway.
(cd "${results}" && find . -type f ! -name 'mpi_payload' \
    -exec cp --parents {} "${dest}/" \;)

# An aborted run has no test output at all, so the job log is the only
# evidence of why.  Find it rather than relying on it being passed in.
if [ -z "${job_log}" ]; then
    for candidate in \
            "$(dirname "${results}")/launcher_spike.o${job_id}" \
            "${PWD}/launcher_spike.o${job_id}" \
            "${here}/launcher_spike.o${job_id}"; do
        if [ -f "${candidate}" ]; then
            job_log="${candidate}"
            break
        fi
    done
fi
if [ -n "${job_log}" ] && [ -f "${job_log}" ]; then
    cp "${job_log}" "${dest}/job.log"
    echo "captured job log: ${job_log}"
else
    echo "WARNING: no job log found for job ${job_id}; pass it as the second" >&2
    echo "  argument if you want the failure reason recorded." >&2
fi

python3 "${here}/summarize.py" "${results}" > "${dest}/summary.txt" 2>&1 || true
if [ "${status}" != "complete" ]; then
    {
        echo
        echo "RUN STATUS: ${status} -- this run did not finish; see job.log"
    } >> "${dest}/summary.txt"
fi

echo
echo "=== recorded summary ==="
cat "${dest}/summary.txt"
echo

git -C "${repo}" add "utils/launcher_spike/results/${machine}"
if git -C "${repo}" diff --cached --quiet; then
    echo "nothing new to commit"
    exit 0
fi

commit_subject="Record launcher spike results from ${machine} (job ${job_id})"
if [ "${status}" != "complete" ]; then
    commit_subject="Record ${status} launcher spike run from ${machine} (job ${job_id})"
fi
git -C "${repo}" commit -q -m "${commit_subject}" \
    -m "$(sed -n '1,12p' "${dest}/summary.txt")"
echo "committed $(git -C "${repo}" log -1 --format=%h)"

if [ "${push}" = "0" ]; then
    echo "skipping push (--no-push); run: git push ${remote} ${branch}"
    exit 0
fi

# Several machines may record around the same time, so rebase before pushing
# and retry once.
for attempt in 1 2 3; do
    if git -C "${repo}" push "${remote}" "${branch}" 2>&1 | tail -3; then
        echo "pushed to ${remote}/${branch}"
        exit 0
    fi
    echo "push failed (attempt ${attempt}); rebasing on ${remote}/${branch}"
    git -C "${repo}" fetch "${remote}" "${branch}"
    git -C "${repo}" rebase "${remote}/${branch}" || {
        echo "ERROR: rebase failed, resolve by hand then: git push ${remote} ${branch}" >&2
        exit 1
    }
done
echo "ERROR: could not push after 3 attempts" >&2
exit 1
