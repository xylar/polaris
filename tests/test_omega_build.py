import os
import subprocess
import sys

from polaris.build.omega import make_build_script


def test_build_script_uses_polaris_python(tmp_path, monkeypatch):
    script = _make_script(tmp_path, monkeypatch)

    # a machine's python module may lack packages that CIME imports
    assert 'module load python' not in script
    python_dir = tmp_path / 'build' / 'polaris_python'
    assert f'export PATH="{python_dir}:${{PATH}}"' in script


def test_python_symlink_finds_polaris_packages(tmp_path, monkeypatch):
    script = _make_script(tmp_path, monkeypatch)

    # run only the lines that set up the python, without a module system
    lines = [
        line
        for line in script.splitlines()
        if line.startswith(('mkdir -p', 'ln -sfn', 'export PATH'))
    ]
    lines.append('python3 -c "import sys; print(sys.prefix)"')
    output = subprocess.check_output(
        ['bash', '-c', '\n'.join(lines)],
        env={'PATH': '/usr/bin:/bin', 'HOME': str(tmp_path)},
        text=True,
    )
    assert output.strip() == sys.prefix


def _make_script(tmp_path, monkeypatch):
    monkeypatch.setenv('POLARIS_BRANCH', str(tmp_path / 'polaris'))
    monkeypatch.setenv('METIS_ROOT', str(tmp_path / 'metis'))
    monkeypatch.setenv('PARMETIS_ROOT', str(tmp_path / 'parmetis'))
    build_dir = tmp_path / 'build'
    build_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    script_filename = make_build_script(
        machine='chrysalis',
        compiler='intel',
        branch=str(tmp_path / 'Omega'),
        build_dir=os.path.relpath(build_dir),
        debug=False,
        cmake_flags=None,
    )
    with open(script_filename, 'r', encoding='utf-8') as f:
        return f.read()
