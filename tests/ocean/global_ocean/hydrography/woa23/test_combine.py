import logging
from types import SimpleNamespace

import gsw
import numpy as np
import pytest
import xarray as xr
from distributed import Client, LocalCluster

from polaris.tasks.ocean.global_ocean.hydrography.woa23.combine import (
    CombineStep,
)
from polaris.tasks.ocean.global_ocean.hydrography.woa23.viz import (
    Woa23VizStep,
)


def test_to_canonical_teos10():
    ds = xr.Dataset(
        data_vars={
            't_an': (
                ('depth', 'lat', 'lon'),
                np.array(
                    [
                        [[1.0, 2.0], [3.0, np.nan]],
                        [[0.5, 1.5], [2.5, 3.5]],
                    ]
                ),
            ),
            's_an': (
                ('depth', 'lat', 'lon'),
                np.array(
                    [
                        [[34.5, 34.7], [34.9, 35.1]],
                        [[34.6, 34.8], [35.0, np.nan]],
                    ]
                ),
            ),
        },
        coords={
            'depth': ('depth', np.array([0.0, 1000.0])),
            'lat': ('lat', np.array([-45.0, 10.0])),
            'lon': ('lon', np.array([20.0, 160.0])),
        },
    )

    ds_out = CombineStep._to_canonical_teos10(ds)

    assert 't_an' not in ds_out
    assert 's_an' not in ds_out
    assert ds_out.ct_an.attrs['standard_name'] == (
        'sea_water_conservative_temperature'
    )
    assert ds_out.sa_an.attrs['standard_name'] == (
        'sea_water_absolute_salinity'
    )
    assert ds_out.sa_an.attrs['units'] == 'g kg-1'

    expected_ct = np.full(ds_out.ct_an.shape, np.nan)
    expected_sa = np.full(ds_out.sa_an.shape, np.nan)
    for depth_index, depth in enumerate(ds.depth.values):
        temp = ds.t_an.isel(depth=depth_index).values
        practical_salinity = ds.s_an.isel(depth=depth_index).values
        level = ds.t_an.isel(depth=depth_index)
        lat = ds.lat.broadcast_like(level).values
        lon = ds.lon.broadcast_like(level).values
        pressure = gsw.p_from_z(-depth, lat)
        mask = np.isfinite(temp) & np.isfinite(practical_salinity)

        expected_sa[depth_index, :, :][mask] = gsw.SA_from_SP(
            practical_salinity[mask],
            pressure[mask],
            lon[mask],
            lat[mask],
        )
        expected_ct[depth_index, :, :][mask] = gsw.CT_from_t(
            expected_sa[depth_index, :, :][mask],
            temp[mask],
            pressure[mask],
        )

    assert ds_out.ct_an.values == pytest.approx(expected_ct, nan_ok=True)
    assert ds_out.sa_an.values == pytest.approx(expected_sa, nan_ok=True)


def test_run_with_dask(tmp_path, monkeypatch):
    _write_synthetic_woa_files(tmp_path)
    monkeypatch.chdir(tmp_path)

    expected = CombineStep._to_canonical_teos10(
        CombineStep._combine_woa_climatologies()
    )

    step = CombineStep(component=SimpleNamespace(name='ocean'), subdir='woa23')
    step.logger = logging.getLogger('test_woa23_combine')
    resources = SimpleNamespace(workers=2)

    cluster = LocalCluster(
        n_workers=2,
        threads_per_worker=1,
        processes=False,
        dashboard_address=None,
    )
    client = Client(cluster)
    try:
        step.run_with_dask(client, resources)
    finally:
        client.close()
        cluster.close()

    with xr.open_dataset('woa_combined.nc') as actual:
        xr.testing.assert_allclose(actual.ct_an, expected.ct_an)
        xr.testing.assert_allclose(actual.sa_an, expected.sa_an)
        assert actual.ct_an.attrs == expected.ct_an.attrs
        assert actual.sa_an.attrs == expected.sa_an.attrs


