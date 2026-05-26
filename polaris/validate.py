import os

import numpy as np
import xarray as xr


def compare_variables(
    component,
    variables,
    filename1,
    filename2,
    logger,
    config,
    l1_norm=0.0,
    l2_norm=0.0,
    linf_norm=0.0,
    quiet=True,
    ds1=None,
    ds2=None,
    check_finite=False,
    mask_callback1=None,
    mask_callback2=None,
):
    """
    compare variables in the two files

    Parameters
    ----------
    variables : list
        A list of variable names to compare

    filename1 : str
        The relative path to a file within the ``work_dir``.  If ``filename2``
        is also given, comparison will be performed with ``variables`` in that
        file.  If a baseline directory was provided when setting up the
        test case, the ``variables`` will be compared between this test case
        and the same relative filename in the baseline version of the test
        case.

    filename2 : str
        The relative path to another file within the ``work_dir`` if comparing
        between files within the current test case.  If a baseline directory
        was provided, the ``variables`` from this file will also be compared
        with those in the corresponding baseline file.

    logger: logging.Logger
        The logger to log validation output to

    config : polaris.config.PolarisConfigParser
        Configuration for the task; forwarded to
        ``component.open_model_dataset()`` when datasets are not pre-loaded
        via ``ds1``/``ds2``.

    l1_norm : float, optional
        The maximum allowed L1 norm difference between the variables in
        ``filename1`` and ``filename2``.  To skip L1 norm check, pass None.

    l2_norm : float, optional
        The maximum allowed L2 norm difference between the variables in
        ``filename1`` and ``filename2``.  To skip L2 norm check, pass None.

    linf_norm : float, optional
        The maximum allowed L-Infinity norm difference between the variables in
        ``filename1`` and ``filename2``.  To skip Linf norm check, pass None.

    quiet : bool, optional
        Whether to print detailed information.  If quiet is False, the norm
        tolerance values being compared against will be printed when the
        comparison is made.  This is generally desirable when using nonzero
        norm tolerance values.

    ds1 : xarray.Dataset, optional
        A dataset loaded from filename1.  This may save time if the dataset is
        already loaded and allows for calculations to be performed or variables
        to be renamed if necessary.

    ds2 : xarray.Dataset, optional
        A dataset loaded from filename2.  This may save time if the dataset is
        already loaded and allows for calculations to be performed or variables
        to be renamed if necessary.

    check_finite : bool, optional
        Whether to fail if variables contain NaN or Inf values before
        computing norms.

    mask_callback1, mask_callback2 : callable, optional
        Functions that return a mask for a variable in ``filename1`` or
        ``filename2``.  Each callback is called with
        ``(variable, da, ds, filename, logger)`` and may return ``None``, a
        mask ``DataArray``, or ``(mask, success)``.

    Returns
    -------
    all_pass : bool
        Whether all variables passed the validation checks

    """

    for filename in [filename1, filename2]:
        if not os.path.exists(filename):
            logger.error(f'File {filename} does not exist.')
            return False

    if ds1 is None:
        ds1 = component.open_model_dataset(filename1, config)

    if ds2 is None:
        ds2 = component.open_model_dataset(filename2, config)

    all_pass = True

    for variable in variables:
        if not _all_found(ds1, filename1, ds2, filename2, variable, logger):
            all_pass = False
            continue

        da1 = ds1[variable]
        da2 = ds2[variable]

        da1, result1 = _validate_and_mask_variable(
            da=da1,
            variable=variable,
            filename=filename1,
            ds=ds1,
            logger=logger,
            check_finite=check_finite,
            mask_callback=mask_callback1,
        )
        all_pass = all_pass and result1
        da2, result2 = _validate_and_mask_variable(
            da=da2,
            variable=variable,
            filename=filename2,
            ds=ds2,
            logger=logger,
            check_finite=check_finite,
            mask_callback=mask_callback2,
        )
        all_pass = all_pass and result2
        if not (result1 and result2):
            continue

        if not np.all(da1.dims == da2.dims):
            logger.error(
                f"Dimensions for variable {variable} don't match "
                f'between files {filename1} and {filename2}.'
            )
            all_pass = False
            continue

        if not _all_sizes_match(
            da1, filename1, da2, filename2, variable, logger
        ):
            all_pass = False
            continue

        if not quiet:
            print('    Pass thresholds are:')
            if l1_norm is not None:
                print(f'       L1: {l1_norm:16.14e}')
            if l2_norm is not None:
                print(f'       L2: {l2_norm:16.14e}')
            if linf_norm is not None:
                print(f'       L_Infinity: {linf_norm:16.14e}')
        variable_pass = True
        if 'Time' in da1.dims:
            time_range = range(0, da1.sizes['Time'])
            time_str = ', '.join([f'{j}' for j in time_range])
            print(f'{variable.ljust(20)} Time index: {time_str}')
            for time_index in time_range:
                slice1 = da1.isel(Time=time_index)
                slice2 = da2.isel(Time=time_index)
                result = _compute_norms(
                    slice1,
                    slice2,
                    quiet,
                    l1_norm,
                    l2_norm,
                    linf_norm,
                    time_index=time_index,
                )
                variable_pass = variable_pass and result

        else:
            print(f'{variable}')
            result = _compute_norms(
                da1, da2, quiet, l1_norm, l2_norm, linf_norm
            )
            variable_pass = variable_pass and result

        # ANSI fail text: https://stackoverflow.com/a/287944/7728169
        start_fail = '\033[91m'
        start_pass = '\033[92m'
        end = '\033[0m'
        pass_str = f'{start_pass}PASS{end}'
        fail_str = f'{start_fail}FAIL{end}'

        if variable_pass:
            print(f'  {pass_str} {filename1}\n')
        else:
            print(f'  {fail_str} {filename1}\n')
        print(f'       {filename2}\n')
        all_pass = all_pass and variable_pass

    return all_pass


