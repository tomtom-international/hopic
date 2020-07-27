# Copyright (c) 2019 - 2020 TomTom N.V. (https://tomtom.com)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from ..cli import cli
from .markers import *

from click.testing import CliRunner
from textwrap import dedent
from typing import Pattern
import git
import os
import pytest
import re
import signal
import stat
import subprocess
import sys

_source_date_epoch = 7 * 24 * 3600


class MonkeypatchInjector:
    def __init__(self, monkeypatch=None, context_entry_function=lambda dum: None):
        self.monkeypatch = monkeypatch
        self.context_entry_function = context_entry_function

    def __enter__(self):
        if self.monkeypatch:
            self.monkeypatch_context = self.monkeypatch.context()
            empty_monkeypatch_context = self.monkeypatch_context.__enter__()
            self.context_entry_function(empty_monkeypatch_context)
            return empty_monkeypatch_context

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.monkeypatch:
            return self.monkeypatch_context.__exit__(exc_type, exc_val, exc_tb)


def run_with_config(config, *args, files={}, env=None, monkeypatch_injector=MonkeypatchInjector()):
    runner = CliRunner(mix_stderr=False, env=env)
    with runner.isolated_filesystem():
        with git.Repo.init() as repo:
            with open('hopic-ci-config.yaml', 'w') as f:
                f.write(config)
            for fname, (content, on_file_created_callback) in files.items():
                if '/' in fname and not os.path.exists(os.path.dirname(fname)):
                    os.makedirs(os.path.dirname(fname))
                with open(fname, 'w') as f:
                    f.write(content)
                on_file_created_callback()
            repo.index.add(('hopic-ci-config.yaml',) + tuple(files.keys()))
            git_time = f"{_source_date_epoch} +0000"
            repo.index.commit(message='Initial commit', author_date=git_time, commit_date=git_time)
        for arg in args:
            with monkeypatch_injector:
                result = runner.invoke(cli, arg)

            if result.stdout_bytes:
                print(result.stdout, end='')
            if result.stderr_bytes:
                print(result.stderr, end='', file=sys.stderr)

            if result.exception is not None and not isinstance(result.exception, SystemExit):
                raise result.exception

            if result.exit_code != 0:
                return result

    return result


def test_missing_manifest():
    with pytest.raises(FileNotFoundError, match=r'(?:i).*\bivy manifest\b.*/dependency_manifest.xml\b'):
        result = run_with_config('''\
image: !image-from-ivy-manifest {}

phases:
  build:
    test:
      - cat /etc/lsb-release
''', ('build',))


def test_global_image(monkeypatch):
    def mock_check_call(args, *popenargs, **kwargs):
        assert args[0] == 'docker'
        assert tuple(args[-2:]) == ('buildpack-deps:18.04', './a.sh')

    with monkeypatch.context() as m:
        m.setattr(subprocess, 'check_call', mock_check_call)
        result = run_with_config('''\
image: buildpack-deps:18.04

phases:
  build:
    a:
      - ./a.sh
''', ('build',))
    assert result.exit_code == 0


def test_default_image(monkeypatch):
    expected = [
        ('buildpack-deps:18.04', './a.sh'),
        ('buildpack-deps:buster', './b.sh'),
    ]

    def mock_check_call(args, *popenargs, **kwargs):
        assert args[0] == 'docker'
        assert tuple(args[-2:]) == expected.pop(0)

    with monkeypatch.context() as m:
        m.setattr(subprocess, 'check_call', mock_check_call)
        result = run_with_config('''\
image:
  default: buildpack-deps:18.04
  b: buildpack-deps:buster

phases:
  build:
    a:
      - ./a.sh
    b:
      - ./b.sh
''', ('build',))
    assert result.exit_code == 0
    assert not expected


