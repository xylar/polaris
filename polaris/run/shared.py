import os
import pickle
import sys
import time
from datetime import timedelta
from typing import Dict, List, Optional

import mpas_tools.io
from mpas_tools.logging import LoggingContext, check_call

from polaris.build.omega import detect_omega_build_type
from polaris.config import PolarisConfigParser
from polaris.logging import log_function_call, log_method_call

# ANSI fail text: https://stackoverflow.com/a/287944/7728169
start_fail = '\033[91m'
start_pass = '\033[92m'
start_time_color = '\033[94m'
end_color = '\033[0m'
pass_str = f'{start_pass}PASS{end_color}'
success_str = f'{start_pass}SUCCESS{end_color}'
fail_str = f'{start_fail}FAIL{end_color}'
error_str = f'{start_fail}ERROR{end_color}'


def unpickle_suite(suite_name):
    """
    Unpickle a suite or task run description from the current directory.

    Parameters
    ----------
    suite_name : str
        The name of the suite, with or without the ``.pickle`` suffix.

    Returns
    -------
    test_suite : dict
        The unpickled suite dictionary.

    Raises
    ------
    ValueError
        If the pickle file does not exist in the current directory.
    """
    # Allow a suite name to either include or not the .pickle suffix
    if suite_name.endswith('.pickle'):
        # code below assumes no suffix, so remove it
        suite_name = suite_name[: -len('.pickle')]
    # Now open the the suite's pickle file
    if not os.path.exists(f'{suite_name}.pickle'):
        raise ValueError(
            f'The suite "{suite_name}" does not appear to have '
            f'been set up here.'
        )
    with open(f'{suite_name}.pickle', 'rb') as handle:
        test_suite = pickle.load(handle)
    return test_suite


def setup_config(base_work_dir, config_filepath):
    """
    Set up a runtime config object from a config file.

    Parameters
    ----------
    base_work_dir : str
        The base work directory where suites and tasks were set up.

    config_filepath : str
        The path to the config file relative to ``base_work_dir``.

    Returns
    -------
    config : polaris.config.PolarisConfigParser
        The populated config object. Its ``filepath`` attribute is set to
        ``config_filepath`` so it can be reused by step and task runtime code.
    """
    config_filename = os.path.join(base_work_dir, config_filepath)
    config = PolarisConfigParser()
    config.add_from_file(config_filename)
    config.filepath = config_filepath
    return config


def load_dependencies(step):
    """
    Reload a step's runtime dependencies from their post-run pickle files.

    Each non-cached dependency is replaced with the object stored in its
    ``step_after_run.pickle`` file so dependent steps can use state that was
    updated while the prerequisite step ran.

    Parameters
    ----------
    step : polaris.step.Step
        The step whose dependencies should be reloaded.

    Raises
    ------
    ValueError
        If a non-cached dependency has not produced
        ``step_after_run.pickle``.
    """
    for name, old_dependency in step.dependencies.items():
        if old_dependency.cached:
            continue

        pickle_filename = os.path.join(
            old_dependency.work_dir, 'step_after_run.pickle'
        )
        if not os.path.exists(pickle_filename):
            raise ValueError(
                f'The dependency {name} of step {step.path} was not run.'
            )

        with open(pickle_filename, 'rb') as handle:
            dependency = pickle.load(handle)
            step.dependencies[name] = dependency


def complete_step_run(step):
    """
    Record successful completion of a step.

    This writes ``polaris_step_complete.log`` in the current working
    directory. If the step is a dependency of another step, it also writes
    ``step_after_run.pickle`` in the step work directory so dependent steps can
    reload runtime-updated state.

    Parameters
    ----------
    step : polaris.step.Step
        The step that has just completed successfully.
    """
    with open('polaris_step_complete.log', 'w') as log_file:
        log_file.write(f'{step.path} finished successfully.')
    if step.is_dependency:
        # pickle the test case and step for use at runtime
        pickle_filename = os.path.join(step.work_dir, 'step_after_run.pickle')
        with open(pickle_filename, 'wb') as handle:
            pickle.dump(step, handle, protocol=pickle.HIGHEST_PROTOCOL)