def validate_output_files(
    component,
    variables_by_file,
    work_dir,
    logger,
    config,
    mask_callback_builder=None,
):
    """
    Check output variables for NaN and Inf values.

    Parameters
    ----------
    component : polaris.Component
        The component this step belongs to

    variables_by_file : dict
        Variables requested for validation by output filename

    work_dir : str
        The step work directory

    logger: logging.Logger
        The logger to log validation output to

    config : polaris.config.PolarisConfigParser
        Configuration for the task; forwarded to
        ``component.open_model_dataset()``.

    mask_callback_builder : callable, optional
        Function called with ``(filename, ds, output_filename)`` to build a
        mask callback for variables in that output file.

    Returns
    -------
    checked : bool
        Whether any variables were checked

    success : bool
        Whether all variables passed the validation checks
    """
    checked = False
    success = True
    failed_vars = []
    passed_vars = []
    for filename, variables in variables_by_file.items():
        filename = str(filename)
        output_filename = os.path.join(work_dir, filename)
        if not os.path.exists(output_filename):
            logger.error(f'File {output_filename} does not exist.')
            success = False
            continue

        if len(variables) == 0:
            continue

        ds = component.open_model_dataset(output_filename, config)
        try:
            if mask_callback_builder is None:
                mask_callback = None
            else:
                mask_callback = mask_callback_builder(
                    filename, ds, output_filename
                )
            checked = True
            result = check_variables_finite(
                variables=variables,
                filename=output_filename,
                ds=ds,
                logger=logger,
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
            ds.close()

    write_output_validation_log(
        work_dir=work_dir,
        checked=checked,
        success=success,
        passed_vars=passed_vars,
        failed_vars=failed_vars,
    )
    return checked, success


def check_variables_finite(
    variables,
    filename,
    ds,
    logger,
    mask_callback=None,
):
    """
    Check variables in a dataset for NaN and Inf values.

    Returns
    -------
    all_pass : bool
        Whether all variables passed the validation checks
    """
    all_pass = True
    for variable in variables:
        if variable not in ds:
            logger.error(f'Variable {variable} not in {filename}.')
            all_pass = False
            continue

        _, result = _validate_and_mask_variable(
            da=ds[variable],
            variable=variable,
            filename=filename,
            ds=ds,
            logger=logger,
            check_finite=True,
            mask_callback=mask_callback,
        )
        all_pass = all_pass and result

    return all_pass


def write_output_validation_log(
    work_dir, checked, success, passed_vars, failed_vars
):
    """Write output-validation pass/fail marker logs."""
    pass_filename = os.path.join(work_dir, 'output_validation_passed.log')
    fail_filename = os.path.join(work_dir, 'output_validation_failed.log')
    for filename in [pass_filename, fail_filename]:
        if os.path.exists(filename):
            os.remove(filename)

    if not checked:
        return

    if success:
        passed_vars_str = '\n  '.join(passed_vars)
        with open(pass_filename, 'w') as result_log_file:
            result_log_file.write(
                'All variables passed NaN/Inf output validation.\n'
                f'{passed_vars_str}\n'
            )
    else:
        failed_vars_str = '\n  '.join(failed_vars)
        with open(fail_filename, 'w') as result_log_file:
            result_log_file.write(
                f'NaN/Inf output validation failed for:\n {failed_vars_str}\n'
            )


def _all_found(ds1, filename1, ds2, filename2, variable, logger):
    """Is the variable found in both datasets?"""
    all_found = True
    for ds, filename in [(ds1, filename1), (ds2, filename2)]:
        if variable not in ds:
            logger.error(f'Variable {variable} not in {filename}.')
            all_found = False
    return all_found


def _all_sizes_match(da1, filename1, da2, filename2, variable, logger):
    """Do all dimension sizes match between the two variables?"""
    all_match = True
    for dim in da1.sizes:
        if da1.sizes[dim] != da2.sizes[dim]:
            logger.error(
                f"Field sizes for variable {variable} don't "
                f'match files {filename1} and {filename2}.'
            )
            all_match = False
    return all_match


def _compute_norms(
    da1, da2, quiet, max_l1_norm, max_l2_norm, max_linf_norm, time_index=None
):
    """Compute norms between variables in two DataArrays"""

    da1 = _rename_duplicate_dims(da1)
    da2 = _rename_duplicate_dims(da2)

    result = True
    diff = np.abs(da1 - da2).values.ravel()
    # skip entries where one field or both are a fill value
    diff = diff[np.isfinite(diff)]

    if diff.size == 0:
        l1_norm = 0.0
        l2_norm = 0.0
        linf_norm = 0.0
    else:
        l1_norm = np.linalg.norm(diff, ord=1)
        l2_norm = np.linalg.norm(diff, ord=2)
        linf_norm = np.linalg.norm(diff, ord=np.inf)

    if time_index is None:
        diff_str = ''
    else:
        diff_str = f'{time_index:d}: '

    if max_l1_norm is not None:
        if max_l1_norm < l1_norm:
            result = False
    diff_str = f'{diff_str} l1: {l1_norm:16.14e} '

    if max_l2_norm is not None:
        if max_l2_norm < l2_norm:
            result = False
    diff_str = f'{diff_str} l2: {l2_norm:16.14e} '

    if max_linf_norm is not None:
        if max_linf_norm < linf_norm:
            result = False
    diff_str = f'{diff_str} linf: {linf_norm:16.14e} '

    if not quiet or not result:
        print(diff_str)

    return result


def _rename_duplicate_dims(da):
    dims = list(da.dims)
    new_dims = list(dims)
    duplicates = False
    for index, dim in enumerate(dims):
        if dim in dims[index + 1 :]:
            duplicates = True
            suffix = 2
            for other_index, other in enumerate(dims[index + 1 :]):
                if other == dim:
                    new_dims[other_index + index + 1] = f'{dim}_{suffix}'
                    suffix += 1

    if not duplicates:
        return da

    da = xr.DataArray(data=da.values, dims=new_dims)
    return da


def _validate_and_mask_variable(
    da,
    variable,
    filename,
    ds,
    logger,
    check_finite,
    mask_callback,
):
    """Validate one variable and return the DataArray used for comparison."""
    mask, mask_success = _get_variable_mask(
        mask_callback, variable, da, ds, filename, logger
    )
    if not mask_success:
        return da, False

    result = True
    if check_finite:
        result = _check_finite_values(da, variable, filename, logger, mask)

    if mask is not None:
        da = da.where(mask)

    return da, result


def _get_variable_mask(mask_callback, variable, da, ds, filename, logger):
    """Get an optional mask from a caller-provided callback."""
    if mask_callback is None:
        return None, True

    mask_result = mask_callback(variable, da, ds, filename, logger)
    if isinstance(mask_result, tuple):
        return mask_result

    return mask_result, True


def _check_finite_values(da, variable, filename, logger, mask):
    """Check that values are finite in the checked region."""
    if mask is None:
        values = da.values.ravel()
    else:
        try:
            broadcast_mask = mask.broadcast_like(da).values.astype(bool)
        except ValueError:
            logger.error(
                f'Could not broadcast validation mask for variable '
                f'{variable} in {filename}.'
            )
            return False
        values = da.values[broadcast_mask]

    try:
        finite = np.isfinite(values)
    except TypeError:
        logger.error(
            f'Variable {variable} in {filename} has non-numeric values and '
            f'cannot be checked for NaN/Inf.'
        )
        return False

    invalid_count = np.count_nonzero(~finite)
    if invalid_count == 0:
        return True

    logger.error(
        f'Variable {variable} in {filename} contains {invalid_count} '
        f'NaN/Inf values in the checked region.'
    )
    return False
