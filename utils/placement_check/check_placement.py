#!/usr/bin/env python3
"""
Check that the commands mache renders from a ``ResourcePlacement`` really do
confine a launch on this machine.

The mechanisms this relies on were already measured, with commands written by
hand, and the results are summarized in the Polaris design document
``docs/design_docs/task_parallelism.md``.  What is *not* yet established, and
what this checks, is that the commands ``mache`` renders behave the same way.
That is a different claim, and it is the one that gates mache pull request
#470.

Nothing here decides which resources a step should get.  The placements are
built by this script, laid out so that concurrent launches cannot overlap,
precisely so that a failure is attributable to mache's rendering rather than
ambiguous between mache and a Polaris scheduler that does not exist yet.

Run it inside an allocation, through ``run_check.sh``, which sources the
Polaris environment first.
"""

import argparse
import os
import shlex
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import List, Sequence

from mache.parallel import (
    PlacementSupport,
    ResourcePlacement,
    get_parallel_system,
)

from polaris.config import PolarisConfigParser

# how long after the payload should have finished to give up on a launch
TIMEOUT_MARGIN = 120


@dataclass(frozen=True)
class Launch:
    """One launch: where it was told to run, and the command that says so."""

    slot: int
    placement: ResourcePlacement | None
    command: List[str]


@dataclass(frozen=True)
class Check:
    """A set of launches to start at the same moment."""

    name: str
    description: str
    placements: Sequence[ResourcePlacement | None]


def main():
    """Run the placement check and write its results to a directory."""
    args = parse_args()

    machine = args.machine
    if machine is None:
        machine = os.environ.get('POLARIS_MACHINE')
    if machine is None:
        raise SystemExit(
            'The machine is unknown.  Source the load script that '
            './deploy.py generated, which exports POLARIS_MACHINE, or pass '
            '--machine.'
        )

    config = build_config(machine)
    parallel_system = get_parallel_system(config)
    support = parallel_system.placement_support

    nodes = get_allocation_nodes()
    if len(nodes) == 0:
        raise SystemExit(
            'No allocation was found.  This check has to run inside a job, '
            'since it needs real nodes to place launches on.'
        )

    cores = get_usable_cores(parallel_system, args.core_list)
    gpus_per_node = parallel_system.gpus_per_node or 0
    gpus_per_slot = 0
    if not args.skip_gpu and gpus_per_node > 0:
        gpus_per_slot = gpus_per_node // args.slots

    gpu_ids_needed = needs_explicit_gpu_ids(parallel_system)

    os.makedirs(args.outdir, exist_ok=True)
    _print_header(
        machine=machine,
        parallel_system=parallel_system,
        support=support,
        nodes=nodes,
        cores=cores,
        gpus_per_node=gpus_per_node,
        gpus_per_slot=gpus_per_slot,
        args=args,
    )
    write_meta(
        outdir=args.outdir,
        machine=machine,
        parallel_system=parallel_system,
        support=support,
        nodes=nodes,
        cores=cores,
        gpus_per_node=gpus_per_node,
        gpus_per_slot=gpus_per_slot,
        args=args,
    )

    checks = build_checks(
        node=nodes[0],
        cores=cores,
        slots=args.slots,
        ntasks=args.ntasks,
        cpus_per_task=args.cpus_per_task,
        gpus_per_slot=gpus_per_slot,
        gpu_ids_needed=gpu_ids_needed,
    )

    failures = 0
    for check in checks:
        failures += run_check(parallel_system, check, args)

    _set_meta_status(args.outdir, 'complete')
    print()
    print(f'results: {args.outdir}')
    if failures > 0:
        print(f'{failures} launch(es) returned nonzero; see the .err files')
    return 0