def test_docker_run_arguments(monkeypatch, tmp_path):
    expected_image_command = [
        ('buildpack-deps:18.04', './a.sh'),
    ]
    uid = 42
    gid = 4242
    class MockDockerSockStat:
        st_gid = 2323
        st_mode = stat.S_IFSOCK | 0o0660

    def mock_check_call(args, *popenargs, **kwargs):
        expected_docker_args = [
            '--cap-add=SYS_PTRACE', '--rm', '--tty', '--volume=/etc/passwd:/etc/passwd:ro',
            '--volume=/etc/group:/etc/group:ro', '--workdir=/code',
            f"--volume={os.getcwd()}:/code",
            f"--env=SOURCE_DATE_EPOCH={_source_date_epoch}",
            '--env=HOME=/home/sandbox', '--env=_JAVA_OPTIONS=-Duser.home=/home/sandbox',
            f"--user={uid}:{gid}",
            '--net=host', f"--tmpfs=/home/sandbox:uid={uid},gid={gid}",
            '--volume=/var/run/docker.sock:/var/run/docker.sock',
            f"--group-add={MockDockerSockStat.st_gid}",
            re.compile(r'^--cidfile=.*'),
        ]

        assert args[0] == 'docker'
        assert args[1] == 'run'
        image_command_length = len(tuple(expected_image_command[0]))
        assert tuple(args[-image_command_length:]) == expected_image_command.pop(0)
        docker_argument_list = args[2:-image_command_length]

        for docker_arg in expected_docker_args:
            if isinstance(docker_arg, Pattern):
                assert any(docker_arg.match(arg) for arg in docker_argument_list)
                docker_argument_list = [arg for arg in docker_argument_list if not docker_arg.match(arg)]
            else:
                assert docker_arg in docker_argument_list
                docker_argument_list.remove(docker_arg)
        assert docker_argument_list == []

    def set_monkey_patch_attrs(monkeypatch):
        monkeypatch.setattr(os, 'getuid', lambda: uid)
        monkeypatch.setattr(os, 'getgid', lambda: gid)
        old_os_stat = os.stat
        monkeypatch.setattr(os, 'stat', lambda path: MockDockerSockStat() if path == '/var/run/docker.sock' else old_os_stat(path))
        monkeypatch.setattr(subprocess, 'check_call', mock_check_call)

    result = run_with_config('''\
image:
  default: buildpack-deps:18.04

phases:
  build:
    a:
      - docker-in-docker: yes
      - ./a.sh
''', ('build',), monkeypatch_injector=MonkeypatchInjector(monkeypatch, set_monkey_patch_attrs))
    assert result.exit_code == 0
    assert not expected_image_command


def test_image_override_per_phase(monkeypatch):
    expected = [
        ('buildpack-deps:18.04', './build-a.sh'),
        ('buildpack-deps:buster', './build-b.sh'),
        ('test-image-a:latest', './test-a.sh'),
        ('test-image-b:bleeding-edge', './test-b.sh'),
        ('buildpack-deps:18.04', './deploy-a.sh'),
        ('buildpack-deps:buster', './deploy-b.sh'),
    ]

    def mock_check_call(args, *popenargs, **kwargs):
        assert args[0] == 'docker'
        assert tuple(args[-2:]) == expected.pop(0)

    with monkeypatch.context() as m:
        m.setattr(subprocess, 'check_call', mock_check_call)
        result = run_with_config('''\
image:
  default: buildpack-deps:18.04
  b: buildpack-deps:buster

phases:
  build:
    a:
      - ./build-a.sh
    b:
      - ./build-b.sh
      
  test:
    a:
      - image: test-image-a:latest
      - ./test-a.sh
    b: 
      - image: test-image-b:bleeding-edge
      - ./test-b.sh
      
  deploy:
    a:
      - ./deploy-a.sh
    b: 
      - ./deploy-b.sh
    
''', ('build',))
    assert result.exit_code == 0
    assert not expected


def test_image_override_per_step(monkeypatch):
    """
    Verify that, when switching between images, and the absence of an image, the WORKSPACE is set properly.
    """
    expected = [
        ('buildpack-deps:18.04', './build-a.sh', '--workspace=/code'),
        ('./build-b.sh', '--workspace=${PWD}'),
        ('buildpack-deps:buster', './build-c.sh', '--workspace=/code'),
        ('./build-d.sh', '--workspace=${PWD}'),
    ]

    def mock_check_call(args, *popenargs, **kwargs):
        expectation = [arg.replace('${PWD}', os.getcwd()) for arg in expected.pop(0)]
        if len(expected) % 2:
            assert args[0] == 'docker'
            assert args[-3:] == expectation
        else:
            assert args == expectation

    monkeypatch.setattr(subprocess, 'check_call', mock_check_call)
    result = run_with_config(dedent('''\
            image: buildpack-deps:18.04

            phases:
              build:
                a:
                  - ./build-a.sh --workspace=${WORKSPACE}
                  - image: null
                    sh: ./build-b.sh --workspace=${WORKSPACE}
                  - image: buildpack-deps:buster
                    sh: ./build-c.sh --workspace=${WORKSPACE}
                  - image: null
                    sh: ./build-d.sh --workspace=${WORKSPACE}
'''), ('build',))
    assert result.exit_code == 0
    assert not expected