def update_steps_to_run(task_name, steps_to_run, steps_to_skip, config, steps):
    """
    Resolve and validate the selected steps for a task run.

    If ``steps_to_run`` is not provided, the default list is read from the
    task's ``steps_to_run`` config option. Any explicitly skipped steps are
    removed from the selected list after both lists have been validated.

    Parameters
    ----------
    task_name : str
        The task name used as the config section containing
        ``steps_to_run``.

    steps_to_run : list of str or None
        Explicit step names to run. If ``None``, use the config default.

    steps_to_skip : list of str or None
        Step names to remove from the selected list.

    config : configparser.ConfigParser
        The runtime config object.

    steps : Mapping
        The available task steps, keyed by step name.

    Returns
    -------
    steps_to_run : list of str
        The validated step names to run.

    Raises
    ------
    ValueError
        If a selected or skipped step is not in ``steps``.
    """
    if steps_to_run is None:
        step_str = config.get(task_name, 'steps_to_run').replace(',', ' ')
        steps_to_run = step_str.split()

    for step in steps_to_run:
        if step not in steps:
            raise ValueError(
                f'A step "{step}" was requested but is not one of the steps '
                f'in this task:'
                f'\n{list(steps)}'
            )

    if steps_to_skip is not None:
        for step in steps_to_skip:
            if step not in steps:
                raise ValueError(
                    f'A step "{step}" was flagged not to run but is not one '
                    f'of the steps in this task:'
                    f'\n{list(steps)}'
                )

        steps_to_run = [
            step for step in steps_to_run if step not in steps_to_skip
        ]

    return steps_to_run


def log_task_runtimes(
    stdout_logger, task_times, result_strs, suite_time, failures
):
    """
    Log the task runtime summary for a run.

    Parameters
    ----------
    stdout_logger : logging.Logger
        Logger used for run-level output.

    task_times : dict
        Mapping from task name to elapsed runtime in seconds.

    result_strs : dict
        Mapping from task name to formatted pass/fail status text.

    suite_time : float
        Total elapsed suite runtime in seconds.

    failures : int
        The number of failed tasks.

    Raises
    ------
    SystemExit
        If one or more tasks failed.
    """
    stdout_logger.info('Task Runtimes:')
    for task_name, task_time in task_times.items():
        task_time_str = str(timedelta(seconds=round(task_time)))
        stdout_logger.info(
            f'{task_time_str} {result_strs[task_name]} {task_name}'
        )
    suite_time_str = str(timedelta(seconds=round(suite_time)))
    stdout_logger.info(f'Total runtime: {suite_time_str}')

    if failures == 0:
        stdout_logger.info('PASS: All passed successfully!')
    else:
        if failures == 1:
            message = '1 task'
        else:
            message = f'{failures} tasks'
        stdout_logger.error(f'FAIL: {message} failed, see above.')
        sys.exit(1)


def print_to_stdout(task, message):
    """
    Log a task progress message to stdout and, if different, the task log.

    Parameters
    ----------
    task : polaris.Task
        The task whose stdout and task loggers should receive the message.

    message : str
        The message to log.
    """
    if task.stdout_logger is not None:
        task.stdout_logger.info(message)
        if task.logger != task.stdout_logger:
            # also write it to the log file
            task.logger.info(message)


