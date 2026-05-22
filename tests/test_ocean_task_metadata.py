from types import SimpleNamespace

from polaris.tasks.ocean.manufactured_solution.init import Init


def test_manufactured_solution_init_declares_base_mesh_output():
    init = Init(
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