@pytest.mark.parametrize('signum', (
    signal.SIGINT,
    signal.SIGTERM,
))
def test_docker_terminated(monkeypatch, signum):
    expected = [
            ('docker', 'run'),
            ('docker', 'stop'),
        ]

    cid = 'the-magical-container-id'

    def mock_check_call(args, *popenargs, **kwargs):
        assert tuple(args[:2]) == expected.pop(0)

        if args[:2] == ['docker', 'run']:
            for arg in args:
                m = re.match(r'^--cidfile=(.*)', arg)
                if not m:
                    continue
                with open(m.group(1), 'w') as f:
                    f.write(cid)
            os.kill(os.getpid(), signum)
        else:
            assert tuple(args) == ('docker', 'stop', cid)

    monkeypatch.setattr(subprocess, 'check_call', mock_check_call)

    def signal_handler(signum, frame):
        assert False, f"Failed to handle signal {signum}"
    old_handler = signal.signal(signum, signal_handler)
    if old_handler == signal.default_int_handler:
        signal.signal(signum, old_handler)
    try:
        result = run_with_config(dedent('''\
            image: buildpack-deps:18.04

            phases:
              build:
                a:
                  - ./build-a.sh
            '''), ('build',))
    finally:
        signal.signal(signum, old_handler)

    assert result.exit_code == 128 + signum
    assert not expected


@docker
def test_container_with_env_var():
    result = run_with_config('''\
image: buildpack-deps:18.04

pass-through-environment-vars:
  - THE_ENVIRONMENT

phases:
  build:
    test:
      - docker-in-docker: yes
      - printenv THE_ENVIRONMENT
''', ('build',),
    env={'THE_ENVIRONMENT': 'The Real Environment!'})
    assert result.exit_code == 0


@docker
def test_container_without_env_var():
    result = run_with_config('''\
image: buildpack-deps:18.04

pass-through-environment-vars:
  - THE_ENVIRONMENT

phases:
  build:
    test:
      - printenv THE_ENVIRONMENT
''', ('build',),
    env={'THE_ENVIRONMENT': None})
    assert result.exit_code != 0


def test_command_with_source_date_epoch(capfd):
    result = run_with_config('''\
phases:
  build:
    test:
      - printenv SOURCE_DATE_EPOCH
''', ('build',))
    assert result.exit_code == 0
    out, err = capfd.readouterr()
    assert out.strip() == str(_source_date_epoch)


def test_command_with_branch_and_commit(capfd):
    result = run_with_config('''\
phases:
  build:
    test:
      - echo ${GIT_BRANCH}=${GIT_COMMIT}
''',
        ('checkout-source-tree', '--clean', '--target-remote', '.', '--target-ref', 'master'),
        ('build',),
    )
    assert result.exit_code == 0

    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)

    checkout_commit = out.splitlines()[0]
    claimed_branch, claimed_commit = out.splitlines()[1].split('=')
    assert claimed_branch == 'master'
    assert claimed_commit == checkout_commit


def test_empty_variant():
    result = run_with_config(dedent('''\
        phases:
          build:
            test: {}'''), ('build',))
    assert result.exit_code == 0


def test_embed_variants(monkeypatch):
    expected = [
        ('./a.sh', 'test_argument'),
        ('./b.sh',),
        ('./test-a.sh',),
        ('./test-b.sh',),
    ]
    generate_script_path = "generate-variants.py"

    def mock_check_call(args, *popenargs, **kwargs):
        assert tuple(args) == expected.pop(0)

    with monkeypatch.context() as m:
        m.setattr(subprocess, 'check_call', mock_check_call)
        result = run_with_config(dedent(f'''\
                    phases:
                      build:
                        a: 
                          - ./a.sh test_argument
                        b:
                          - ./b.sh 
                        
                      test: !embed
                        cmd: {generate_script_path}'''),
                    ('build',),
                    files={generate_script_path: (dedent('''\
                    #!/usr/bin/env python3
                    print(\'\'\'a:
                      - ./test-a.sh
                    b:
                      - ./test-b.sh \'\'\')'''),
                    lambda: os.chmod(generate_script_path, os.stat(generate_script_path).st_mode | stat.S_IEXEC))})
    assert result.exit_code == 0
    assert not expected


def test_embed_variants_syntax_error(capfd):
    generate_script_path = "generate-variants.py"

    result = run_with_config(dedent(f'''\
                phases:
                  test: !embed
                    cmd: {generate_script_path}
                '''),
                ('build',),
                files={generate_script_path: (dedent('''\
                #!/usr/bin/env python3
                print(\'\'\'a:
                  - ./a-test.sh
                b:
                  - ./b-test.sh
                yaml_error \'\'\')
                '''), lambda: os.chmod(generate_script_path, os.stat(generate_script_path).st_mode | stat.S_IEXEC))})
    assert result.exit_code == 42
    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)
    assert 'An error occurred when parsing the hopic configuration file' in out
