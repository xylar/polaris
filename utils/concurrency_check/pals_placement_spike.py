"""
Does PALS apply ``--cpu-bind list:`` across the launch, or per node?

mache renders a placement on Aurora as ``--hosts`` plus one CPU list entry
per task, counted across *all* the nodes the launch names.  Slurm was found
to restart an equivalent mask list on every node, so a three-node launch
given 48 masks used the first node's 16 on all three: every launch
succeeded, on cores nobody had chosen, and nothing said so.  Aurora takes
the other launcher and has never been asked the same question.

So each case below gives the two nodes deliberately disjoint cores -- low
numbers on the first, high on the second -- and asks the ranks where they
landed.  A launcher that restarts the list per node puts the second node's
ranks on the first node's cores, which those disjoint ranges make
unmistakable.

Run it inside a two-node PBS job; ``pals_placement_aurora.pbs`` beside this
file does that.  It needs no model build, which is why it can run before
one exists.
"""

import logging
import os
import subprocess
import sys
from dataclasses import dataclass

from mache.parallel import ResourcePlacement, get_parallel_system
from mache.parallel.placement import split_cores

from polaris.config import PolarisConfigParser
from polaris.parallel import check_mache_supports_placement
from polaris.run.allocation import read_allocation
from polaris.run.confinement import MARKER, PROBE, _parse, _ranges

# Cases are (name, cores on the first node, cores on the second node,
# ntasks, cpus_per_task).  A ``None`` second set keeps the launch on one
# node, which is the control: a mismatch there is not about spanning.
#
# The cores avoid 0, 49-52 and 101-103, which the machine's own binding
# lists leave out, so that a rank refused a reserved core cannot be
# mistaken for the answer this is looking for.
CASES = [
    ('one node, two tasks', (1, 2, 3, 4), None, 2, 2),
    ('two nodes, one task each', (1,), (60,), 2, 1),
    ('two nodes, two tasks each', (1, 2, 3, 4), (61, 62, 63, 64), 4, 2),
    ('two nodes, three tasks', (5, 6), (65,), 3, 1),
]


def main():
    """Run every case against the allocation this job holds."""
    check_mache_supports_placement()

    system = _build_parallel_system()
    names = system.node_names
    print(f'node_names: {names}')
    print(f'cores_per_node: {system.cores_per_node}')
    print(f'gpus_per_node: {system.gpus_per_node}')
    print(f'placement_support: {system.placement_support}')
    print()

    if not names or len(names) < 2:
        print('FAIL: this needs an allocation of at least two nodes.')
        return 1

    print('== what each node says about its memory ==')
    _read_memory(system)
    print()
    print('== does a placement spanning nodes land where it was placed? ==')

    failures = 0
    for name, first, second, ntasks, cpus_per_task in CASES:
        nodes = names[:1] if second is None else names[:2]
        cores = (first,) if second is None else (first, second)
        placement = ResourcePlacement(nodes=tuple(nodes), cores=cores)
        if not _run_case(name, system, placement, ntasks, cpus_per_task):
            failures += 1

    print()
    if failures:
        print(
            f'{failures} of {len(CASES)} case(s) did not land where they '
            f'were placed.'
        )
    else:
        print(f'All {len(CASES)} cases landed exactly where they were placed.')
    return 1 if failures else 0


@dataclass
class _Allocation:
    """
    The little of a component that reading an allocation needs.

    ``read_allocation`` wants a component for its parallel system and for
    its name in one error message, and building a real one would pull in a
    whole component's config for no gain here.
    """

    parallel_system: object
    name: str = 'spike'


def _read_memory(system):
    """Take the same reading a concurrent run takes when it starts."""
    logging.basicConfig(
        format='%(message)s', level=logging.DEBUG, stream=sys.stdout
    )
    read_allocation(_Allocation(parallel_system=system), logging.getLogger())


def _build_parallel_system():
    """Build the parallel system the way a polaris run does."""
    config = PolarisConfigParser()
    config.add_from_package('polaris', 'default.cfg')
    config.add_from_package('mache.machines', 'aurora.cfg')
    config.add_from_package('polaris.machines', 'aurora.cfg')
    # cores_per_node and the binding options live in the machine's
    # parallel.<compiler> section, and mache picks that section from
    # [build] compiler, which polaris setup fills in from the environment.
    # Without it the parallel system has no cores per node and refuses to
    # be built.
    config.set('build', 'machine', os.environ['POLARIS_MACHINE'], user=True)
    config.set('build', 'compiler', os.environ['POLARIS_COMPILER'], user=True)
    config.set('build', 'mpi', os.environ['POLARIS_MPI'], user=True)
    return get_parallel_system(config)


def _run_case(name, system, placement, ntasks, cpus_per_task):
    """Launch one placement and report whether its ranks honored it."""
    print(f'== {name} ==')
    chunks = split_cores(placement, ntasks, cpus_per_task)
    print(f'placement nodes: {list(placement.nodes)}')
    for node, cores in zip(placement.nodes, placement.cores, strict=False):
        print(f'  {node}: {_ranges(cores)}')
    print(f'tasks: {ntasks}, cpus_per_task: {cpus_per_task}')
    print(f'chunks in task order: {chunks}')

    command = system.get_parallel_command(
        args=[sys.executable, '-c', PROBE],
        ntasks=ntasks,
        cpus_per_task=cpus_per_task,
        placement=placement,
    )
    print(f'command: {" ".join(command)}')

    result = subprocess.run(
        command, capture_output=True, text=True, timeout=300, check=False
    )
    if result.returncode != 0:
        print(f'  the launch exited {result.returncode}')
        print(_indent(result.stderr.strip()))
        return False

    print('  raw probe output:')
    print(
        _indent(
            '\n'.join(
                line for line in result.stdout.splitlines() if MARKER in line
            )
        )
    )

    seen = _parse(result.stdout)
    if not seen:
        print('  no rank reported, so nothing can be concluded')
        return False
    return _report(placement, seen)


def _report(placement, seen):
    """Say, node by node, what was given against what was allowed."""
    promised = {
        node: set(cores)
        for node, cores in zip(placement.nodes, placement.cores, strict=False)
    }
    # the probe prints a short hostname while the node file may hold a fully
    # qualified one, and a name that does not match is a different finding
    # from cores that do not, so the two are kept apart here
    short = {node.split('.')[0]: node for node in promised}

    good = True
    for reported in sorted(seen):
        node = short.get(reported)
        if node is None:
            print(
                f'  {reported} answered but was not placed on; the '
                f'placement named {sorted(promised)}'
            )
            good = False
            continue
        allowed = seen[reported]
        given = promised[node]
        if allowed == given:
            print(
                f'  {reported}: allowed exactly its {len(given)} cores '
                f'({_ranges(given)})'
            )
            continue
        good = False
        print(
            f'  {reported}: allowed {len(allowed)} cores '
            f'({_ranges(allowed)}) but was given {len(given)} '
            f'({_ranges(given)})'
        )
    missing = [node for node in promised if node.split('.')[0] not in seen]
    if missing:
        print(f'  no rank reported from {missing}')
        good = False
    return good


def _indent(text):
    """Indent captured output so it reads as a quotation."""
    return '\n'.join(f'    {line}' for line in text.splitlines())


if __name__ == '__main__':
    sys.exit(main())