def parse_args():
    """Parse the command-line arguments for the placement check."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--outdir', required=True, help='where to write the results'
    )
    parser.add_argument(
        '--payload', required=True, help='the executable each launch runs'
    )
    parser.add_argument(
        '--machine', default=None, help='the machine name known to mache'
    )
    parser.add_argument(
        '--slots',
        type=int,
        default=4,
        help='how many launches to run at once (default 4)',
    )
    parser.add_argument(
        '--ntasks',
        type=int,
        default=2,
        help='MPI tasks in each launch (default 2)',
    )
    parser.add_argument(
        '--cpus-per-task',
        type=int,
        default=4,
        help='cores each task gets (default 4)',
    )
    parser.add_argument(
        '--sleep',
        type=int,
        default=15,
        help='seconds the payload sleeps, so that overlap is unambiguous',
    )
    parser.add_argument(
        '--core-list',
        default=None,
        help='the usable cores on a node, as 0-7,16 (default: from the '
        'machine config)',
    )
    parser.add_argument(
        '--skip-gpu', action='store_true', help='skip the GPU checks'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='render the commands and write them out, but launch nothing',
    )
    return parser.parse_args()


def build_config(machine):
    """
    Assemble the config a Polaris run would see for this machine.

    This is Polaris's own config assembly rather than a hand-written one, so
    that the parallel options mache is given here are the ones it would be
    given in a real run.  Only the placements themselves are this script's
    invention.
    """
    config = PolarisConfigParser()
    config.add_from_package('polaris', 'default.cfg')
    config.add_from_package(
        'mache.machines', f'{machine}.cfg', exception=False
    )
    config.add_from_package('polaris.machines', f'{machine}.cfg')
    # the [parallel.<compiler>] section is where the GPU machines describe
    # their GPUs, and it is selected by [build] compiler
    compiler = os.environ.get('POLARIS_COMPILER')
    if compiler is not None:
        config.set('build', 'compiler', compiler, user=True)
    mpi = os.environ.get('POLARIS_MPI')
    if mpi is not None:
        config.set('build', 'mpi', mpi, user=True)
    config.set('build', 'machine', machine, user=True)
    config.combine()
    return config.combined


def get_allocation_nodes():
    """Get the hostnames of the nodes in the current allocation."""
    nodelist = os.environ.get('SLURM_JOB_NODELIST')
    if nodelist is not None and nodelist != '':
        output = subprocess.check_output(
            ['scontrol', 'show', 'hostnames', nodelist], text=True
        )
        return [line.strip() for line in output.splitlines() if line.strip()]

    nodefile = os.environ.get('PBS_NODEFILE')
    if nodefile is not None and os.path.exists(nodefile):
        nodes = []
        with open(nodefile) as handle:
            for line in handle:
                name = line.strip()
                if name != '' and name not in nodes:
                    nodes.append(name)
        return nodes

    # No batch system: the only node there is, is this one. That is right for
    # a single-node machine and it is what makes --dry-run work anywhere.
    return [socket.gethostname()]


def get_usable_cores(parallel_system, override=None):
    """
    Get the cores on a node that a launch is allowed to use.

    A machine whose config binds tasks to an explicit list of cores has
    already said which cores are usable, and it is not always all of them --
    Aurora reserves core 0 and cores 49-52.  Anywhere else, every core counts.
    """
    if override is not None and override != '':
        return parse_core_list(override)

    cpu_bind = parallel_system.get_config('cpu_bind')
    if cpu_bind is not None and cpu_bind.startswith('list:'):
        cores = set()
        for chunk in cpu_bind[len('list:') :].split(':'):
            cores.update(parse_core_list(chunk))
        return sorted(cores)

    cores_per_node = parallel_system.cores_per_node
    if cores_per_node is None or cores_per_node <= 0:
        raise SystemExit(
            f'This machine reports {cores_per_node} cores per node and sets '
            f'no cpu_bind list, so there is no way to know which cores a '
            f'launch may use. Pass --core-list to say.'
        )
    return list(range(cores_per_node))


def parse_core_list(spec):
    """Parse a ``0-7,16`` core list into a sorted list of core numbers."""
    cores: set[int] = set()
    for chunk in spec.split(','):
        chunk = chunk.strip()
        if chunk == '':
            continue
        if '-' in chunk:
            low, _, high = chunk.partition('-')
            cores.update(range(int(low), int(high) + 1))
        else:
            cores.add(int(chunk))
    return sorted(cores)


def needs_explicit_gpu_ids(parallel_system):
    """
    Check whether this machine's launcher needs to be told which GPUs to use.

    Where the batch system assigns GPUs, asking for a number is enough and
    naming them would be presumptuous.  Where it does not -- PBS with PALS --
    mache confines a launch with the vendor's visible-devices variable and
    can only set what it is given, so the caller has to choose.  A machine
    config that names that variable is exactly a machine in the second case.
    """
    value = parallel_system.get_config('gpu_visible_devices_var')
    return value is not None and value.strip() != ''


def plan_placements(
    node, cores, slots, ntasks, cpus_per_task, gpus_per_slot, gpu_ids_needed
):
    """
    Lay out ``slots`` placements on one node so that none of them overlap.

    All the slots share a node on purpose: sharing one node is the hard case
    for placement, and spreading across nodes is not what we are unsure
    about.
    """
    per_slot = ntasks * cpus_per_task
    needed = slots * per_slot
    if needed > len(cores):
        raise SystemExit(
            f'{slots} slots x {ntasks} tasks x {cpus_per_task} cores need '
            f'{needed} cores, but only {len(cores)} are usable on a node.'
        )

    placements = []
    for index in range(slots):
        slot_cores = tuple(cores[index * per_slot : (index + 1) * per_slot])
        gpu_ids = None
        if gpus_per_slot > 0 and gpu_ids_needed:
            gpu_ids = tuple(
                range(index * gpus_per_slot, (index + 1) * gpus_per_slot)
            )
        placements.append(
            ResourcePlacement(
                nodes=(node,),
                cores=slot_cores,
                gpus=gpus_per_slot,
                gpu_ids=gpu_ids,
            )
        )
    return placements


def build_checks(
    node, cores, slots, ntasks, cpus_per_task, gpus_per_slot, gpu_ids_needed
):
    """
    Build the checks to run, in the order they should run.

    The single-launch checks come first because they say whether a placement
    is honored at all.  A concurrency result is only meaningful once that is
    known: launches that never overlapped trivially have disjoint cores.
    """
    cpu_only = plan_placements(
        node=node,
        cores=cores,
        slots=slots,
        ntasks=ntasks,
        cpus_per_task=cpus_per_task,
        gpus_per_slot=0,
        gpu_ids_needed=gpu_ids_needed,
    )

    checks = [
        Check(
            name='A_unplaced',
            description=(
                'one launch with no placement at all -- what Polaris does '
                'today, and the control that shows placement changes '
                'something'
            ),
            placements=[None],
        ),
        Check(
            name='B_placed_alone',
            description=(
                'one placed launch, alone, asking for no GPUs -- does it see '
                'only the cores it was given?'
            ),
            placements=[cpu_only[0]],
        ),
        Check(
            name='D_concurrent',
            description=(
                f'{slots} placed launches at once, none asking for GPUs'
            ),
            placements=cpu_only,
        ),
    ]

    if gpus_per_slot > 0:
        with_gpus = plan_placements(
            node=node,
            cores=cores,
            slots=slots,
            ntasks=ntasks,
            cpus_per_task=cpus_per_task,
            gpus_per_slot=gpus_per_slot,
            gpu_ids_needed=gpu_ids_needed,
        )
        checks.insert(
            2,
            Check(
                name='C_placed_alone_gpu',
                description=(
                    f'one placed launch, alone, asking for {gpus_per_slot} '
                    f'GPU(s) -- does it see only those?'
                ),
                placements=[with_gpus[0]],
            ),
        )
        checks.append(
            Check(
                name='E_concurrent_gpu',
                description=(
                    f'{slots} placed launches at once, each asking for '
                    f'{gpus_per_slot} GPU(s)'
                ),
                placements=with_gpus,
            )
        )

    return checks


def run_check(parallel_system, check, args):
    """Render a check's commands, start them together and wait for them."""
    test_dir = os.path.join(args.outdir, check.name)
    os.makedirs(test_dir, exist_ok=True)

    print()
    print(f'--- {check.name}: {check.description}')

    launches = []
    for slot, placement in enumerate(check.placements, start=1):
        try:
            command = parallel_system.get_parallel_command(
                args=[args.payload],
                ntasks=args.ntasks,
                cpus_per_task=args.cpus_per_task,
                placement=placement,
            )
        except ValueError as exception:
            # A machine that cannot render a placement is a result, not a
            # crash: pre-20.11 Slurm refuses GPU placement, for instance.
            print(f'    could not render slot {slot}: {exception}')
            with open(os.path.join(test_dir, 'render_error.txt'), 'w') as f:
                f.write(f'{exception}\n')
            return 0
        launches.append(
            Launch(slot=slot, placement=placement, command=command)
        )

    write_launches(test_dir, launches, args.outdir, check.name)
    for launch in launches:
        print(f'    slot {launch.slot}: {shlex.join(launch.command)}')

    if args.dry_run:
        return 0

    return _start_and_wait(launches, check, args, test_dir)


