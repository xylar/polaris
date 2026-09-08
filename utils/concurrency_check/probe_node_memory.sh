#!/bin/sh
# Report what one node says about the memory a job may use on it.
#
# Run once per node, through whatever launcher the machine has.  Every line
# is `key=value` so that the summary can parse it without caring which
# fields a given machine happens to have.

printf 'host=%s\n' "$(hostname -s 2>/dev/null || hostname)"

awk '/^MemTotal:/ {print "mem_total_kb=" $2}
     /^MemAvailable:/ {print "mem_available_kb=" $2}' /proc/meminfo 2>/dev/null

# The cgroup this process is in is what a batch system holds a job to, and
# it is the only one of these numbers that means "what this job may use
# here" rather than "what this node has".  v2 keeps one unified hierarchy;
# v1 keeps a memory controller of its own, and the path comes from
# /proc/self/cgroup.
if [ -f /sys/fs/cgroup/cgroup.controllers ]; then
    printf 'cgroup_version=2\n'
    rel=$(awk -F: '$1 == "0" {print $3}' /proc/self/cgroup 2>/dev/null)
    printf 'cgroup_path=%s\n' "${rel:-unknown}"
    for name in memory.max memory.high; do
        value=$(cat "/sys/fs/cgroup${rel}/${name}" 2>/dev/null)
        [ -n "$value" ] && printf '%s=%s\n' "$(echo "$name" | tr . _)" "$value"
    done
else
    printf 'cgroup_version=1\n'
    rel=$(awk -F: '$2 ~ /(^|,)memory(,|$)/ {print $3}' /proc/self/cgroup 2>/dev/null)
    printf 'cgroup_path=%s\n' "${rel:-unknown}"
    for base in "/sys/fs/cgroup/memory${rel}" /sys/fs/cgroup/memory; do
        value=$(cat "${base}/memory.limit_in_bytes" 2>/dev/null)
        if [ -n "$value" ]; then
            printf 'memory_limit_in_bytes=%s\n' "$value"
            printf 'memory_limit_from=%s\n' "$base"
            break
        fi
    done
fi

# What the batch system itself advertises for this node, which is the figure
# a site's config is normally copied from.  Absent where there is no such
# command, which is an answer rather than a gap.
if command -v scontrol >/dev/null 2>&1; then
    scontrol show node "$(hostname -s 2>/dev/null || hostname)" 2>/dev/null |
        tr ' ' '\n' | awk -F= '$1 == "RealMemory" || $1 == "AllocMem" ||
                               $1 == "FreeMem" {print "slurm_" tolower($1) "_mb=" $2}'
fi

printf 'end=%s\n' "$(hostname -s 2>/dev/null || hostname)"