def log_and_run_task(
    task,
    stdout_logger,
    task_logger,
    quiet,
    log_filename,
    is_task,
    steps_to_run,
    steps_to_skip,
    available_resources,
    subprocess_command='serial',
    dask_client=None,
):
    """
    Run one task with task-level logging and status accounting.

    Parameters
    ----------
    task : polaris.Task
        The task to run.

    stdout_logger : logging.Logger
        Logger used for run-level output.

    task_logger : logging.Logger or None
        Existing task logger to use, or ``None`` to create one.

    quiet : bool
        Whether step progress should be omitted from stdout.

    log_filename : str or None
        Task log filename, or ``None`` for stdout-only logging.

    is_task : bool
        Whether the run scope is a single task rather than a suite.

    steps_to_run : list of str or None
        Explicit step names to run for a task-scope invocation.

    steps_to_skip : list of str or None
        Step names to skip for a task-scope invocation.

    available_resources : dict
        Available CPU, GPU and MPI resources for this run.

    subprocess_command : str, optional
        Polaris subcommand to use when a step must run in a subprocess.

    dask_client : distributed.Client, optional
        Dask client for the active ``polaris run`` lifecycle.

    Returns
    -------
    result_str : str
        Formatted pass/fail status for summary output.

    success : bool
        Whether task execution and baseline checks passed.

    task_time : float
        Elapsed task runtime in seconds.

    exec_failed : bool
        Whether task execution failed.

    diff_failed : bool
        Whether baseline comparison failed.
    """
    task_name = task.path.replace('/', '_')
    with LoggingContext(
        task_name, logger=task_logger, log_filename=log_filename
    ) as task_logger:
        if quiet:
            # just log the step names and any failure messages to the
            # log file
            task.stdout_logger = task_logger
        else:
            # log steps to stdout
            task.stdout_logger = stdout_logger
        task.logger = task_logger
        task.log_filename = log_filename

        # If we are running a task on its own, we want a log file per step
        # If we are running within a suite, we want a log file per task, with
        #   output from each of its steps
        task.new_step_log_file = is_task

        os.chdir(task.work_dir)

        config = setup_config(task.base_work_dir, task.config.filepath)
        task.config = config

        mpas_tools.io.default_format = config.get('io', 'format')
        mpas_tools.io.default_engine = config.get('io', 'engine')

        task.steps_to_run = update_steps_to_run(
            task.name, steps_to_run, steps_to_skip, config, task.steps
        )

        task_start = time.time()

        log_function_call(function=run_task, logger=task_logger)
        task_logger.info('')
        task_list = ', '.join(task.steps_to_run)
        task_logger.info(f'Running steps: {task_list}')
        # Default in case execution fails before setting this
        baselines_passed = None
        try:
            baselines_passed = run_task(
                task,
                available_resources,
                subprocess_command=subprocess_command,
                dask_client=dask_client,
            )
            run_status = success_str
            task_pass = True
        except Exception:
            run_status = error_str
            task_pass = False
            task_logger.exception(
                'Exception raised while running the steps of the task'
            )

        status = f'  task execution:   {run_status}'
        task_logger.info(f'POLARIS TASK: {"PASS" if task_pass else "FAIL"}')
        if task_pass:
            stdout_logger.info(status)
            if baselines_passed is None:
                result_str = pass_str
                success = True
            else:
                if baselines_passed:
                    baseline_str = pass_str
                    result_str = pass_str
                    success = True
                else:
                    baseline_str = fail_str
                    result_str = fail_str
                    success = False
                status = f'  baseline comp.:   {baseline_str}'
                stdout_logger.info(status)
                task_logger.info(
                    f'POLARIS BASELINE: '
                    f'{"PASS" if baselines_passed else "FAIL"}'
                )

        else:
            stdout_logger.error(status)
            if not is_task:
                stdout_logger.error(f'  see: case_outputs/{task_name}.log')
            result_str = fail_str
            success = False

        task_time = time.time() - task_start

        task_time_str = str(timedelta(seconds=round(task_time)))
        stdout_logger.info(
            f'  task runtime:     {start_time_color}{task_time_str}{end_color}'
        )

    exec_failed = not task_pass
    diff_failed = baselines_passed is False
    return result_str, success, task_time, exec_failed, diff_failed