def write_launches(test_dir, launches, outdir, check_name):
    """Record what each launch was asked for and what command said so."""
    with open(os.path.join(test_dir, 'expected.kv'), 'w') as handle:
        for launch in launches:
            placement = launch.placement
            if placement is None:
                handle.write(f'slot={launch.slot} placement=none\n')
                continue
            gpu_ids = ''
            if placement.gpu_ids is not None:
                gpu_ids = ','.join(f'{gpu}' for gpu in placement.gpu_ids)
            handle.write(
                f'slot={launch.slot} '
                f'nodes={",".join(placement.nodes)} '
                f'cores={",".join(f"{core}" for core in placement.cores)} '
                f'gpus={placement.gpus} '
                f'gpu_ids={gpu_ids}\n'
            )

    for launch in launches:
        path = os.path.join(test_dir, f'slot{launch.slot}.cmd')
        with open(path, 'w') as handle:
            handle.write(f'{shlex.join(launch.command)}\n')

    with open(os.path.join(outdir, 'commands.txt'), 'a') as handle:
        for launch in launches:
            handle.write(
                f'{check_name} slot{launch.slot}: '
                f'{shlex.join(launch.command)}\n'
            )


def write_meta(
    outdir,
    machine,
    parallel_system,
    support,
    nodes,
    cores,
    gpus_per_node,
    gpus_per_slot,
    args,
):
    """
    Write the run metadata before anything that can fail.

    An aborted run is still worth recording, so this goes out first and the
    status is corrected at the end.
    """
    import mache

    scheduler = 'slurm'
    job_id = os.environ.get('SLURM_JOB_ID', '')
    if job_id == '':
        scheduler = 'pbs'
        job_id = os.environ.get('PBS_JOBID', '').split('.')[0]

    lines = {
        'machine': machine,
        'scheduler': scheduler,
        'job_id': job_id,
        'mache_version': mache.__version__,
        'mache_path': os.path.dirname(mache.__file__),
        'placement_support': support.value,
        'parallel_executable': parallel_system.get_config(
            'parallel_executable', ''
        ),
        'compiler': os.environ.get('POLARIS_COMPILER', ''),
        'mpi': os.environ.get('POLARIS_MPI', ''),
        'nodes': f'{len(nodes)}',
        'nodelist': ','.join(nodes),
        'target_node': nodes[0],
        'cores_on_node': f'{parallel_system.cores_per_node}',
        'gpus_on_node': f'{gpus_per_node}',
        'core_list': ','.join(f'{core}' for core in cores),
        'slots': f'{args.slots}',
        'ntasks': f'{args.ntasks}',
        'cpus_per_task': f'{args.cpus_per_task}',
        'gpus_per_slot': f'{gpus_per_slot}',
        'sleep': f'{args.sleep}',
        'payload': os.path.basename(args.payload),
        'started': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'status': 'incomplete',
    }
    with open(os.path.join(outdir, 'meta.kv'), 'w') as handle:
        for key, value in lines.items():
            handle.write(f'{key}={value}\n')


