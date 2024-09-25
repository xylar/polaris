import os

from polaris.config import PolarisConfigParser
from polaris.ocean.ice_shelf import IceShelfTask
from polaris.ocean.tasks.isomip_plus.init import Forcing, Init
from polaris.ocean.tasks.isomip_plus.ssh_forward import SshForward


class IsomipPlusTest(IceShelfTask):
    """
    An ISOMIP+ test case

    Attributes
    ----------
    resolution : float
        The horizontal resolution (km) of the test case

    experiment : str
        The ISOMIP+ experiment

    vertical_coordinate : str
            The type of vertical coordinate (``z-star``, ``z-level``, etc.)

    tidal_forcing: bool
        Whether the case has tidal forcing

    thin_film: bool
        Whether a thin film is present under land ice

    planar : bool, optional
        Whether the test case runs on a planar or a spherical mesh
    """

    def __init__(self, component, resdir, resolution, experiment,
                 vertical_coordinate, planar, shared_steps,
                 thin_film=False, tidal_forcing=False):
        """
        Create the test case

        Parameters
        ----------
        component : polaris.Component
            The component the task belongs to

        resdir : str
            The subdirectory in the component for ISOMIP+ experiments of the
            given resolution

        resolution : float
            The horizontal resolution (km) of the test case

        experiment : str
            The ISOMIP+ experiment

        vertical_coordinate : str
            The type of vertical coordinate (``z-star``, ``z-level``, etc.)

        planar : bool
            Whether the test case runs on a planar or a spherical mesh

        shared_steps : dict
            The shared step for creating a topography mapping file from
            the ISOMIP+ input data to the base mesh

        thin_film: bool, optional
            Whether the run includes a thin film below grounded ice

        tidal_forcing: bool, optional
            Whether the run includes a single-period tidal forcing
        """  # noqa: E501
        name = experiment
        if tidal_forcing:
            name = f'tidal_forcing_{name}'
        if thin_film:
            name = f'thin_film_{name}'

        self.resolution = resolution
        self.experiment = experiment
        self.vertical_coordinate = vertical_coordinate
        self.thin_film = thin_film
        self.tidal_forcing = tidal_forcing
        self.planar = planar
        subdir = os.path.join(resdir, vertical_coordinate, name)
        sshdir = os.path.join(subdir, 'adjust_ssh')
        super().__init__(component=component, min_resolution=resolution,
                         name=name, subdir=subdir, sshdir=sshdir)

        config_filename = 'isomip_plus.cfg'
        config = PolarisConfigParser(
            filepath=os.path.join(subdir, config_filename))
        self.set_shared_config(config, link=config_filename)

        config.add_from_package('polaris.ocean.ice_shelf', 'freeze.cfg')
        config.add_from_package('polaris.ocean.ice_shelf',
                                'ssh_adjustment.cfg')
        config.add_from_package('polaris.ocean.tasks.isomip_plus',
                                'isomip_plus.cfg')
        config.add_from_package('polaris.ocean.tasks.isomip_plus',
                                'isomip_plus_init.cfg')

        for symlink, step in shared_steps.items():
            if symlink == 'topo_final':
                continue
            self.add_step(step, symlink=symlink)

        culled_mesh = shared_steps['topo/cull_mesh']
        topo = shared_steps['topo_final']

        init = Init(component=component,
                    indir=subdir,
                    culled_mesh=culled_mesh,
                    topo=topo,
                    experiment=experiment,
                    vertical_coordinate=vertical_coordinate,
                    thin_film=thin_film)
        init.set_shared_config(config, link=config_filename)
        self.add_step(init)

        forcing = Forcing(component=component,
                          indir=subdir,
                          culled_mesh=culled_mesh,
                          topo=topo,
                          resolution=resolution,
                          experiment=experiment,
                          vertical_coordinate=vertical_coordinate,
                          thin_film=thin_film)
        forcing.set_shared_config(config, link=config_filename)
        self.add_step(forcing)

        mesh_filename = os.path.join(culled_mesh.path, 'culled_mesh.nc')
        graph_filename = os.path.join(culled_mesh.path, 'culled_graph.info')
        init_filename = os.path.join(init.path, 'culled_graph.info')

        self.setup_ssh_adjustment_steps(
            mesh_filename=mesh_filename,
            graph_filename=graph_filename,
            init_filename=init_filename,
            config=config, config_filename=config_filename,
            ForwardStep=SshForward,
            yaml_filename='forward.yaml',
            package='polaris.ocean.tasks.isomip_plus')

    def configure(self):
        """
        Modify the configuration options for this test case.
        """
        config = self.config
        vertical_coordinate = self.vertical_coordinate
        experiment = self.experiment

        # for most coordinates, use the config options, which is 36 layers
        levels = None
        if vertical_coordinate == 'single-layer':
            levels = 1
            # this isn't a known coord_type so use z-level
            vertical_coordinate = 'z-level'

        if vertical_coordinate == 'sigma':
            if experiment in ['wetting', 'drying']:
                levels = 3
            else:
                levels = 10

        config.set('vertical_grid', 'coord_type', vertical_coordinate)
        if levels is not None:
            config.set('vertical_grid', 'vert_levels', f'{levels}')