def read_baseline_status_from_logs(step_work_dir: str) -> Optional[bool]:
    """
    Get baseline comparison status from existing marker files.

    Parameters
    ----------
    step_work_dir : str
        The step work directory to inspect.

    Returns
    -------
    status : bool or None
        True if ``baseline_passed.log`` exists, False if
        ``baseline_failed.log`` exists, otherwise None.
    """
    baseline_pass_filename = os.path.join(step_work_dir, 'baseline_passed.log')
    baseline_fail_filename = os.path.join(step_work_dir, 'baseline_failed.log')

    if os.path.exists(baseline_pass_filename):
        return True
    if os.path.exists(baseline_fail_filename):
        return False
    return None


def read_property_status_from_logs(step_work_dir: str) -> Optional[bool]:
    """
    Get property-check status from existing marker files.

    Parameters
    ----------
    step_work_dir : str
        The step work directory to inspect.

    Returns
    -------
    status : bool or None
        True if ``property_check_passed.log`` exists, False if
        ``property_check_failed.log`` exists, otherwise None.
    """
    property_check_pass_filename = os.path.join(
        step_work_dir, 'property_check_passed.log'
    )
    property_check_fail_filename = os.path.join(
        step_work_dir, 'property_check_failed.log'
    )

    if os.path.exists(property_check_pass_filename):
        return True
    if os.path.exists(property_check_fail_filename):
        return False
    return None


def accumulate_statuses(
    accumulated_status: Optional[bool], status: bool
) -> Optional[bool]:
    """
    Aggregate validation results across steps.

    None means no validations were performed. If any validation fails, the
    aggregate becomes False.

    Parameters
    ----------
    accumulated_status : bool or None
        The aggregate status so far, or ``None`` if no validation has run.

    status : bool
        The new validation status to include.

    Returns
    -------
    accumulated_status : bool or None
        The updated aggregate status.
    """
    if accumulated_status is None:
        return status
    return accumulated_status and status


def run_task(
    task, available_resources, subprocess_command='serial', dask_client=None
):
    """
    Run each selected step in a task.

    Parameters
    ----------
    task : polaris.Task
        The task to run. Its ``steps_to_run`` list controls which steps are
        considered.

    available_resources : dict
        Available CPU, GPU and MPI resources for this run.

    subprocess_command : str, optional
        Polaris subcommand to use when a step must run in a subprocess.

    dask_client : distributed.Client, optional
        Dask client for the active ``polaris run`` lifecycle.

    Returns
    -------
    baselines_passed : bool or None
        Aggregate baseline comparison status across steps. ``None`` means no
        baseline comparisons were performed.
    """
    logger = task.logger
    cwd = os.getcwd()
    baselines_passed = None
    property_passed = None
    for step_name in task.steps_to_run:
        step = task.steps[step_name]
        complete_filename = os.path.join(
            step.work_dir, 'polaris_step_complete.log'
        )

        print_to_stdout(task, f'  * step: {step_name}')

        if os.path.exists(complete_filename):
            print_to_stdout(task, '          already completed')
            # print results of baseline comparison if it was done
            baseline_status = read_baseline_status_from_logs(step.work_dir)
            if baseline_status is not None:
                baseline_str = pass_str if baseline_status else fail_str
                print_to_stdout(
                    task, f'          baseline comp.:   {baseline_str}'
                )
                baselines_passed = accumulate_statuses(
                    baselines_passed, baseline_status
                )
            property_status = read_property_status_from_logs(step.work_dir)
            if property_status is not None:
                property_str = pass_str if property_status else fail_str
                print_to_stdout(
                    task, f'          property comp.:   {property_str}'
                )
                property_passed = accumulate_statuses(
                    property_passed, property_status
                )
            continue
        if step.cached:
            print_to_stdout(task, '          cached')
            # cached steps never perform baseline comparisons; leave
            # baselines_passed unchanged
            continue

        step_start = time.time()

        step.config = setup_config(step.base_work_dir, step.config.filepath)
        if task.log_filename is not None:
            step_log_filename = task.log_filename
        else:
            step_log_filename = None

        try:
            if step.run_as_subprocess:
                run_step_as_subprocess(
                    logger,
                    step,
                    task.new_step_log_file,
                    subprocess_command=subprocess_command,
                )
            else:
                run_step(
                    task,
                    step,
                    task.new_step_log_file,
                    available_resources,
                    step_log_filename,
                    dask_client=dask_client,
                )
        except Exception:
            print_to_stdout(task, f'          execution:        {error_str}')
            raise
        finally:
            # Always restore the working directory, even if a step fails.
            os.chdir(cwd)

        print_to_stdout(task, f'          execution:        {success_str}')
        step_time = time.time() - step_start
        step_time_str = str(timedelta(seconds=round(step_time)))

        compared, status = step.check_properties()
        if compared:
            if status:
                property_str = pass_str
            else:
                property_str = fail_str
            print_to_stdout(
                task, f'          property checks:  {property_str}'
            )
            property_passed = accumulate_statuses(property_passed, status)

        compared, status = step.validate_baselines()
        if compared:
            if status:
                baseline_str = pass_str
            else:
                baseline_str = fail_str
            print_to_stdout(
                task, f'          baseline comp.:   {baseline_str}'
            )
            baselines_passed = accumulate_statuses(baselines_passed, status)

        print_to_stdout(
            task,
            f'          runtime:          '
            f'{start_time_color}{step_time_str}{end_color}',
        )

    return baselines_passed