def _start_and_wait(launches, check, args, test_dir):
    """Start every launch in a check together, then wait for all of them."""
    timeout = args.sleep + TIMEOUT_MARGIN
    env = dict(os.environ)
    env['PLACE_TEST'] = check.name
    env['PLACE_OUTDIR'] = args.outdir
    env['PLACE_SLEEP'] = f'{args.sleep}'

    processes = []
    failed_to_start = []
    started = time.time()
    for launch in launches:
        slot_env = dict(env)
        slot_env['PLACE_SLOT'] = f'{launch.slot}'
        out = open(os.path.join(test_dir, f'slot{launch.slot}.out'), 'wb')
        err = open(os.path.join(test_dir, f'slot{launch.slot}.err'), 'wb')
        try:
            process = subprocess.Popen(
                launch.command, env=slot_env, stdout=out, stderr=err
            )
        except OSError as exception:
            # A launcher that is not on PATH is worth recording rather than
            # crashing on: the other slots may still say something useful.
            err.write(f'{exception}\n'.encode())
            out.close()
            err.close()
            _write_rc(test_dir, launch.slot, 127)
            failed_to_start.append(launch.slot)
            continue
        processes.append((launch, process, out, err))
    print(
        f'    all {len(processes)} launch(es) started in '
        f'{time.time() - started:.2f}s'
    )

    failures = len(failed_to_start)
    for slot in failed_to_start:
        print(f'    slot {slot} could not start; see its .err file')

    for launch, process, out, err in processes:
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            returncode = process.wait()
            print(f'    slot {launch.slot} timed out after {timeout}s')
        out.close()
        err.close()
        _write_rc(test_dir, launch.slot, returncode)
        if returncode != 0:
            failures += 1
            print(f'    slot {launch.slot} returned {returncode}')
    return failures


