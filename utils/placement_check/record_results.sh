#!/usr/bin/env bash
# Record a placement-check run back onto the branch, so results from every
# machine collect in one place and reach whoever is reviewing mache #470.
#
#   ./record_results.sh [results-dir] [job-log]
#
# With no arguments it picks the newest placement_results_* directory in the
# current directory.  Pass the job stdout file as a second argument to capture
# it too (e.g. placement_check.o123456).
#
# Run this from a LOGIN node: compute nodes on Frontier and Aurora have no
# outbound network, so the push has to happen outside the job.
#
# Results land in utils/placement_check/results/<machine>/<jobid>/ and are
# committed and pushed.  Use --no-push to commit only.

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(git -C "${here}" rev-parse --show-toplevel)"
branch="${PLACE_BRANCH:-add-task-parallelism-phase-a}"
# On other machines the fork is probably not called "xylar", so prefer the
# remote this branch already tracks.  There is deliberately no fallback: this
# once resolved to `origin` on a machine where the branch tracked nothing,
# and pushed a work-in-progress branch full of recorded results to the main
# project repository.  Refusing is the right answer -- the person recording
# knows which fork they meant, and this script does not.
tracked_remote="$(git -C "${here}" config --get "branch.${branch}.remote" \
    2>/dev/null || true)"
remote="${PLACE_REMOTE:-${tracked_remote}}"

# A branch that tracks nothing is the common case on the HPC clones, because
# creating a worktree and hard-resetting to a remote branch sets no upstream.
# Rather than ask every time, take the one remote that is not the main project
# -- if there is exactly one, it is not a guess.  Several, or none, and it
# still asks.
if [ -z "${remote}" ]; then
    forks=""
    fork_count=0
    first_fork=""
    for name in $(git -C "${here}" remote); do
        url="$(git -C "${here}" remote get-url "${name}" 2>/dev/null || true)"
        case "${url}" in
            ''|*E3SM-Project/polaris*) continue ;;
        esac
        forks="${forks} ${name}"
        fork_count=$((fork_count + 1))
        [ -n "${first_fork}" ] || first_fork="${name}"
    done
    # deliberately not `set -- ${forks}` to count them: that clobbers the
    # script's own positional parameters, and the usage message below then
    # echoes the fork list back at you instead of your arguments
    if [ "${fork_count}" = "1" ]; then
        remote="${first_fork}"
        echo "branch ${branch} tracks no remote here; using '${remote}',"
        echo "  the only fork this clone has. Set PLACE_REMOTE to override."
    elif [ "${fork_count}" -gt 1 ]; then
        echo "NOTE: branch ${branch} tracks no remote and this clone has" >&2
        echo "  several forks:${forks}" >&2
    fi
fi

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

if [ "${push}" = "1" ]; then
    if [ -z "${remote}" ]; then
        echo "ERROR: branch ${branch} tracks no remote here, so there is no" >&2
        echo "  way to know where these results should go." >&2
        echo "  Say which fork, e.g.:" >&2
        echo "    PLACE_REMOTE=<your-fork> ${0##*/} $*" >&2
        echo "  or pass --no-push to commit locally only." >&2
        exit 1
    fi
    remote_url="$(git -C "${here}" remote get-url "${remote}" 2>/dev/null \
        || true)"
    case "${remote_url}" in
        *E3SM-Project/polaris*)
            echo "ERROR: '${remote}' is the main project repository:" >&2
            echo "    ${remote_url}" >&2
            echo "  Recorded results belong on a fork, not there." >&2
            echo "  Set PLACE_REMOTE to your fork, or pass --no-push." >&2
            exit 1
            ;;
    esac
fi

