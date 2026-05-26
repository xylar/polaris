import os

import numpy as np
import xarray as xr

from polaris.validate import (
    check_variables_finite,
    compare_variables,
    write_output_validation_log,
)

_TRACERS_TO_CHECK = (
    'temperature',
    'salinity',
    'tracer1',
    'tracer2',
    'tracer3',
)


def validate_ocean_outputs(step):
    """
    Check ocean output variables for NaN and Inf values.

    Parameters
    ----------
    step : polaris.Step
        The ocean step whose outputs should be checked

    Returns
    -------
    checked : bool
        Whether output validation was performed

    success : bool
        Whether all checked variables were finite
    """
    if step.work_dir is None:
        raise ValueError(
            'The work directory must be set before the step outputs can '
            'be validated.'
        )

    filenames = _get_validation_filenames(
        step.validate_vars, step.properties_to_check
    )
    checked = False
    success = True
    failed_vars = []
    passed_vars = []

    for filename in filenames:
        filename = str(filename)
        output_filename = os.path.join(step.work_dir, filename)
        if not os.path.exists(output_filename):
            step.logger.error(f'File {output_filename} does not exist.')
            success = False
            continue

        ds = step.component.open_model_dataset(output_filename, step.config)
        ds_vert_coord = None
        try:
            variables = get_output_validation_variables(
                validate_vars=step.validate_vars.get(filename, []),
                properties_to_check=step.properties_to_check.get(filename, []),
                ds=ds,
            )
            if len(variables) == 0:
                continue

            ds_vert_coord = _open_vert_coord_dataset(
                step=step,
                work_dir=step.work_dir,
                output_filename=output_filename,
                output_key=filename,
                ds=ds,
            )
            mask_callback = get_ocean_validation_mask_callback(ds_vert_coord)
            checked = True
            result = check_variables_finite(
                variables=variables,
                filename=output_filename,
                ds=ds,
                logger=step.logger,
                mask_callback=mask_callback,
            )
            success = success and result
            if result:
                passed_vars.extend(
                    [f'{filename}: {variable}' for variable in variables]
                )
            else:
                failed_vars.extend(
                    [f'{filename}: {variable}' for variable in variables]
                )
        finally:
            if ds_vert_coord is not None and ds_vert_coord is not ds:
                ds_vert_coord.close()
            ds.close()

    write_output_validation_log(
        work_dir=step.work_dir,
        checked=checked,
        success=success,
        passed_vars=passed_vars,
        failed_vars=failed_vars,
    )
    return checked, success


def validate_ocean_baselines(step):
    """
    Compare ocean variables to a baseline, checking finite values first.

    Parameters
    ----------
    step : polaris.Step
        The ocean step whose baselines should be checked

    Returns
    -------
    compared : bool
        Whether a baseline comparison was performed

    success : bool
        Whether the baseline comparison passed
    """
    if step.work_dir is None:
        raise ValueError(
            'The work directory must be set before the step '
            'outputs can be validated against baselines.'
        )

    compared = False
    success = True
    if step.baseline_dir is not None:
        failed_vars = []
        for filename, variables in step.validate_vars.items():
            filename = str(filename)

            this_filename = os.path.join(step.work_dir, filename)
            baseline_filename = os.path.join(step.baseline_dir, filename)

            current_vert_coord = None
            baseline_vert_coord = None
            try:
                current_vert_coord = _open_vert_coord_dataset(
                    step=step,
                    work_dir=step.work_dir,
                    output_filename=this_filename,
                    output_key=filename,
                )
                baseline_vert_coord = _open_vert_coord_dataset(
                    step=step,
                    work_dir=step.baseline_dir,
                    output_filename=baseline_filename,
                    output_key=filename,
                )
                result = compare_variables(
                    step.component,
                    variables,
                    this_filename,
                    baseline_filename,
                    logger=step.logger,
                    config=step.config,
                    check_finite=True,
                    mask_callback1=get_ocean_validation_mask_callback(
                        current_vert_coord
                    ),
                    mask_callback2=get_ocean_validation_mask_callback(
                        baseline_vert_coord
                    ),
                )
            finally:
                if current_vert_coord is not None:
                    current_vert_coord.close()
                if baseline_vert_coord is not None:
                    baseline_vert_coord.close()

            success = success and result
            compared = True
            if not result:
                failed_vars.extend(variables)

        if compared and success:
            log_filename = os.path.join(step.work_dir, 'baseline_passed.log')
            with open(log_filename, 'w') as result_log_file:
                result_log_file.write(
                    'All variables passed baseline comparison.\n'
                )
        elif compared and not success:
            log_filename = os.path.join(step.work_dir, 'baseline_failed.log')
            failed_vars_str = '\n  '.join(failed_vars)
            with open(log_filename, 'w') as result_log_file:
                result_log_file.write(
                    f'Baseline comparison failed for:.\n {failed_vars_str}\n'
                )

    return compared, success