def _write_rc(test_dir, slot, returncode):
    """Record the exit code of one launch."""
    with open(os.path.join(test_dir, f'slot{slot}.rc'), 'w') as handle:
        handle.write(f'{returncode}\n')


def _print_header(
    machine,
    parallel_system,
    support,
    nodes,
    cores,
    gpus_per_node,
    gpus_per_slot,
    args,
):
    """Print what this run is about to do."""
    print('=== mache placement check ===')
    print(f'machine:            {machine}')
    print(
        f'launcher:           '
        f'{parallel_system.get_config("parallel_executable", "")}'
    )
    print(f'placement support:  {support.value}')
    if support is PlacementSupport.NONE:
        print('  This machine reports no placement mechanism, so every')
        print('  placed launch below will fail to render.  That is the')
        print('  correct behavior, but it means there is nothing to measure.')
    print(f'nodes:              {len(nodes)} ({",".join(nodes)})')
    print(f'target node:        {nodes[0]}')
    print(f'usable cores:       {len(cores)}')
    print(f'gpus per node:      {gpus_per_node}')
    if gpus_per_slot == 0 and not args.skip_gpu:
        print('  No GPU checks will run.  On the GPU machines the GPU count')
        print('  lives in [parallel.<compiler>], so a deployment with the')
        print('  machine default compiler can report none: Frontier needs')
        print('  craygnu_mphipcc and Aurora needs oneapi-ifxgpu.  If GPUs')
        print('  were meant to be checked, redeploy with that compiler.')
    print(
        f'layout:             {args.slots} slot(s) x {args.ntasks} task(s) '
        f'x {args.cpus_per_task} core(s), {gpus_per_slot} gpu(s) per slot'
    )
    print(f'payload:            {args.payload} (sleeps {args.sleep}s)')


def _set_meta_status(outdir, status):
    """Correct the status recorded in meta.kv once the run has finished."""
    path = os.path.join(outdir, 'meta.kv')
    if not os.path.exists(path):
        return
    with open(path) as handle:
        lines = handle.readlines()
    with open(path, 'w') as handle:
        for line in lines:
            if line.startswith('status='):
                handle.write(f'status={status}\n')
            else:
                handle.write(line)


if __name__ == '__main__':
    sys.exit(main())