def run_step(
    task,
    step,
    new_log_file,
    available_resources,
    step_log_filename,
    dask_client=None,
):
    """
    Run one step through the standard Polaris step lifecycle.

    The lifecycle includes runtime input checks, dependency loading, resource
    constraining, ``runtime_setup()``, either command-line execution or
    ``run()``, completion markers and output checks.

    Parameters
    ----------
    task : polaris.Task
        The task that owns this step for the current run.

    step : polaris.Step
        The step to run.

    new_log_file : bool
        Whether to create a separate log file for this step.

    available_resources : dict
        Available CPU, GPU and MPI resources for this run.

    step_log_filename : str or None
        Existing task log filename to associate with the step, or ``None``.

    dask_client : distributed.Client, optional
        Dask client for the active ``polaris run`` lifecycle.

    Raises
    ------
    OSError
        If required input files are missing before the step runs, or required
        output files are missing after it completes.
    """
    logger = task.logger
    cwd = os.getcwd()

    missing_files = list()
    for input_file in step.inputs:
        if not os.path.exists(input_file):
            missing_files.append(input_file)

    if len(missing_files) > 0:
        raise OSError(
            f'input file(s) missing in step {step.name} in '
            f'{step.component.name}/{step.subdir}: {missing_files}'
        )

    load_dependencies(step)

    # each logger needs a unique name
    logger_name = step.path.replace('/', '_')
    if new_log_file:
        # we want to create new log file and point the step to that name
        new_log_filename = f'{cwd}/{step.name}.log'
        step_log_filename = new_log_filename
        step_logger = None
    else:
        # either we don't want a log file at all or there is an existing one
        # to use.  Either way, we don't want a new log filename and we want
        # to use the existing logger.  The step log filename will be whatever
        # is passed as a parameter
        step_logger = logger
        new_log_filename = None

    step.log_filename = step_log_filename

    with LoggingContext(
        name=logger_name, logger=step_logger, log_filename=new_log_filename
    ) as step_logger:
        step.logger = step_logger
        os.chdir(step.work_dir)

        step_logger.info('')
        log_method_call(method=step.constrain_resources, logger=step_logger)
        step_logger.info('')
        step.constrain_resources(available_resources)

        # runtime_setup() will perform small tasks that require knowing the
        # resources of the task before the step runs (such as creating
        # graph partitions)
        step_logger.info('')
        log_method_call(method=step.runtime_setup, logger=step_logger)
        step_logger.info('')
        step.runtime_setup()

        if step.args is not None:
            step_logger.info(
                "\nBypassing step's run() method and running "
                'with command line args\n'
            )
            for args in step.args:
                log_method_call(
                    method=step.component.run_parallel_command,
                    logger=step_logger,
                )
                step_logger.info('')
                step.component.run_parallel_command(
                    args,
                    step.cpus_per_task,
                    step.ntasks,
                    step.openmp_threads,
                    step.logger,
                    gpus_per_task=step.gpus_per_task,
                )
        else:
            step_logger.info('')
            log_method_call(method=step.run, logger=step_logger)
            step_logger.info('')
            step.run()

    complete_step_run(step)

    missing_files = list()
    for output_file in step.outputs:
        if not os.path.exists(output_file):
            missing_files.append(output_file)

    if len(missing_files) > 0:
        # We want to indicate that the step failed by removing the pickle
        try:
            os.remove('step_after_run.pickle')
        except FileNotFoundError:
            pass
        raise OSError(
            f'output file(s) missing in step {step.name} in '
            f'{step.component.name}/{step.subdir}: {missing_files}'
        )