def get_output_validation_variables(validate_vars, properties_to_check, ds):
    """
    Get the ordered union of variables needed for ocean output validation.

    ``validate_vars`` are listed first, followed by conservation-property
    dependencies that were not already requested.
    """
    variables: list[str] = []
    for variable in validate_vars:
        _append_unique(variables, variable)

    for output_property in properties_to_check:
        for variable in _property_validation_variables(output_property, ds):
            _append_unique(variables, variable)

    return variables


def get_ocean_validation_mask_callback(ds_vert_coord):
    """
    Build a callback that masks invalid ocean vertical levels.

    Parameters
    ----------
    ds_vert_coord : xarray.Dataset
        A dataset containing ``minLevelCell`` and ``maxLevelCell``

    Returns
    -------
    callback : callable
        A callback compatible with :mod:`polaris.validate`
    """

    def callback(variable, da, ds, filename, logger):
        if not _needs_vertical_mask(da):
            return None

        if not _has_vertical_bounds(ds_vert_coord):
            logger.error(
                f'Variable {variable} in {filename} needs ocean vertical '
                f'masking but the supplied vertical-coordinate dataset does '
                f'not provide minLevelCell and maxLevelCell.'
            )
            return None, False

        levels = xr.DataArray(
            np.arange(da.sizes['nVertLevels']), dims=('nVertLevels',)
        )
        min_level = ds_vert_coord['minLevelCell'] - 1
        max_level = ds_vert_coord['maxLevelCell'] - 1
        return (levels >= min_level) & (levels <= max_level)

    return callback


def _open_vert_coord_dataset(
    step,
    work_dir,
    output_filename,
    output_key,
    ds=None,
):
    """Open the dataset used as the source of ocean vertical bounds."""
    vert_coord_filename = _get_vert_coord_filename(step, output_key)
    vert_coord_path = os.path.join(work_dir, vert_coord_filename)

    if ds is not None and os.path.abspath(vert_coord_path) == os.path.abspath(
        output_filename
    ):
        return ds

    if not os.path.exists(vert_coord_path):
        return xr.Dataset()

    return step.component.open_model_dataset(vert_coord_path, step.config)


def _get_vert_coord_filename(step, output_key):
    """Get the local vertical-coordinate filename for an ocean step."""
    model = step.config.get('ocean', 'model')
    if model == 'omega':
        if hasattr(step, 'get_vert_coord_filename'):
            return step.get_vert_coord_filename()
        return 'vert_coord.nc'

    if hasattr(step, 'get_init_filename'):
        return step.get_init_filename()

    return output_key


def _get_validation_filenames(validate_vars, properties_to_check):
    """Get output filenames in deterministic validation order."""
    filenames: list[str] = []
    for filename in validate_vars:
        _append_unique(filenames, str(filename))
    for filename in properties_to_check:
        _append_unique(filenames, str(filename))
    return filenames


def _append_unique(items, item):
    """Append an item only if it is not already present."""
    if item not in items:
        items.append(item)


def _property_validation_variables(output_property, ds):
    """Variables required by an ocean conservation property check."""
    if output_property == 'mass conservation':
        return ['layerThickness']
    if output_property == 'salt conservation':
        return ['layerThickness', 'salinity']
    if output_property == 'energy conservation':
        return ['layerThickness', 'temperature']
    if output_property == 'tracer conservation':
        variables = ['layerThickness']
        if ds is not None:
            variables.extend(
                tracer for tracer in _TRACERS_TO_CHECK if tracer in ds
            )
        return variables
    if output_property.startswith('tracer conservation-'):
        return ['layerThickness', output_property.split('-', maxsplit=1)[1]]
    return []


def _needs_vertical_mask(da):
    """Whether an ocean variable should be masked by vertical bounds."""
    return 'nCells' in da.dims and 'nVertLevels' in da.dims


def _has_vertical_bounds(ds):
    """Whether a dataset provides ocean vertical topography bounds."""
    return 'minLevelCell' in ds and 'maxLevelCell' in ds
