#!/usr/bin/env python3
"""
How does JIGSAW scale with threads, and does the answer depend on resolution?

JIGSAW is the only consumer of thread parallelism in Polaris's own Python
work, and it is not configured the way the rest of the framework is: the
binary links `libgomp`, but it takes its thread count from `NUMTHREAD` in its
own job-config file, which `jigsawpy` exposes as `opts.numthread`.  Polaris
never sets it, so the two steps that shell out to JIGSAW -- the
quasi-uniform and unified spherical base meshes -- run at whatever count the
machine offers, and their meshes are not reproducible across machines or
allocation shapes.

The agreed fix is for those steps to declare `cpus_per_task` and
`min_cpus_per_task` and pass what they were assigned to `opts.numthread`.
This measures what they should declare, because the expectation is that a
large mesh wants many cores and a small one few -- in which case a single
figure on the base class is the wrong shape.

It builds the same JIGSAW inputs `QuasiUniformSphericalMeshStep` builds, with
the same optimization settings, and times only the `jigsaw` call.  Runs
coarse to fine and stops when its time budget is spent, so a resolution that
turns out to be far more expensive than expected costs the run nothing that
was already measured.

    python jigsaw_thread_spike.py --budget 3000 --out results.txt
"""

import argparse
import os
import shutil
import subprocess
import sys
import time

import jigsawpy
import numpy as np
from jigsawpy.savejig import savejig

# what polaris gives JIGSAW for a quasi-uniform mesh, from
# polaris/mesh/spherical/spherical.cfg and QuasiUniformSphericalMeshStep
OPTM_KERN = 'odt+dqdx'
OPTM_ITER = 16
OPTM_QTOL = 1.0e-4
OPTM_QLIM = 0.9375
EARTH_RADIUS_KM = 6371.2290


def build(resolution, threads, where, timeout):
    """Build one quasi-uniform mesh, and say how long JIGSAW took."""
    os.makedirs(where, exist_ok=True)
    opts = jigsawpy.jigsaw_jig_t()
    opts.geom_file = os.path.join(where, 'geom.msh')
    opts.jcfg_file = os.path.join(where, 'opts.jig')
    opts.mesh_file = os.path.join(where, 'mesh.msh')
    opts.hfun_file = os.path.join(where, 'spac.msh')

    opts.hfun_scal = 'absolute'
    opts.hfun_hmax = float('inf')
    opts.hfun_hmin = 0.0
    opts.mesh_dims = 2
    opts.verbosity = 1
    opts.optm_kern = OPTM_KERN
    opts.optm_iter = OPTM_ITER
    opts.optm_qtol = OPTM_QTOL
    opts.optm_qlim = OPTM_QLIM

    # the thing under test.  None means "say nothing", which is what polaris
    # does today and leaves JIGSAW to choose.
    if threads is not None:
        opts.numthread = threads

    # a uniform cell-width field on a one-degree grid, as
    # QuasiUniformSphericalMeshStep builds for a constant resolution
    lon = np.arange(-180.0, 180.01, 1.0)
    lat = np.arange(-90.0, 90.01, 1.0)
    cell_width = resolution * np.ones((lat.size, lon.size))

    hmat = jigsawpy.jigsaw_msh_t()
    hmat.mshID = 'ELLIPSOID-GRID'
    hmat.xgrid = np.radians(lon)
    hmat.ygrid = np.radians(lat)
    hmat.value = cell_width
    jigsawpy.savemsh(opts.hfun_file, hmat)

    geom = jigsawpy.jigsaw_msh_t()
    geom.mshID = 'ELLIPSOID-MESH'
    geom.radii = EARTH_RADIUS_KM * np.ones(3, float)
    jigsawpy.savemsh(opts.geom_file, geom)

    savejig(opts.jcfg_file, opts)

    began = time.time()
    try:
        with open(os.path.join(where, 'jigsaw.log'), 'w') as log:
            subprocess.run(
                ['jigsaw', opts.jcfg_file],
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=True,
            )
    except subprocess.TimeoutExpired:
        return None, 'timed out'
    except subprocess.CalledProcessError as failure:
        return None, f'jigsaw exited {failure.returncode}'
    seconds = time.time() - began

    points = _count_points(opts.mesh_file)
    return seconds, f'{points} points'


def _count_points(mesh_file):
    """How many points JIGSAW put in the mesh it wrote."""
    try:
        with open(mesh_file) as handle:
            for line in handle:
                if line.upper().startswith('POINT='):
                    return int(line.split('=')[1])
    except OSError:
        pass
    return -1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--resolutions',
        type=float,
        nargs='+',
        default=[240.0, 120.0, 60.0, 30.0, 12.0],
        help='cell widths in km, coarse first',
    )
    parser.add_argument(
        '--threads',
        type=int,
        nargs='+',
        default=[1, 2, 4, 8, 16, 32, 64],
    )
    parser.add_argument(
        '--budget',
        type=float,
        default=3000.0,
        help='seconds to spend before stopping between resolutions',
    )
    parser.add_argument(
        '--timeout',
        type=float,
        default=1800.0,
        help='seconds to allow any single jigsaw call',
    )
    parser.add_argument('--work', default='jigsaw_thread_spike')
    parser.add_argument('--out', default='jigsaw_thread_spike.txt')
    args = parser.parse_args()

    started = time.time()
    lines = [
        f'jigsaw: {shutil.which("jigsaw")}',
        f'cores visible: {len(os.sched_getaffinity(0))}',
        f'optm_kern={OPTM_KERN} optm_iter={OPTM_ITER} '
        f'optm_qtol={OPTM_QTOL} optm_qlim={OPTM_QLIM}',
        '',
        f'{"res km":>8} {"threads":>8} {"seconds":>10} {"speedup":>8}  note',
    ]
    print('\n'.join(lines), flush=True)

    stop = False
    for resolution in args.resolutions:
        if stop or time.time() - started > args.budget:
            lines.append(f'# stopped before {resolution:g} km: budget spent')
            print(lines[-1], flush=True)
            break
        one_thread = None
        for threads in args.threads:
            # checked here as well as between resolutions: one resolution's
            # sweep can outlast the whole budget on its own, which is how
            # the first run of this hit its wall clock at 12 km and lost the
            # results file it had not written yet
            if time.time() - started > args.budget:
                lines.append(
                    f'# stopped inside {resolution:g} km: budget spent'
                )
                print(lines[-1], flush=True)
                stop = True
                break
            where = os.path.join(
                args.work, f'qu{resolution:g}km_{threads}thread'
            )
            seconds, note = build(resolution, threads, where, args.timeout)
            if seconds is None:
                row = f'{resolution:>8g} {threads:>8} {"":>10} {"":>8}  {note}'
            else:
                if threads == args.threads[0]:
                    one_thread = seconds
                speedup = one_thread / seconds if one_thread else float('nan')
                row = (
                    f'{resolution:>8g} {threads:>8} {seconds:>10.1f} '
                    f'{speedup:>8.2f}  {note}'
                )
            lines.append(row)
            print(row, flush=True)
            # rewritten after every point, so that a run killed part way
            # through still leaves what it had measured
            with open(args.out, 'w') as handle:
                handle.write('\n'.join(lines) + '\n')
            # a mesh at this size is not needed once it is timed
            shutil.rmtree(where, ignore_errors=True)

    with open(args.out, 'w') as handle:
        handle.write('\n'.join(lines) + '\n')
    print(f'\nwritten to {args.out}', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
