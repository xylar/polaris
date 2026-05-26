import logging
from types import SimpleNamespace

import numpy as np
import xarray as xr

from polaris.ocean.validation import (
    get_ocean_validation_mask_callback,
    get_output_validation_variables,
    validate_ocean_baselines,
    validate_ocean_outputs,
)
from polaris.run.serial import (
    _default_validation_statuses,
    _validation_status_failed,
)
from polaris.validate import check_variables_finite


def test_generic_finite_check_fails_nan_without_mask(tmp_path):
    filename = tmp_path / 'output.nc'
    ds = xr.Dataset(data_vars=dict(field=('x', np.array([1.0, np.nan]))))

    result = check_variables_finite(
        variables=['field'],
        filename=str(filename),
        ds=ds,
        logger=logging.getLogger('polaris.tests.validate'),
    )

    assert not result


def test_generic_finite_check_applies_mask(tmp_path):
    filename = tmp_path / 'output.nc'
    ds = xr.Dataset(data_vars=dict(field=('x', np.array([1.0, np.nan]))))

    def mask_callback(variable, da, ds, filename, logger):
        return xr.DataArray(data=np.array([True, False]), dims=('x',))

    result = check_variables_finite(
        variables=['field'],
        filename=str(filename),
        ds=ds,
        logger=logging.getLogger('polaris.tests.validate'),
        mask_callback=mask_callback,
    )

    assert result


def test_output_validation_variable_union_deduplicates_in_order():
    ds = xr.Dataset(
        data_vars=dict(
            salinity=('nCells', np.ones(1)),
            layerThickness=('nCells', np.ones(1)),
            temperature=('nCells', np.ones(1)),
            tracer1=('nCells', np.ones(1)),
            tracer3=('nCells', np.ones(1)),
        )
    )

    variables = get_output_validation_variables(
        validate_vars=['salinity', 'layerThickness'],
        properties_to_check=[
            'mass conservation',
            'salt conservation',
            'energy conservation',
            'tracer conservation',
        ],
        ds=ds,
    )

    assert variables == [
        'salinity',
        'layerThickness',
        'temperature',
        'tracer1',
        'tracer3',
    ]


def test_ocean_mask_uses_ds_vert_coord_not_output_dataset(tmp_path):
    data = _layer_thickness_data()
    data[0, 0, 0] = np.nan
    data[0, 0, 2] = np.inf
    _write_dataset(
        tmp_path / 'output.nc',
        layer_thickness=data,
        include_topography=False,
    )
    _write_vert_coord(tmp_path / 'vert_coord.nc')

    step = _make_ocean_step(tmp_path, validate_vars=['layerThickness'])

    checked, success = validate_ocean_outputs(step)

    assert checked
    assert success
    assert (tmp_path / 'output_validation_passed.log').exists()


def test_ocean_cell_3d_mask_fails_nan_inside_valid_levels(tmp_path):
    data = _layer_thickness_data()
    data[0, 0, 1] = np.nan
    _write_dataset(
        tmp_path / 'output.nc',
        layer_thickness=data,
        include_topography=False,
    )
    _write_vert_coord(tmp_path / 'vert_coord.nc')

    step = _make_ocean_step(tmp_path, validate_vars=['layerThickness'])

    checked, success = validate_ocean_outputs(step)

    assert checked
    assert not success
    assert (tmp_path / 'output_validation_failed.log').exists()


def test_ocean_cell_3d_missing_vert_coord_bounds_fails(tmp_path):
    _write_dataset(tmp_path / 'output.nc', include_topography=False)
    xr.Dataset().to_netcdf(tmp_path / 'vert_coord.nc')

    step = _make_ocean_step(tmp_path, validate_vars=['layerThickness'])

    checked, success = validate_ocean_outputs(step)

    assert checked
    assert not success


def test_edge_centered_variables_are_fully_checked(tmp_path):
    normal_velocity = np.ones((1, 2, 3))
    normal_velocity[0, 0, 0] = np.nan
    _write_dataset(
        tmp_path / 'output.nc',
        normal_velocity=normal_velocity,
        include_topography=False,
    )
    _write_vert_coord(tmp_path / 'vert_coord.nc')

    step = _make_ocean_step(tmp_path, validate_vars=['normalVelocity'])

    checked, success = validate_ocean_outputs(step)

    assert checked
    assert not success


