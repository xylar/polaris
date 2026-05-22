import sys

import pytest

import polaris.job as job
import polaris.setup as setup
import polaris.suite as suite


class FakeConfig:
    def __init__(self, system, account=''):
        self.combined = None
        self.values = {
            ('parallel', 'system'): system,
            ('job', 'job_name'): '<<<default>>>',
            ('job', 'wall_time'): '1:30:00',
        }
        if account != '':
            self.values[('parallel', 'account')] = account

    def combine(self):
        self.combined = self

    def has_option(self, section, option):
        return (section, option) in self.values

    def get(self, section, option):
        return self.values[(section, option)]


class FakeParallelSystem:
    @staticmethod
    def get_config_int(option, default=None):
        values = {'cores_per_node': 64, 'gpus_per_node': 4}
        return values.get(option, default)


def test_job_script_run_command_defaults_to_serial():
    assert (
        setup._get_job_script_run_command('serial')
        == 'source load_polaris_env.sh\npolaris serial'
    )


def test_job_script_run_command_can_use_run():
    assert (
        setup._get_job_script_run_command('run', suite='omega_pr')
        == 'source load_polaris_env.sh\npolaris run omega_pr'
    )


def test_job_script_run_command_rejects_invalid_command():
    with pytest.raises(ValueError, match='Invalid run_command'):
        setup._get_job_script_run_command('parallel')


def test_setup_cli_run_command_default(monkeypatch):
    called = {}

    def _setup_tasks(**kwargs):
        called.update(kwargs)

    monkeypatch.setattr(
        sys,
        'argv',
        ['polaris', 'setup', '-w', 'work', '-t', 'ocean/task'],
    )
    monkeypatch.setattr(setup, 'setup_tasks', _setup_tasks)

    setup.main()

    assert called['run_command'] == 'serial'


def test_setup_cli_run_command_opt_in(monkeypatch):
    called = {}

    def _setup_tasks(**kwargs):
        called.update(kwargs)

    monkeypatch.setattr(
        sys,
        'argv',
        [
            'polaris',
            'setup',
            '-w',
            'work',
            '-t',
            'ocean/task',
            '--run_command',
            'run',
        ],
    )
    monkeypatch.setattr(setup, 'setup_tasks', _setup_tasks)

    setup.main()

    assert called['run_command'] == 'run'


@pytest.mark.parametrize(
    ('system', 'expected_directives'),
    [
        (
            'slurm',
            [
                '#SBATCH --job-name=polaris_omega_pr',
                '#SBATCH --account=project',
                '#SBATCH --nodes=2',
                '#SBATCH --partition=debug',
                '#SBATCH --qos=regular',
                '#SBATCH --constraint=cpu',
                'cd $SLURM_SUBMIT_DIR',
            ],
        ),
        (
            'pbs',
            [
                '#PBS -N polaris_omega_pr',
                '#PBS -A project',
                '#PBS -l select=2',
                '#PBS -q debug',
                '#PBS -l cpu',
                '#PBS -l filesystems=home:eagle',
                'cd $PBS_O_WORKDIR',
            ],
        ),
    ],
)
def test_machine_job_script_can_use_run_for_suite(
    monkeypatch, tmp_path, system, expected_directives
):
    _patch_batch_systems(monkeypatch)
    script_filename = tmp_path / f'{system}.sh'

    job.write_job_script(
        config=FakeConfig(system, account='project'),
        machine='dry_run',
        work_dir=str(tmp_path),
        target_cores=128,
        min_cores=64,
        suite='omega_pr',
        script_filename=str(script_filename),
        run_command=setup._get_job_script_run_command('run', 'omega_pr'),
    )

    script = script_filename.read_text()
    for directive in expected_directives:
        assert directive in script
    assert 'source load_polaris_env.sh\npolaris run omega_pr' in script
    assert 'polaris serial' not in script


@pytest.mark.parametrize('system', ['slurm', 'pbs'])
def test_machine_job_script_defaults_to_serial(monkeypatch, tmp_path, system):
    _patch_batch_systems(monkeypatch)
    script_filename = tmp_path / f'{system}.sh'

    job.write_job_script(
        config=FakeConfig(system),
        machine='dry_run',
        work_dir=str(tmp_path),
        target_cores=64,
        min_cores=64,
        script_filename=str(script_filename),
    )

    script = script_filename.read_text()
    assert 'source load_polaris_env.sh\npolaris serial' in script
    assert 'polaris run' not in script


def test_suite_cli_run_command_default(monkeypatch):
    called = {}

    def _setup_suite(**kwargs):
        called.update(kwargs)

    monkeypatch.setattr(
        sys,
        'argv',
        [
            'polaris',
            'suite',
            '-c',
            'ocean',
            '-t',
            'omega_pr',
            '-w',
            'work',
        ],
    )
    monkeypatch.setattr(suite, 'setup_suite', _setup_suite)

    suite.main()

    assert called['run_command'] == 'serial'


def test_suite_cli_run_command_opt_in(monkeypatch):
    called = {}

    def _setup_suite(**kwargs):
        called.update(kwargs)

    monkeypatch.setattr(
        sys,
        'argv',
        [
            'polaris',
            'suite',
            '-c',
            'ocean',
            '-t',
            'omega_pr',
            '-w',
            'work',
            '--run_command',
            'run',
        ],
    )
    monkeypatch.setattr(suite, 'setup_suite', _setup_suite)

    suite.main()

    assert called['run_command'] == 'run'


def _patch_batch_systems(monkeypatch):
    def _get_parallel_system(config):
        return FakeParallelSystem()

    def _get_slurm_options(config, nodes, min_nodes_allowed):
        assert min_nodes_allowed == 1
        return 'debug', 'regular', 'cpu', '', '2:00:00', nodes

    def _get_pbs_options(config, nodes, min_nodes_allowed):
        assert min_nodes_allowed == 1
        return 'debug', 'cpu', '', '2:00:00', 'home:eagle', nodes

    monkeypatch.setattr(job, 'get_parallel_system', _get_parallel_system)
    monkeypatch.setattr(
        job.SlurmSystem, 'get_slurm_options', _get_slurm_options
    )
    monkeypatch.setattr(job.PbsSystem, 'get_pbs_options', _get_pbs_options)
