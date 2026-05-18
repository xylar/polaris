import argparse
import glob
import os
import pickle
import sys
import time
from typing import List

import mpas_tools.io
from mpas_tools.logging import LoggingContext

from polaris import Task
from polaris.logging import log_function_call
from polaris.parallel import set_parallel_systems
from polaris.run.dask import dask_client_context
from polaris.run.scheduler import run_task as run_task_with_scheduler
from polaris.run.shared import (
    log_and_run_task,
    log_task_runtimes,
    run_task,
    setup_config,
    unpickle_suite,
    write_output_for_pull_request,
)


def run_tasks(
    suite_name,
    quiet=False,
    is_task=False,
    steps_to_run=None,
    steps_to_skip=None,
):
    """
    Run the given suite or task through the ``polaris run`` command path.

    Parameters
    ----------
    suite_name : str
        The name of the suite.

    quiet : bool, optional
        Whether step names are not included in stdout as the suite progresses.

    is_task : bool, optional
        Whether this is a task instead of a full suite.

    steps_to_run : list of str, optional
        A list of the steps to run if this is a task, not a full suite.
        The default behavior is to run the default steps unless they are in
        ``steps_to_skip``.

    steps_to_skip : list of str, optional
        A list of steps not to run if this is a task, not a full suite.
    """
    suite = unpickle_suite(suite_name)

    # get the config file for the first task in the suite
    task = next(iter(suite['tasks'].values()))
    component = task.component
    common_config = setup_config(task.base_work_dir, f'{component.name}.cfg')
    set_parallel_systems(suite['tasks'], common_config)
    available_resources = component.get_available_resources()

    # start logging to stdout/stderr
    with LoggingContext(suite_name) as stdout_logger:
        os.environ['PYTHONUNBUFFERED'] = '1'

        if not is_task:
            try:
                os.makedirs('case_outputs')
            except OSError:
                pass

        with dask_client_context(
            available_resources, logger=stdout_logger
        ) as dask_client:
            failures = 0
            cwd = os.getcwd()
            suite_start = time.time()
            task_times = dict()
            result_strs = dict()
            total_tasks = len(suite['tasks'])
            exec_fail_tasks: List[str] = []
            diff_fail_tasks: List[str] = []
            for task_name in suite['tasks']:
                stdout_logger.info(f'{task_name}')

                task = suite['tasks'][task_name]

                if is_task:
                    log_filename = None
                    task_logger = stdout_logger
                else:
                    task_prefix = task.path.replace('/', '_')
                    log_filename = f'{cwd}/case_outputs/{task_prefix}.log'
                    task_logger = None
                if is_task:
                    task_runner = run_task_with_scheduler
                else:
                    task_runner = None

                (
                    result_str,
                    success,
                    task_time,
                    exec_failed,
                    diff_failed,
                ) = log_and_run_task(
                    task,
                    stdout_logger,
                    task_logger,
                    quiet,
                    log_filename,
                    is_task,
                    steps_to_run,
                    steps_to_skip,
                    available_resources,
                    subprocess_command='run',
                    dask_client=dask_client,
                    task_runner=task_runner,
                )
                result_strs[task_name] = result_str
                if not success:
                    failures += 1
                if exec_failed:
                    exec_fail_tasks.append(task_name)
                if diff_failed:
                    diff_fail_tasks.append(task_name)
                task_times[task_name] = task_time

            suite_time = time.time() - suite_start

            os.chdir(cwd)

            # Write a concise, copy/paste-friendly summary for Omega PRs
            write_output_for_pull_request(
                suite_name,
                suite,
                results={
                    'total': total_tasks,
                    'failures': exec_fail_tasks,
                    'diffs': diff_fail_tasks,
                },
            )

            log_task_runtimes(
                stdout_logger, task_times, result_strs, suite_time, failures
            )


def run_single_step(step_is_subprocess=False, quiet=False):
    """
    Run one step from its work directory through ``polaris run``.

    Parameters
    ----------
    step_is_subprocess : bool, optional
        Whether the step is being run as a subprocess of a task or suite.

    quiet : bool, optional
        Whether step names are not included in stdout as the suite progresses.
    """
    with open('step.pickle', 'rb') as handle:
        step = pickle.load(handle)
    task = Task(component=step.component, name='dummy_task')
    task.add_step(step)
    task.new_step_log_file = False

    # This prevents infinite loop of subprocesses
    if step_is_subprocess:
        step.run_as_subprocess = False

    config = setup_config(step.base_work_dir, step.config.filepath)
    task.config = config
    set_parallel_systems({task.path: task}, config)
    available_resources = step.component.get_available_resources()

    mpas_tools.io.default_format = config.get('io', 'format')
    mpas_tools.io.default_engine = config.get('io', 'engine')

    # start logging to stdout/stderr
    logger_name = step.path.replace('/', '_')
    with LoggingContext(name=logger_name) as stdout_logger:
        task.logger = stdout_logger
        if quiet:
            task.stdout_logger = None
        else:
            task.stdout_logger = stdout_logger
            log_function_call(function=run_task, logger=stdout_logger)
            stdout_logger.info('')
            stdout_logger.info(f'Running step: {step.name}')
        with dask_client_context(
            available_resources, logger=stdout_logger
        ) as dask_client:
            run_task(
                task,
                available_resources,
                subprocess_command='run',
                dask_client=dask_client,
            )


def main():
    parser = argparse.ArgumentParser(
        description='Run a suite, task or step', prog='polaris run'
    )
    parser.add_argument(
        'suite',
        nargs='?',
        help='The name of a suite to run. Can exclude '
        'or include the .pickle filename suffix.',
    )
    parser.add_argument(
        '--steps', dest='steps', nargs='+', help='The steps of a task to run.'
    )
    parser.add_argument(
        '--skip_steps',
        dest='skip_steps',
        nargs='+',
        help='The steps of a task not to run, see '
        'steps_to_run in the config file for defaults.',
    )
    parser.add_argument(
        '-q',
        '--quiet',
        dest='quiet',
        action='store_true',
        help='If set, step names are not included in the '
        'output as the suite progresses.  Has no '
        'effect when running tasks or steps on '
        'their own.',
    )
    parser.add_argument(
        '--step_is_subprocess',
        dest='step_is_subprocess',
        action='store_true',
        help='Used internally by polaris to indicate that '
        'a step is being run as a subprocess.',
    )
    args = parser.parse_args(sys.argv[2:])

    if args.suite is not None:
        # Running a specified suite from the base work directory
        run_tasks(args.suite, quiet=args.quiet)
    elif os.path.exists('task.pickle'):
        # Running a task inside of its work directory
        run_tasks(
            suite_name='task',
            quiet=args.quiet,
            is_task=True,
            steps_to_run=args.steps,
            steps_to_skip=args.skip_steps,
        )
    elif os.path.exists('step.pickle'):
        # Running a step inside of its work directory
        run_single_step(
            step_is_subprocess=args.step_is_subprocess, quiet=args.quiet
        )
    else:
        pickles = glob.glob('*.pickle')
        if len(pickles) == 1:
            # Running an unspecified suite from the base work directory
            suite = os.path.splitext(os.path.basename(pickles[0]))[0]
            run_tasks(suite, quiet=args.quiet)
        elif len(pickles) == 0:
            raise OSError(
                'No pickle files were found. Are you sure this is '
                'a polaris suite, task or step work directory?'
            )
        else:
            raise ValueError(
                'More than one suite was found. Please specify '
                'which to run: polaris run <suite>'
            )