def run_step_as_subprocess(
    logger, step, new_log_file, subprocess_command='serial'
):
    """
    Run one step by invoking ``polaris serial`` in a subprocess.

    Parameters
    ----------
    logger : logging.Logger
        The logger to use if a separate step log is not requested.

    step : polaris.Step
        The step to run.

    new_log_file : bool
        Whether to create a separate log file for this step.

    subprocess_command : str, optional
        Polaris subcommand to use for the subprocess.
    """
    cwd = os.getcwd()
    logger_name = step.path.replace('/', '_')
    if new_log_file:
        log_filename = f'{cwd}/{step.name}.log'
        step_logger = None
    else:
        step_logger = logger
        log_filename = None

    step.log_filename = log_filename

    with LoggingContext(
        name=logger_name, logger=step_logger, log_filename=log_filename
    ) as step_logger:
        os.chdir(step.work_dir)
        step_args = ['polaris', subprocess_command, '--step_is_subprocess']
        check_call(step_args, step_logger)


def write_output_for_pull_request(
    suite_name, suite, results: Optional[dict] = None
):
    """
    Write a concise run summary for copy/paste into an Omega pull request.

    The summary is built from provenance metadata, scheduler log naming
    conventions and optional run results. If no provenance file is present,
    this helper returns without writing a file.

    Parameters
    ----------
    suite_name : str
        The name of the suite (or 'task') being run.

    suite : dict
        The unpickled suite dictionary containing tasks and base work dir.

    results : dict, optional
        Optional result metadata with ``total``, ``failures`` and ``diffs``
        entries.
    """
    work_dir = suite.get('work_dir', os.getcwd())
    provenance_path = os.path.join(work_dir, 'provenance')
    if not os.path.exists(provenance_path):
        return

    # keys in provenance are written exactly like these labels
    labels = {
        'baseline work directory': 'baseline',
        'build directory': 'build',
        'build type': 'build_type',
        'work directory': 'work',
        'machine': 'machine',
        'partition': 'partition',
        'compiler': 'compiler',
    }

    values: Dict[str, Optional[str]] = {v: None for v in labels.values()}
    _parse_provenance_into(provenance_path, labels, values)

    # If a baseline workdir exists, parse its provenance to get baseline build
    baseline_build: Optional[str] = None
    baseline_build = _parse_baseline_build(values.get('baseline'))

    # Build the output content. Only include optional lines if present.
    lines = [f'### Polaris `{suite_name}` suite']

    if values['baseline']:
        lines.append(f'- Baseline workdir: `{values["baseline"]}`')
    if baseline_build:
        lines.append(f'- Baseline build: `{baseline_build}`')
    if values['build']:
        lines.append(f'- PR build: `{values["build"]}`')
    if values['work']:
        lines.append(f'- PR workdir: `{values["work"]}`')
    if values['machine']:
        lines.append(f'- Machine: `{values["machine"]}`')
    if values['partition']:
        lines.append(f'- Partition: `{values["partition"]}`')
    if values['compiler']:
        lines.append(f'- Compiler: `{values["compiler"]}`')

    build_type = values['build_type']
    if build_type is None:
        build_type = detect_omega_build_type(values['build'])

    if build_type:
        lines.append(f'- Build type: `{build_type}`')
    else:
        lines.append('- Build type: unknown')

    # Try to include job scheduler log path for Slurm
    job_log = _derive_job_log_path(suite_name, suite)
    if job_log and os.path.exists(job_log):
        lines.append(f'- Log: `{job_log}`')
    else:
        lines.append('- Log: not found')

    # If we have results, summarize them
    if results is not None and isinstance(results, dict):
        total = int(results.get('total', 0) or 0)
        failures: List[str] = list(results.get('failures', []) or [])
        diffs: List[str] = list(results.get('diffs', []) or [])

        if total > 0 and not failures and not diffs:
            lines.append('- Result: All tests passed')
        else:
            lines.append('- Result:')
            if failures:
                lines.append(f'  - Failures ({len(failures)} of {total}):')
                for name in failures:
                    lines.append(f'    - `{name}`')
            if diffs:
                lines.append(f'  - Diffs ({len(diffs)} of {total}):')
                for name in diffs:
                    lines.append(f'    - `{name}`')

    out_path = os.path.join(work_dir, f'{suite_name}_output_for_pr.md')
    print(f'Writing output useful for copy/paste into PRs to:\n  {out_path}')
    with open(out_path, 'w') as out:
        out.write('\n'.join(lines) + '\n')
    print('Done.')