# With no argument, work out which runs have not been recorded yet rather
# than taking the newest.  Two jobs on the same machine land in the same
# directory -- pm-cpu and pm-gpu share a filesystem, for instance -- and
# "newest" silently records one and drops the other.
if [ -z "${results}" ]; then
    pending=""
    pending_count=0
    found_any=0
    for candidate in $(ls -1dt placement_results_* 2>/dev/null || true); do
        found_any=1
        cand_machine=""
        cand_job=""
        if [ -f "${candidate}/meta.kv" ]; then
            cand_machine="$(awk -F= '/^machine=/ {print $2}' \
                "${candidate}/meta.kv")"
            cand_job="$(awk -F= '/^job_id=/ {print $2}' \
                "${candidate}/meta.kv")"
            cand_job="${cand_job%%.*}"
        fi
        if [ -n "${cand_machine}" ] && [ -n "${cand_job}" ] && [ -d \
                "${repo}/utils/placement_check/results/${cand_machine}/${cand_job}" ]; then
            continue
        fi
        pending="${pending} ${candidate}"
        pending_count=$((pending_count + 1))
    done

    if [ "${found_any}" = "0" ]; then
        echo "ERROR: no results directory given and none found" >&2
        exit 1
    fi
    if [ "${pending_count}" = "0" ]; then
        echo "every placement_results_* here is already recorded under" >&2
        echo "  utils/placement_check/results/. Name one explicitly to" >&2
        echo "  record it again." >&2
        exit 1
    fi
    if [ "${pending_count}" -gt 1 ]; then
        echo "ERROR: ${pending_count} runs here have not been recorded:" >&2
        for candidate in ${pending}; do
            echo "    ${candidate}" >&2
        done
        echo "  Recording only the newest would silently drop the rest." >&2
        echo "  Name one:  ${0##*/} <results-dir>" >&2
        exit 1
    fi
    # not `set -- ${pending}`: that clobbers this script's own arguments,
    # which has already caught me once
    results="${pending# }"
    echo "recording the one run here that has not been recorded yet:"
    echo "  ${results}"
fi
results="$(cd "${results}" && pwd)"

# A run that aborted early is exactly the one worth recording, so fall back to
# whatever can be worked out rather than refusing.
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
    case "$(basename "${results}")" in
        placement_results_*) job_id="$(basename "${results}")"
                             job_id="${job_id#placement_results_}" ;;
    esac
    status="aborted"
fi
# PBS job ids look like 12345.aurora-pbs-0001.hostmgmt.cm.aurora.alcf.anl.gov
job_id="${job_id%%.*}"
machine="${PLACE_MACHINE:-${machine:-unknown}}"
job_id="${job_id:-$(date +%Y%m%d_%H%M%S)}"
if [ "${machine}" = "unknown" ]; then
    echo "NOTE: could not determine the machine name; recording under" >&2
    echo "  'unknown'.  Set PLACE_MACHINE=<name> to label it properly." >&2
fi

dest="${repo}/utils/placement_check/results/${machine}/${job_id}"
if [ -e "${dest}" ]; then
    dest="${dest}_$(date +%H%M%S)"
fi
mkdir -p "${dest}"

# Copy everything except the compiled payload, which is a binary and is
# rebuilt on each run anyway.
(cd "${results}" && find . -type f ! -name 'mpi_payload' \
    -exec cp --parents {} "${dest}/" \;)

# An aborted run has no check output at all, so the job log is the only
# evidence of why.  Find it rather than relying on it being passed in.
if [ -z "${job_log}" ]; then
    for candidate in \
            "$(dirname "${results}")/placement_check.o${job_id}" \
            "${PWD}/placement_check.o${job_id}" \
            "${here}/placement_check.o${job_id}"; do
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
    echo "WARNING: no job log found for job ${job_id}; pass it as the" >&2
    echo "  second argument if you want the failure reason recorded." >&2
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

git -C "${repo}" add "utils/placement_check/results/${machine}"
if git -C "${repo}" diff --cached --quiet; then
    echo "nothing new to commit"
    exit 0
fi

subject="Record placement-check results from ${machine} (job ${job_id})"
if [ "${status}" != "complete" ]; then
    subject="Record ${status} placement-check run from ${machine}"
    subject="${subject} (job ${job_id})"
fi
# --no-verify because the hooks rewrite files: the trailing-whitespace hook
# was editing recorded srun stderr on the earlier launcher spike.  Recorded
# results are evidence and must land byte-for-byte as the machine produced
# them.
git -C "${repo}" commit -q --no-verify -m "${subject}" \
    -m "$(sed -n '1,12p' "${dest}/summary.txt")" \
    -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
echo "committed $(git -C "${repo}" log -1 --format=%h)"

if [ "${push}" = "0" ]; then
    echo "skipping push (--no-push); run: git push ${remote} ${branch}"
    exit 0
fi

# Several machines may record around the same time, so rebase before pushing
# and retry.
for attempt in 1 2 3; do
    if git -C "${repo}" push "${remote}" "${branch}" 2>&1 | tail -3; then
        echo "pushed to ${remote}/${branch}"
        exit 0
    fi
    echo "push failed (attempt ${attempt}); rebasing on ${remote}/${branch}"
    git -C "${repo}" fetch "${remote}" "${branch}"
    git -C "${repo}" rebase "${remote}/${branch}" || {
        echo "ERROR: rebase failed; resolve by hand, then run:" >&2
        echo "  git push ${remote} ${branch}" >&2
        exit 1
    }
done
echo "ERROR: could not push after 3 attempts" >&2
exit 1
