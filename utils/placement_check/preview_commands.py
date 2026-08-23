#!/usr/bin/env python3
"""
Show the commands the placement check would run, without running anything.

Every launch the check makes is a command mache renders, and the whole point
of the check is what those commands are.  Being able to read them before a
single job is submitted is worth a lot: five machines is five queues, and a
placement that renders wrongly is much cheaper to see here.

Nothing about this is a substitute for running the check.  A command can be
perfectly formed and still not be honored, which is exactly what the real
runs are for.

The batch environment is faked, so this runs anywhere -- a login node
included.  Slurm machines are rendered for both eras, since the flags differ
completely across the 20.11 change and this cannot know which a machine runs.
"""

import argparse
import os
import shlex
import sys
from contextlib import contextmanager
from unittest import mock

from mache.parallel.pbs import PbsSystem
from mache.parallel.single_node import SingleNodeSystem
from mache.parallel.slurm import SlurmSystem

from polaris.config import PolarisConfigParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from check_placement import (  # noqa: E402
    build_checks,
    get_usable_cores,
    needs_explicit_gpu_ids,
)

# the machines Phase A has to be validated on
DEFAULT_MACHINES = ('chrysalis', 'pm-cpu', 'pm-gpu', 'frontier', 'aurora')

# the two eras of Slurm, both of which are in production on these machines
SLURM_ERAS = {'20.11 and newer': (25, 11), 'older than 20.11': (20, 2)}

# stand-in node names, so that the rendered -w / --hosts is recognizable
FAKE_NODES = ('node0001', 'node0002')


def main():
    """Render and print the check's commands for each machine."""
    args = parse_args()
    for machine in args.machines:
        for compiler in get_compilers(machine):
            for label, system in iter_systems(machine, compiler):
                print()
                print('=' * 70)
                heading = machine
                if compiler != '':
                    heading += f' [{compiler}]'
                if label != '':
                    heading += f' ({label})'
                print(heading)
                print('=' * 70)
                report_machine(system, args)
    return 0


def parse_args():
    """Parse the command-line arguments for the preview."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--machines',
        nargs='+',
        default=list(DEFAULT_MACHINES),
        help='the machines to render for',
    )
    parser.add_argument('--slots', type=int, default=4)
    parser.add_argument('--ntasks', type=int, default=2)
    parser.add_argument('--cpus-per-task', type=int, default=4)
    return parser.parse_args()


def get_compilers(machine):
    """
    Get every compiler a machine can actually be deployed with.

    Taken from the ``mpi_<compiler>`` options in ``[deploy]``, which define
    the valid combinations, plus any compiler with parallel options of its
    own.  Listing only the latter would miss the case that matters most: a
    compiler with no ``[parallel.<compiler>]`` section falls back to the base
    ``[parallel]``, and on pm-gpu that fallback is the deployment with the
    GPUs.
    """
    config = build_raw_config(machine)
    compilers = set()
    if config.has_section('deploy'):
        for option in config.options('deploy'):
            if option.startswith('mpi_'):
                compilers.add(option[len('mpi_') :])
    for section in config.sections():
        if section.startswith('parallel.'):
            compilers.add(section[len('parallel.') :])
    if len(compilers) == 0:
        compilers.add('')
    return sorted(compilers)


def build_raw_config(machine, compiler=''):
    """Assemble the config a Polaris run would see for a machine."""
    config = PolarisConfigParser()
    config.add_from_package('polaris', 'default.cfg')
    config.add_from_package(
        'mache.machines', f'{machine}.cfg', exception=False
    )
    config.add_from_package('polaris.machines', f'{machine}.cfg')
    config.set('build', 'machine', machine, user=True)
    config.set('build', 'compiler', compiler, user=True)
    config.combine()
    return config.combined


def iter_systems(machine, compiler):
    """
    Yield the parallel systems to render for a machine.

    Slurm machines get one per era, because a site can be either and the two
    render nothing alike.  Everything else gets one.

    Each system is yielded from inside the context that fakes its batch
    environment, and stays there until the caller asks for the next one.
    That matters: ``placement_support`` reads the Slurm version every time it
    is asked, so a system built under a faked version and then rendered
    outside it would quietly render for whatever Slurm this machine runs.
    """
    config = build_raw_config(machine, compiler)
    system_name = config.get('parallel', 'system', fallback=None)

    if system_name == 'slurm':
        for label, version in SLURM_ERAS.items():
            with fake_slurm(version):
                yield label, SlurmSystem(config)
    elif system_name == 'pbs':
        with fake_pbs():
            yield '', PbsSystem(config)
    elif system_name == 'single_node':
        yield '', SingleNodeSystem(config)


def report_machine(system, args):
    """Print the placement support and every command for one machine."""
    print(f'launcher:           {system.get_config("parallel_executable")}')
    support = system.placement_support
    print(f'placement support:  {support.value}')
    if support.value == 'none':
        print('  Placement support is probed from the launcher actually')
        print('  installed here, not from the machine config, so a machine')
        print('  previewed from somewhere else reports none.  Run the')
        print('  preview on the machine itself to see its real answer.')

    try:
        cores = get_usable_cores(system)
    except SystemExit as exception:
        print(f'usable cores:       unknown -- {exception}')
        return
    gpus_per_node = system.gpus_per_node or 0
    gpus_per_slot = gpus_per_node // args.slots
    print(f'usable cores:       {len(cores)}')
    print(f'gpus per node:      {gpus_per_node} ({gpus_per_slot} per slot)')

    try:
        checks = build_checks(
            node=FAKE_NODES[0],
            cores=cores,
            slots=args.slots,
            ntasks=args.ntasks,
            cpus_per_task=args.cpus_per_task,
            gpus_per_slot=gpus_per_slot,
            gpu_ids_needed=needs_explicit_gpu_ids(system),
        )
    except SystemExit as exception:
        print(f'cannot lay out the checks: {exception}')
        return

    for check in checks:
        print()
        print(f'  {check.name}')
        for slot, placement in enumerate(check.placements, start=1):
            print(f'    slot {slot}: {render(system, placement, args)}')


def render(system, placement, args):
    """Render one launch, or say why it could not be rendered."""
    try:
        command = system.get_parallel_command(
            args=['PAYLOAD'],
            ntasks=args.ntasks,
            cpus_per_task=args.cpus_per_task,
            placement=placement,
        )
    except ValueError as exception:
        return f'NOT RENDERED: {exception}'
    return shlex.join(command)


@contextmanager
def fake_slurm(version):
    """Pretend to be inside a Slurm allocation of a given Slurm version."""
    with (
        mock.patch.dict(os.environ, {'SLURM_JOB_ID': '12345'}),
        mock.patch(
            'mache.parallel.slurm._get_subprocess_int',
            lambda args: len(FAKE_NODES),
        ),
        mock.patch('mache.parallel.slurm.get_slurm_version', lambda: version),
    ):
        yield


@contextmanager
def fake_pbs():
    """Pretend to be inside a PBS allocation."""
    with (
        mock.patch.dict(os.environ, {'PBS_JOBID': '12345.server'}),
        mock.patch.object(
            PbsSystem,
            '_get_node_count_from_qstat',
            lambda self: len(FAKE_NODES),
        ),
    ):
        yield


if __name__ == '__main__':
    sys.exit(main())
