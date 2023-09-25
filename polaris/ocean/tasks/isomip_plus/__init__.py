from typing import Dict, Union

from polaris.ocean.resolution import resolution_to_subdir
from polaris.ocean.tasks.isomip_plus.cull_mesh import CullMesh
from polaris.ocean.tasks.isomip_plus.isomip_plus_test import IsomipPlusTest
from polaris.ocean.tasks.isomip_plus.planar_mesh import PlanarMesh
from polaris.ocean.tasks.isomip_plus.spherical_mesh import SphericalMesh
from polaris.ocean.tasks.isomip_plus.topo_map import TopoMap
from polaris.ocean.tasks.isomip_plus.topo_remap import TopoRemap


def add_isomip_plus_tasks(component, mesh_type):
    """
    Add tasks for different baroclinic channel tests to the ocean component

    component : polaris.ocean.Ocean
        the ocean component that the tasks will be added to

    mesh_type : {'planar', 'spherical'}
        The type of mesh
    """
    planar = (mesh_type == 'planar')
    for resolution in [8., 4., 2., 1., 0.5]:
        mesh_name = resolution_to_subdir(resolution)
        resdir = f'{mesh_type}/isomip_plus/{mesh_name}'

        subdir = f'{resdir}/base_mesh'
        base_mesh: Union[PlanarMesh, SphericalMesh, None] = None
        if mesh_type == 'planar':
            base_mesh = PlanarMesh(component=component,
                                   resolution=resolution,
                                   subdir=subdir)
        else:
            base_mesh = SphericalMesh(component=component,
                                      cell_width=resolution,
                                      subdir=subdir)

        subdir = f'{resdir}/topo/map_base'
        topo_map_base = TopoMap(component=component,
                                subdir=subdir,
                                mesh_name=mesh_name,
                                mesh_step=base_mesh,
                                mesh_filename='base_mesh.nc')

        subdir = f'{resdir}/topo/remap_base'
        topo_remap_base = TopoRemap(component=component,
                                    subdir=subdir,
                                    topo_map=topo_map_base,
                                    experiment='ocean1')

        subdir = f'{resdir}/topo/cull_mesh'
        cull_mesh = CullMesh(component=component,
                             subdir=subdir,
                             base_mesh=base_mesh,
                             topo_remap=topo_remap_base)

        for experiment in ['ocean0']:
            for vertical_coordinate in ['z-star']:
                task = IsomipPlusTest(component=component,
                                      resdir=resdir,
                                      resolution=resolution,
                                      experiment=experiment,
                                      vertical_coordinate=vertical_coordinate,
                                      base_mesh=base_mesh,
                                      topo_map=topo_map_base,
                                      topo_remap=topo_remap_base,
                                      cull_mesh=cull_mesh,
                                      planar=planar)
                component.add_task(task)