def test_depth_bounds_depth_major():
    depth_bounds = xr.DataArray(
        np.array([[0.0, 100.0], [100.0, 300.0], [300.0, 600.0]]),
        dims=('depth', 'nbounds'),
    )

    bounds = Woa23VizStep._depth_bounds(depth_bounds)

    assert bounds == pytest.approx([0.0, 100.0, 300.0, 600.0])


def test_depth_bounds_bounds_major():
    depth_bounds = xr.DataArray(
        np.array([[0.0, 100.0, 300.0], [100.0, 300.0, 600.0]]),
        dims=('nbounds', 'depth'),
    )

    bounds = Woa23VizStep._depth_bounds(depth_bounds)

    assert bounds == pytest.approx([0.0, 100.0, 300.0, 600.0])


def test_add_periodic_lon_keeps_non_lon_variables_1d():
    ds = xr.Dataset(
        data_vars={
            'ct_an': (
                ('depth', 'lat', 'lon'),
                np.arange(12, dtype=float).reshape(2, 2, 3),
            ),
            'depth_bnds': (
                ('depth', 'nbounds'),
                np.array([[0.0, 100.0], [100.0, 300.0]]),
            ),
        },
        coords={
            'depth': ('depth', np.array([50.0, 200.0])),
            'lat': ('lat', np.array([-75.0, -74.0])),
            'lon': ('lon', np.array([-10.0, 0.0, 10.0])),
            'nbounds': ('nbounds', np.array([0, 1])),
        },
    )

    ds_periodic = Woa23VizStep._add_periodic_lon(ds)

    assert ds_periodic.sizes['lon'] == 9
    assert ds_periodic.depth_bnds.dims == ('depth', 'nbounds')
    assert ds_periodic.depth_bnds.values == pytest.approx(ds.depth_bnds.values)
    assert Woa23VizStep._depth_bounds(ds_periodic.depth_bnds) == pytest.approx(
        [0.0, 100.0, 300.0]
    )


def _write_synthetic_woa_files(tmp_path):
    lon = np.array([20.0, 160.0])
    lat = np.array([-45.0, 10.0])
    depth_ann = np.array([0.0, 500.0, 1000.0])
    depth_jan = depth_ann[:2]

    datasets = {
        'woa_temp_ann.nc': _synthetic_woa_dataset('t_an', depth_ann, 1.0),
        'woa_temp_jan.nc': _synthetic_woa_dataset('t_an', depth_jan, 2.0),
        'woa_salin_ann.nc': _synthetic_woa_dataset('s_an', depth_ann, 34.0),
        'woa_salin_jan.nc': _synthetic_woa_dataset('s_an', depth_jan, 35.0),
    }

    for filename, ds in datasets.items():
        ds = ds.assign_coords(
            lon=('lon', lon[: ds.sizes['lon']]),
            lat=('lat', lat[: ds.sizes['lat']]),
        )
        ds.to_netcdf(tmp_path / filename)


def _synthetic_woa_dataset(var_name, depth, offset):
    data = offset + np.arange(depth.size * 4, dtype=float).reshape(
        1, depth.size, 2, 2
    )
    data[0, -1, 1, 1] = np.nan
    depth_bnds = np.column_stack((depth, depth + 100.0))

    return xr.Dataset(
        data_vars={
            var_name: (('time', 'depth', 'lat', 'lon'), data),
            'lon_bnds': (
                ('lon', 'nbounds'),
                np.array([[19.5, 20.5], [159.5, 160.5]]),
            ),
            'lat_bnds': (
                ('lat', 'nbounds'),
                np.array([[-45.5, -44.5], [9.5, 10.5]]),
            ),
            'depth_bnds': (('depth', 'nbounds'), depth_bnds),
        },
        coords={
            'time': ('time', np.array([0.0])),
            'depth': ('depth', depth),
            'lat': ('lat', np.array([-45.0, 10.0])),
            'lon': ('lon', np.array([20.0, 160.0])),
            'nbounds': ('nbounds', np.array([0, 1])),
        },
    )
