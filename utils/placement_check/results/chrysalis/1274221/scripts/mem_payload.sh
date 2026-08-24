#!/usr/bin/env bash
# Payload for the memory-enforcement check.
#
# Allocates several times the memory allowance its launch was given and
# records how far it got.  Two answers matter and both are useful: if nothing
# stops it, a memory request is inert on this machine and Polaris keeping its
# own memory budget is the whole of the answer.  If it is killed, then a step
# could be capped here, and -- by the same argument that applies to GPUs -- a
# launch that says nothing about memory may be read as claiming all of it.
#
# Progress is written and flushed as it goes, on purpose.  A process killed
# for exceeding a limit dies without warning, so anything held in a buffer
# until the end is exactly the evidence that would be lost.

set -u

test_name="${PLACE_TEST:?PLACE_TEST must be set}"
slot="${PLACE_SLOT:?PLACE_SLOT must be set}"
outdir="${PLACE_OUTDIR:?PLACE_OUTDIR must be set}"
target_mb="${PLACE_MEM_TARGET_MB:-4096}"
allowance_mb="${PLACE_MEM_ALLOWANCE_MB:-0}"
chunk_mb="${PLACE_MEM_CHUNK_MB:-64}"

rank="${SLURM_PROCID:-${PMI_RANK:-${PALS_RANKID:-${ALPS_APP_PE:-0}}}}"

mkdir -p "${outdir}/${test_name}"
out="${outdir}/${test_name}/slot${slot}_rank${rank}.kv"

{
    echo "test=${test_name}"
    echo "slot=${slot}"
    echo "rank=${rank}"
    echo "host=$(hostname)"
    echo "payload=memory"
    echo "cpus_allowed=$(awk '/Cpus_allowed_list/ {print $2}' /proc/self/status)"
    echo "allowance_mb=${allowance_mb}"
    echo "target_mb=${target_mb}"
    echo "t_start=$(date +%s.%N)"
} > "${out}"

python3 - "${target_mb}" "${chunk_mb}" "${out}" <<'PY'
import sys

target_mb = int(sys.argv[1])
chunk_mb = int(sys.argv[2])
path = sys.argv[3]

# Touch every page.  A bytearray of zeros is already resident, but writing to
# it makes that true of any allocator, and an untouched allocation is not
# what a limit would act on.
blocks = []
reached = 0
with open(path, 'a', buffering=1) as handle:
    while reached < target_mb:
        size = min(chunk_mb, target_mb - reached)
        block = bytearray(size * 1024 * 1024)
        block[::4096] = b'x' * len(block[::4096])
        blocks.append(block)
        reached += size
        handle.write(f'reached_mb={reached}\n')
        handle.flush()
    handle.write('completed=true\n')
    handle.flush()
PY
status=$?

{
    echo "t_end=$(date +%s.%N)"
    echo "python_rc=${status}"
} >> "${out}"

exit ${status}