def test_property_only_variable_is_scanned(tmp_path):
    data = _layer_thickness_data()
    data[0, 0, 1] = np.nan
    _write_dataset(
        tmp_path / 'output.nc',
        layer_thickness=data,
        include_topography=False,
    )
    _write_vert_coord(tmp_path / 'vert_coord.nc')

    step = _make_ocean_step(
        tmp_path,
        validate_vars=None,
        check_properties=['mass conservation'],
    )

    checked, success = validate_ocean_outputs(step)

    assert checked
    assert not success


def test_baseline_validation_fails_on_valid_nan(tmp_path):
    baseline_dir = tmp_path / 'baseline'
    baseline_dir.mkdir()

    _write_dataset(tmp_path / 'output.nc', include_topography=False)
    _write_vert_coord(tmp_path / 'vert_coord.nc')
    baseline_data = _layer_thickness_data()
    baseline_data[0, 0, 1] = np.nan
    _write_dataset(
        baseline_dir / 'output.nc',
        layer_thickness=baseline_data,
        include_topography=False,
    )
    _write_vert_coord(baseline_dir / 'vert_coord.nc')

    step = _make_ocean_step(tmp_path, validate_vars=['layerThickness'])
    step.baseline_dir = str(baseline_dir)

    compared, success = validate_ocean_baselines(step)

    assert compared
    assert not success
    assert (tmp_path / 'baseline_failed.log').exists()


def test_ocean_validation_mask_callback_fails_without_bounds():
    ds = xr.Dataset()
    da = xr.DataArray(
        data=np.ones((1, 1, 1)), dims=('Time', 'nCells', 'nVertLevels')
    )
    callback = get_ocean_validation_mask_callback(ds)

    mask, success = callback(
        variable='layerThickness',
        da=da,
        ds=xr.Dataset(),
        filename='output.nc',
        logger=logging.getLogger('polaris.tests.validate'),
    )

    assert mask is None
    assert not success


def test_validation_status_failures_affect_task_result():
    statuses = _default_validation_statuses()
    assert not _validation_status_failed(statuses)

    statuses['output_validation'] = False
    assert _validation_status_failed(statuses)

    statuses = _default_validation_statuses()
    statuses['property'] = False
    assert _validation_status_failed(statuses)


def _make_ocean_step(tmp_path, validate_vars, check_properties=None):
    step = SimpleNamespace()
    step.component = SimpleNamespace(open_model_dataset=_open_model_dataset)
    step.work_dir = str(tmp_path)
    step.baseline_dir = None
    step.config = _Config()
    step.logger = logging.getLogger('polaris.tests.validate')
    step.validate_vars = dict()
    step.properties_to_check = dict()
    step.get_vert_coord_filename = lambda: 'vert_coord.nc'
    if validate_vars is not None:
        step.validate_vars['output.nc'] = validate_vars
    if check_properties is not None:
        step.properties_to_check['output.nc'] = check_properties
    return step


def _open_model_dataset(filename, config, **kwargs):
    return xr.open_dataset(filename, **kwargs)


def _write_dataset(
    filename,
    layer_thickness=None,
    normal_velocity=None,
    include_topography=True,
):
    ds = xr.Dataset()
    if include_topography:
        ds = _vert_coord_dataset()
    if layer_thickness is None:
        layer_thickness = _layer_thickness_data()
    ds['layerThickness'] = (
        ('Time', 'nCells', 'nVertLevels'),
        layer_thickness,
    )
    if normal_velocity is not None:
        ds['normalVelocity'] = (
            ('Time', 'nEdges', 'nVertLevels'),
            normal_velocity,
        )
    ds.to_netcdf(filename)


def _write_vert_coord(filename):
    _vert_coord_dataset().to_netcdf(filename)


def _vert_coord_dataset():
    return xr.Dataset(
        data_vars=dict(
            minLevelCell=('nCells', np.array([2, 1])),
            maxLevelCell=('nCells', np.array([2, 2])),
        )
    )


def _layer_thickness_data():
    return np.ones((1, 2, 3))


class _Config:
    def get(self, section, option):
        assert section == 'ocean'
        assert option == 'model'
        return 'omega'
