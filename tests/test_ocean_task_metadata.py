from types import SimpleNamespace

from polaris.tasks.ocean.ice_shelf_2d.forward import Forward
from polaris.tasks.ocean.inertial_gravity_wave.init import (
    Init as InertialGravityWaveInit,
)
from polaris.tasks.ocean.manufactured_solution.init import (
    Init as ManufacturedSolutionInit,
)


def test_manufactured_solution_init_declares_base_mesh_output():
    init = ManufacturedSolutionInit(
        component=SimpleNamespace(name='ocean'),
        resolution=200.0,
        subdir='planar/manufactured_solution/init/200km',
        name='init_200km',
    )
    init.config.set('ocean', 'model', 'omega')

    init.setup()

    assert 'base_mesh.nc' in init.outputs
    assert 'culled_mesh.nc' in init.outputs
    assert 'initial_state.nc' in init.outputs


def test_inertial_gravity_wave_init_declares_base_mesh_output():
    init = InertialGravityWaveInit(
        component=SimpleNamespace(name='ocean'),
        resolution=200.0,
        subdir='planar/inertial_gravity_wave/init/200km',
    )

    assert 'base_mesh.nc' in init.outputs
    assert 'culled_mesh.nc' in init.outputs
    assert 'initial_state.nc' in init.outputs
    assert 'culled_graph.info' in init.outputs


def test_ice_shelf_2d_forward_declares_validation_outputs():
    mesh = SimpleNamespace(path='ocean/planar/ice_shelf_2d/init')
    init = SimpleNamespace(path='ocean/planar/ice_shelf_2d/ssh_adjustment')
    forward = Forward(
        component=SimpleNamespace(name='ocean'),
        resolution=5.0,
        mesh=mesh,
        init=init,
        indir='ocean/planar/ice_shelf_2d/5km/z-star/default',
    )

    assert 'output.nc' in forward.outputs
    assert 'land_ice_fluxes.nc' in forward.outputs
    assert 'frazil.nc' in forward.outputs