def _parse_provenance_into(path, labels, target_values):
    if not os.path.exists(path):
        return
    try:
        with open(path, 'r') as f:
            for line in f:
                if ':' not in line:
                    continue
                parts = line.strip().split(':', 1)
                if len(parts) != 2:
                    continue
                key = parts[0].strip().lower()
                val = parts[1].strip()
                if key in labels:
                    target_values[labels[key]] = val
    except (OSError, UnicodeDecodeError):
        # silent failure; helper is best-effort
        pass


def _parse_baseline_build(baseline_workdir: Optional[str]) -> Optional[str]:
    if not baseline_workdir:
        return None
    path = os.path.join(baseline_workdir, 'provenance')
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r') as f:
            for line in f:
                if ':' not in line:
                    continue
                parts = line.strip().split(':', 1)
                if len(parts) != 2:
                    continue
                key = parts[0].strip().lower()
                val = parts[1].strip()
                if key == 'build directory':
                    return val
    except (OSError, UnicodeDecodeError):
        return None
    return None


def _derive_job_log_path(suite_name: str, suite: dict) -> Optional[str]:
    """Best-effort reconstruction of the Slurm job log path."""
    job_id = os.environ.get('SLURM_JOB_ID')
    if not job_id:
        return None

    # Reconstruct the job_name the same way the job script did
    # using the common component config
    try:
        task = next(iter(suite['tasks'].values()))
        component = task.component
        common_config = setup_config(
            task.base_work_dir, f'{component.name}.cfg'
        )
        job_name = common_config.get('job', 'job_name')
        if job_name == '<<<default>>>':
            suite_suffix = f'_{suite_name}' if suite_name else ''
            job_name = f'polaris{suite_suffix}'
        work_dir = suite.get('work_dir', os.getcwd())
        return os.path.join(work_dir, f'{job_name}.o{job_id}')
    except (StopIteration, KeyError, OSError, AttributeError):
        return None
