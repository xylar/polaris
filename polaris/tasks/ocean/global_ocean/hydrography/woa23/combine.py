import gsw
import numpy as np
import xarray as xr
from mpas_tools.io import write_netcdf

from polaris import Step


class CombineStep(Step):
    """
    A step for combining January and annual WOA23 climatologies.
    """

    def __init__(self, component, subdir):
        """
        Create the step.

        Parameters
        ----------
        component : polaris.Component
            The component the step belongs to.

        subdir : str
            The subdirectory for the step.
        """
        super().__init__(
            component=component,
            name='combine',
            dask_workers=102,  # number of depth levels
            min_dask_workers=1,
        )
        self.add_output_file(filename='woa_combined.nc')

    def setup(self):
        """
        Set up input files for the step.
        """
        super().setup()

        base_url = (
            'https://www.ncei.noaa.gov/thredds-ocean/fileServer/woa23/DATA'
        )
        directories = {
            'temp': {
                'ann': 'temperature/netcdf/decav91C0/0.25',
                'jan': 'temperature/netcdf/decav91C0/0.25',
            },
            'salin': {
                'ann': 'salinity/netcdf/decav91C0/0.25',
                'jan': 'salinity/netcdf/decav91C0/0.25',
            },
        }
        filenames = {
            'temp': {
                'ann': 'woa23_decav91C0_t00_04.nc',
                'jan': 'woa23_decav91C0_t01_04.nc',
            },
            'salin': {
                'ann': 'woa23_decav91C0_s00_04.nc',
                'jan': 'woa23_decav91C0_s01_04.nc',
            },
        }

        for field in ['temp', 'salin']:
            for season in ['jan', 'ann']:
                woa_filename = filenames[field][season]
                woa_dir = directories[field][season]
                self.add_input_file(
                    filename=f'woa_{field}_{season}.nc',
                    target=woa_filename,
                    database='initial_condition_database',
                    url=f'{base_url}/{woa_dir}/{woa_filename}',
                )

    def run(self):
        """
        Combine January and annual climatologies and derive conservative
        temperature and absolute salinity.
        """
        logger = self.logger
        logger.info('Combining January and annual WOA23 climatologies')

        ds_out = self._combine_woa_climatologies()
        ds_out = self._to_canonical_teos10(ds_out)
        write_netcdf(ds_out, 'woa_combined.nc')
        logger.info('Wrote woa_combined.nc')

    def run_with_dask(self, client, resources):
        """
        Combine January and annual climatologies using Dask for TEOS-10
        conversion by depth slice.

        Parameters
        ----------
        client : distributed.Client
            The Dask client for the active ``polaris run`` lifecycle.

        resources : polaris.run.resources.StepResourceLease
            The resources assigned to the step.
        """
        logger = self.logger
        logger.info('Combining January and annual WOA23 climatologies')
        logger.info(
            f'Using Dask for TEOS-10 conversion with '
            f'{resources.workers} workers'
        )

        ds_out = self._combine_woa_climatologies()
        ds_out = self._to_canonical_teos10_with_dask(ds_out, client)
        write_netcdf(ds_out, 'woa_combined.nc')
        logger.info('Wrote woa_combined.nc')

    @staticmethod
    def _combine_woa_climatologies():
        """
        Combine January and annual WOA23 climatologies.

        Returns
        -------
        ds_out : xarray.Dataset
            Combined in-situ temperature and practical salinity.
        """
        with xr.open_dataset('woa_temp_ann.nc', decode_times=False) as ds_temp:
            ds_out = xr.Dataset()
            for var in ['lon', 'lat', 'depth']:
                ds_out[var] = ds_temp[var]
                ds_out[f'{var}_bnds'] = ds_temp[f'{var}_bnds']

        var_map = {'temp': 't_an', 'salin': 's_an'}
        for field, var_name in var_map.items():
            with xr.open_dataset(
                f'woa_{field}_ann.nc', decode_times=False
            ) as ds_ann:
                ds_ann = ds_ann.isel(time=0, drop=True)
                with xr.open_dataset(
                    f'woa_{field}_jan.nc', decode_times=False
                ) as ds_jan:
                    ds_jan = ds_jan.isel(time=0, drop=True)
                    slices = []
                    for depth_index in range(ds_ann.sizes['depth']):
                        if depth_index < ds_jan.sizes['depth']:
                            ds = ds_jan
                        else:
                            ds = ds_ann
                        slices.append(ds[var_name].isel(depth=depth_index))

                    ds_out[var_name] = xr.concat(slices, dim='depth')
                    ds_out[var_name].attrs = ds_ann[var_name].attrs

        return ds_out

    @staticmethod
    def _to_canonical_teos10(ds):
        """
        Convert WOA in-situ temperature and practical salinity to canonical
        conservative temperature and absolute salinity.

        Parameters
        ----------
        ds : xarray.Dataset
            A combined WOA dataset with in-situ temperature and salinity.

        Returns
        -------
        ds : xarray.Dataset
            The dataset with conservative temperature and absolute salinity.
        """
        dims = ds.t_an.dims
        ct_slices = []
        sa_slices = []
        for depth_index in range(ds.sizes['depth']):
            temp_slice = ds.t_an.isel(depth=depth_index)
            conservative_temp, absolute_salinity = (
                CombineStep._to_canonical_teos10_slice(
                    depth=ds.depth.isel(depth=depth_index).values,
                    temp_slice=temp_slice.values,
                    salinity_slice=ds.s_an.isel(depth=depth_index).values,
                    lat=ds.lat.broadcast_like(temp_slice).values,
                    lon=ds.lon.broadcast_like(temp_slice).values,
                )
            )
            ct_slices.append(
                xr.DataArray(
                    data=conservative_temp,
                    dims=temp_slice.dims,
                    attrs=temp_slice.attrs,
                )
            )
            sa_slices.append(
                xr.DataArray(
                    data=absolute_salinity,
                    dims=temp_slice.dims,
                    attrs=ds.s_an.attrs,
                )
            )

        return CombineStep._finish_canonical_teos10(
            ds, ct_slices, sa_slices, dims
        )

    @staticmethod
    def _to_canonical_teos10_with_dask(ds, client):
        """
        Convert WOA in-situ temperature and practical salinity using Dask.

        Parameters
        ----------
        ds : xarray.Dataset
            A combined WOA dataset with in-situ temperature and salinity.

        client : distributed.Client
            The Dask client for the active ``polaris run`` lifecycle.

        Returns
        -------
        ds : xarray.Dataset
            The dataset with conservative temperature and absolute salinity.
        """
        dims = ds.t_an.dims
        futures = []
        for depth_index in range(ds.sizes['depth']):
            temp_slice = ds.t_an.isel(depth=depth_index)
            futures.append(
                client.submit(
                    CombineStep._to_canonical_teos10_slice,
                    depth=ds.depth.isel(depth=depth_index).values,
                    temp_slice=temp_slice.values,
                    salinity_slice=ds.s_an.isel(depth=depth_index).values,
                    lat=ds.lat.broadcast_like(temp_slice).values,
                    lon=ds.lon.broadcast_like(temp_slice).values,
                )
            )

        ct_slices = []
        sa_slices = []
        for depth_index, (conservative_temp, absolute_salinity) in enumerate(
            client.gather(futures)
        ):
            temp_slice = ds.t_an.isel(depth=depth_index)
            ct_slices.append(
                xr.DataArray(
                    data=conservative_temp,
                    dims=temp_slice.dims,
                    attrs=temp_slice.attrs,
                )
            )
            sa_slices.append(
                xr.DataArray(
                    data=absolute_salinity,
                    dims=temp_slice.dims,
                    attrs=ds.s_an.attrs,
                )
            )

        return CombineStep._finish_canonical_teos10(
            ds, ct_slices, sa_slices, dims
        )

    @staticmethod
    def _to_canonical_teos10_slice(
        depth, temp_slice, salinity_slice, lat, lon
    ):
        """
        Convert one WOA depth slice to conservative temperature and absolute
        salinity.

        Parameters
        ----------
        depth : float
            Depth of the slice.

        temp_slice : numpy.ndarray
            In-situ temperature for the depth slice.

        salinity_slice : numpy.ndarray
            Practical salinity for the depth slice.

        lat : numpy.ndarray
            Latitude broadcast to the slice shape.

        lon : numpy.ndarray
            Longitude broadcast to the slice shape.

        Returns
        -------
        conservative_temp : numpy.ndarray
            Conservative temperature for the depth slice.

        absolute_salinity : numpy.ndarray
            Absolute salinity for the depth slice.
        """
        pressure = gsw.p_from_z(-depth, lat)

        mask = np.isfinite(temp_slice) & np.isfinite(salinity_slice)
        conservative_temp = np.full(temp_slice.shape, np.nan)
        absolute_salinity = np.full(salinity_slice.shape, np.nan)
        absolute_salinity[mask] = gsw.SA_from_SP(
            salinity_slice[mask],
            pressure[mask],
            lon[mask],
            lat[mask],
        )
        conservative_temp[mask] = gsw.CT_from_t(
            absolute_salinity[mask],
            temp_slice[mask],
            pressure[mask],
        )
        return conservative_temp, absolute_salinity

    @staticmethod
    def _finish_canonical_teos10(ds, ct_slices, sa_slices, dims):
        """
        Add converted TEOS-10 fields to a WOA dataset.

        Parameters
        ----------
        ds : xarray.Dataset
            Dataset with in-situ temperature and practical salinity.

        ct_slices : list of xarray.DataArray
            Conservative-temperature depth slices.

        sa_slices : list of xarray.DataArray
            Absolute-salinity depth slices.

        dims : tuple
            Original dimension order for the WOA fields.

        Returns
        -------
        ds : xarray.Dataset
            Dataset with TEOS-10 fields replacing the original WOA fields.
        """
        ds['ct_an'] = xr.concat(ct_slices, dim='depth').transpose(*dims)
        ds.ct_an.attrs['standard_name'] = 'sea_water_conservative_temperature'
        ds.ct_an.attrs['long_name'] = (
            'Objectively analyzed mean fields for '
            'sea_water_conservative_temperature at standard depth levels.'
        )
        ds['sa_an'] = xr.concat(sa_slices, dim='depth').transpose(*dims)
        ds.sa_an.attrs['standard_name'] = 'sea_water_absolute_salinity'
        ds.sa_an.attrs['long_name'] = (
            'Objectively analyzed mean fields for '
            'sea_water_absolute_salinity at standard depth levels.'
        )
        ds.sa_an.attrs['units'] = 'g kg-1'

        return ds.drop_vars(['t_an', 's_an'])
