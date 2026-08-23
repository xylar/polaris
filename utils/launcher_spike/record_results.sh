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

if [ ! -f "${results}/meta.kv" ]; then
    echo "ERROR: ${results}/meta.kv not found -- did the spike finish?" >&2
    exit 1
fi

machine="$(awk -F= '/^machine=/ {print $2}' "${results}/meta.kv")"
job_id="$(awk -F= '/^job_id=/ {print $2}' "${results}/meta.kv")"
# PBS job ids look like 12345.aurora-pbs-0001.hostmgmt.cm.aurora.alcf.anl.gov
job_id="${job_id%%.*}"
machine="${machine:-unknown}"
job_id="${job_id:-$(date +%Y%m%d_%H%M%S)}"

dest="${repo}/utils/launcher_spike/results/${machine}/${job_id}"
if [ -e "${dest}" ]; then
    dest="${dest}_$(date +%H%M%S)"
fi
mkdir -p "${dest}"

# Copy everything except the compiled payload, which is a binary and is
# rebuilt on each run anyway.
(cd "${results}" && find . -type f ! -name 'mpi_payload' \
    -exec cp --parents {} "${dest}/" \;)

if [ -n "${job_log}" ] && [ -f "${job_log}" ]; then
    cp "${job_log}" "${dest}/job.log"
    echo "captured job log: ${job_log}"
fi

python3 "${here}/summarize.py" "${results}" > "${dest}/summary.txt" 2>&1 || true

echo
echo "=== recorded summary ==="
cat "${dest}/summary.txt"
echo

git -C "${repo}" add "utils/launcher_spike/results/${machine}"
if git -C "${repo}" diff --cached --quiet; then
    echo "nothing new to commit"
    exit 0
fi

git -C "${repo}" commit -q -m "Record launcher spike results from ${machine} (job ${job_id})" \
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
