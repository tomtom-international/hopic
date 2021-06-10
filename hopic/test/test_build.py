# Copyright (c) 2019 - 2021 TomTom N.V. (https://tomtom.com)
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

from . import source_date_epoch
from .markers import (
        docker,
    )
from .. import credentials
from .. import config_reader
from ..errors import (
    ConfigurationError,
    MissingFileError,
    UnknownPhaseError,
    StepTimeoutExpiredError,
    VersioningError,
)

from datetime import datetime
from textwrap import dedent
from typing import Pattern
import functools
import logging
import os
import pytest
import re
import signal
import stat
import subprocess
import sys
import time

if sys.version_info[:2] >= (3, 10):
    from importlib import metadata
else:
    import importlib_metadata as metadata

from dateutil.parser import parse as parse_date
from dateutil.tz import tzutc


def test_missing_manifest(run_hopic):
    with pytest.raises(FileNotFoundError, match=r'(?:i).*\bivy manifest\b.*/dependency_manifest.xml\b'):
        (_,) = run_hopic(
            ("build",),
            config='''\
image: !image-from-ivy-manifest {}

phases:
  build:
    test:
      - cat /etc/lsb-release
''',
        )


def test_all_phases_and_variants(monkeypatch, run_hopic):
    expected = [
        ('build', 'a'),
        ('build', 'b'),
        ('test', 'b'),
        ('test', 'a'),
    ]

    def mock_check_call(args, *popenargs, **kwargs):
        assert tuple(args) == expected.pop(0)

    monkeypatch.setattr(subprocess, 'check_call', mock_check_call)
    (result,) = run_hopic(
        ("build",),
        config=dedent('''\
phases:
  build:
    a:
      - build a
    b:
      - build b
  test:
    b:
      - test b
    a:
      - test a
'''),
    )
    assert result.exit_code == 0


def test_filtered_phases(monkeypatch, run_hopic):
    expected = [
        ('build', 'a'),
        ('test', 'a'),
    ]

    def mock_check_call(args, *popenargs, **kwargs):
        assert tuple(args) == expected.pop(0)

    monkeypatch.setattr(subprocess, 'check_call', mock_check_call)
    (result,) = run_hopic(
        ("build", "--phase=build", "--phase=test"),
        config=dedent('''\
phases:
  build:
    a:
      - build a
  test:
    a:
      - test a
  deploy:
    a:
      - deploy a
'''),
    )
    assert result.exit_code == 0


def test_filtered_non_existing_phase(monkeypatch, run_hopic):
    expected = []

    def mock_check_call(args, *popenargs, **kwargs):
        assert tuple(args) == expected.pop(0)

    monkeypatch.setattr(subprocess, 'check_call', mock_check_call)
    (result,) = run_hopic(
        ("build", "--phase=build", "--phase=does-not-exist"),
        config=dedent('''\
phases:
  build:
    a:
      - build a
  test:
    a:
      - test a
  deploy:
    a:
      - deploy a
'''),
    )

    assert isinstance(result.exception, UnknownPhaseError)
    assert result.exception.format_message() == "build does not contain phase(s): does-not-exist"


def test_filtered_variants(monkeypatch, run_hopic):
    expected = [
        ('build', 'a'),
        ('build', 'c'),
        ('build', 'd'),
        ('test', 'a'),
        ('test', 'c'),
    ]

    def mock_check_call(args, *popenargs, **kwargs):
        assert tuple(args) == expected.pop(0)

    monkeypatch.setattr(subprocess, 'check_call', mock_check_call)
    (result,) = run_hopic(
        ("build", "--variant=a", "--variant=c", "--variant=d"),
        config=dedent('''\
phases:
  build:
    a:
      - build a
    b:
      - build b
    c:
      - build c
    d:
      - build d
  test:
    b:
      - test b
    a:
      - test a
    c:
      - test c
'''),
    )

    assert (logging.WARNING, "phase 'test' does not contain variant 'd'") in result.logs
    assert result.exit_code == 0


def test_global_image(monkeypatch, run_hopic):
    def mock_check_call(args, *popenargs, **kwargs):
        assert args[0] == 'docker'
        assert tuple(args[-2:]) == ('buildpack-deps:18.04', './a.sh')

    monkeypatch.setattr(subprocess, 'check_call', mock_check_call)
    (result,) = run_hopic(
        ("build",),
        config='''\
image: buildpack-deps:18.04

phases:
  build:
    a:
      - ./a.sh
''',
    )
    assert result.exit_code == 0


def test_default_image(monkeypatch, run_hopic):
    expected = [
        ('buildpack-deps:18.04', './a.sh'),
        ('buildpack-deps:buster', './b.sh'),
    ]

    def mock_check_call(args, *popenargs, **kwargs):
        assert args[0] == 'docker'
        assert tuple(args[-2:]) == expected.pop(0)

    monkeypatch.setattr(subprocess, 'check_call', mock_check_call)
    (result,) = run_hopic(
        ("build",),
        config='''\
image:
  default: buildpack-deps:18.04
  b: buildpack-deps:buster

phases:
  build:
    a:
      - ./a.sh
    b:
      - ./b.sh
''',
    )
    assert result.exit_code == 0
    assert not expected


def test_null_image(monkeypatch, run_hopic):
    expected = [
        {"docker": True, "cmd": ('buildpack-deps:18.04', './a.sh', '123')},
        {"docker": False, "cmd": ('./b.sh',)},
        {"docker": False, "cmd": ('./c.sh',)},
    ]

    def mock_check_call(args, *popenargs, **kwargs):
        expected_call = expected.pop(0)
        if expected_call['docker']:
            assert args[0] == 'docker'
        else:
            assert args[0] != 'docker'

        cmd = tuple(args[-len(expected_call['cmd']):])
        assert cmd == expected_call['cmd']

    monkeypatch.setattr(subprocess, 'check_call', mock_check_call)
    (result,) = run_hopic(
        ("build",),
        config='''\
image:
  default: buildpack-deps:18.04
  b: null

phases:
  build:
    a:
      - ./a.sh 123
    b:
      - ./b.sh
    c:
      - image: null
      - ./c.sh
''',
    )
    assert result.exit_code == 0
    assert not expected


@pytest.mark.parametrize('expect_forward_tty, has_stderr, has_stdin, has_stdout', (
    (True,   True , True , True),
    (False,  True , True , False),
    (False,  True , False, True),
    (False,  False, True , False),
    (False,  False, False, False),
))
def test_docker_run_arguments(run_hopic, expect_forward_tty, has_stderr, has_stdin, has_stdout):
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
            '--cap-add=SYS_PTRACE', '--rm', '--volume=/etc/passwd:/etc/passwd:ro',
            '--volume=/etc/group:/etc/group:ro', '--workdir=/code',
            f"--volume={os.getcwd()}:/code",
            f"--env=SOURCE_DATE_EPOCH={source_date_epoch}",
            '--env=HOME=/home/sandbox', '--env=_JAVA_OPTIONS=-Duser.home=/home/sandbox',
            f"--user={uid}:{gid}",
            '--net=host', f"--tmpfs=/home/sandbox:exec,uid={uid},gid={gid}",
            '--volume=/var/run/docker.sock:/var/run/docker.sock',
            f"--group-add={MockDockerSockStat.st_gid}",
            re.compile(r'^--cidfile=.*'),
        ]
        if expect_forward_tty:
            expected_docker_args += ['--tty']

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
        monkeypatch.setattr(sys.stderr, 'isatty', lambda: has_stderr)
        monkeypatch.setattr(sys.stdin, 'isatty', lambda: has_stdin)
        monkeypatch.setattr(sys.stdout, 'isatty', lambda: has_stdout)

    (result,) = run_hopic(
        ("build",),
        config='''\
image:
  default: buildpack-deps:18.04

phases:
  build:
    a:
      - docker-in-docker: yes
      - ./a.sh
''',
        monkeypatch_injector=set_monkey_patch_attrs,
        tag="0.0.0",
    )
    assert result.exit_code == 0
    assert not expected_image_command


@pytest.mark.parametrize('extra_docker_run_args', (
    {
        'config-lines': ('device: /dev/special-test-device',),
        'expected-args': ('--device=/dev/special-test-device',),
    }, {
        'config-lines': (
            'add-host:',
            '  - my-test-host:10.13.37.254',
            '  - my-other-test-host:10.13.37.253',
        ),
        'expected-args': (
            '--add-host=my-test-host:10.13.37.254',
            '--add-host=my-other-test-host:10.13.37.253',
        ),
    }, {
        'config-lines': (
            'hostname: TESTBAK',
            'init: true',
            'device:',
            '  - /dev/null',
            '  - /dev/special-test-device',
            'add-host: my-test-host:10.13.37.254', 'dns: 9.9.9.9',
        ),
        'expected-args': (
            '--hostname=TESTBAK',
            '--init',
            '--device=/dev/null',
            '--device=/dev/special-test-device',
            '--add-host=my-test-host:10.13.37.254',
            '--dns=9.9.9.9',
        ),
    },
    ), ids=('single-device', 'multiple-hosts', 'all-options'),
)
def test_docker_run_extra_arguments(capfd, monkeypatch, run_hopic, extra_docker_run_args):
    mock_state = {'times_called': 0}

    def mock_check_call(args, *popenargs, **kwargs):
        mock_state['times_called'] += 1
        monkeypatch.setattr(subprocess, 'check_call', mock_check_call)
        for argument in extra_docker_run_args['expected-args']:
            # Expect only the first two calls to contain the extra docker-run arguments
            if mock_state['times_called'] < 3:
                assert argument in args
            else:
                assert argument not in args

    monkeypatch.setattr(subprocess, 'check_call', mock_check_call)
    config_lines = ''.join([f'{10 * " "}{line}\n' for line in extra_docker_run_args['config-lines']])
    (result,) = run_hopic(
        ("build",),
        config=dedent('''\
        image:
          default: buildpack-deps:18.04

        phases:
          p-one:
            v-one:
              - extra-docker-args:
        {extra_args}
              - sh -c 'echo Should contain extra args'
              - sh -c 'echo Should contain extra args'
            v-two:
              - sh -c 'echo Should not contain extra args'
          p-two:
            v-one:
              - sh -c 'echo Should not contain extra args'
        ''').format(extra_args=config_lines),
    )

    assert result.exit_code == 0
    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)


def test_docker_run_extra_arguments_forbidden_option(run_hopic):
    (result,) = run_hopic(
        ("build",),
        config=dedent('''\
            phases:
              p-one:
                v-one:
                  - image: buildpack-deps:18.04
                    extra-docker-args:
                      hostname: TESTBAK
                      user: root
                      workspace: /dev
                  - echo This build shall fail
            '''),
    )

    assert isinstance(result.exception, ConfigurationError)
    err = result.exception.format_message()
    assert '`extra-docker-args` member of `v-one` contains one or more options that are not allowed:' in err.splitlines()[1]
    for option in ('user', 'workspace'):
        assert option in err.splitlines()[2], f'expected {option} in error message'


def test_docker_run_extra_arguments_whitespace_in_option(run_hopic):
    (result,) = run_hopic(
        ("build",),
        config=dedent(
            '''\
            image:
              default: buildpack-deps:18.04

            phases:
              p-one:
                v-one:
                  - extra-docker-args:
                      hostname: something --user root
                  - echo This build shall fail
            '''
        ),
    )

    assert isinstance(result.exception, ConfigurationError)
    err = result.exception.format_message()
    assert 'argument `hostname` for `v-one` contains whitespace, which is not permitted.' in err


def test_override_default_volume(run_hopic):
    global_source = '/somewhere/over/the/rainbow'
    local_source = '/platform/nine/and/three/quarters'

    expected = [
            f"--volume={global_source}:/code",
            f"--volume={local_source}:/code",
        ]

    def mock_check_call(args, *popenargs, **kwargs):
        assert expected.pop(0) in args

    def set_monkey_patch_attrs(monkeypatch):
        monkeypatch.setattr(subprocess, 'check_call', mock_check_call)
        monkeypatch.setattr(os, 'makedirs', lambda _: None)

    (result,) = run_hopic(
        ('build',),
        config=dedent(
            f"""\
            image: buildpack-deps:18.04

            volumes:
              - source: {global_source}
                target: /code

            phases:
              test:
                regular:
                  - echo 'Hello World!'

                awesomeness:
                  - volumes:
                      - source: {local_source}
                        target: /code
                    sh: echo 'Hello World!'
            """
        ),
        monkeypatch_injector=set_monkey_patch_attrs,
    )
    assert result.exit_code == 0
    assert not expected


def test_image_override_per_phase(monkeypatch, run_hopic):
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

    monkeypatch.setattr(subprocess, 'check_call', mock_check_call)
    (result,) = run_hopic(
        ("build",),
        config=dedent('''\
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

            '''),
    )
    assert result.exit_code == 0
    assert not expected


def test_image_override_per_step(monkeypatch, run_hopic):
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
    (result,) = run_hopic(
        ("build",),
        config=dedent('''\
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
'''),
    )
    assert result.exit_code == 0
    assert not expected


@pytest.mark.parametrize('signum', (
    signal.SIGINT,
    signal.SIGTERM,
))
def test_docker_terminated(monkeypatch, run_hopic, signum):
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
        (result,) = run_hopic(
            ("build",),
            config=dedent('''\
            image: buildpack-deps:18.04

            phases:
              build:
                a:
                  - ./build-a.sh
            '''),
        )
    finally:
        signal.signal(signum, old_handler)

    assert result.exit_code == 128 + signum
    assert not expected


@docker
def test_container_with_env_var(run_hopic):
    (result,) = run_hopic(
        ("build",),
        config=dedent('''\
            image: buildpack-deps:18.04

            pass-through-environment-vars:
              - THE_ENVIRONMENT

            phases:
              build:
                test:
                  - docker-in-docker: yes
                  - printenv THE_ENVIRONMENT
            '''),
        env={'THE_ENVIRONMENT': 'The Real Environment!'},
    )
    assert result.exit_code == 0


@docker
def test_container_without_env_var(run_hopic):
    (result,) = run_hopic(
        ("build",),
        config=dedent('''\
            image: buildpack-deps:18.04

            pass-through-environment-vars:
              - THE_ENVIRONMENT

            phases:
              build:
                test:
                  - printenv THE_ENVIRONMENT
            '''),
        env={'THE_ENVIRONMENT': None},
    )
    assert result.exit_code != 0


def test_command_with_source_date_epoch(capfd, run_hopic):
    (result,) = run_hopic(
        ("build",),
        config='''\
phases:
  build:
    test:
      - printenv SOURCE_DATE_EPOCH
''',
    )
    assert result.exit_code == 0
    out, err = capfd.readouterr()
    assert out.strip() == str(source_date_epoch)


def test_command_with_deleted_env_var(run_hopic):
    (result,) = run_hopic(
        ("build",),
        config=dedent(
            '''\
            phases:
              build:
                test:
                  - environment:
                      SOURCE_DATE_EPOCH: null
                    sh: printenv SOURCE_DATE_EPOCH
            '''
        ),
    )
    assert result.exit_code != 0


def test_command_with_branch_and_commit(capfd, run_hopic):
    (_, result) = run_hopic(
        ("checkout-source-tree", "--clean", "--target-remote", ".", "--target-ref", "master"),
        ("build",),
        config=dedent('''\
            phases:
              build:
                test:
                  - echo ${GIT_BRANCH}=${GIT_COMMIT}
            '''),
    )
    assert result.exit_code == 0

    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)

    checkout_commit = out.splitlines()[0]
    claimed_branch, claimed_commit = out.splitlines()[1].split('=')
    assert claimed_branch == 'master'
    assert claimed_commit == checkout_commit


def test_empty_variant(run_hopic):
    (result,) = run_hopic(
        ("build",),
        config=dedent('''\
        phases:
          build:
            test: []'''),
    )
    assert result.exit_code == 0


def test_embed_variants(monkeypatch, run_hopic):
    expected = [
        ('./a.sh', 'test_argument'),
        ('./b.sh',),
        ('./test-a.sh',),
        ('./test-b.sh',),
    ]
    generate_script_path = "generate-variants.py"

    def mock_check_call(args, *popenargs, **kwargs):
        assert tuple(args) == expected.pop(0)

    monkeypatch.setattr(subprocess, 'check_call', mock_check_call)
    (result,) = run_hopic(
        ("build",),
        config=dedent(f'''\
                phases:
                  build:
                    a:
                      - ./a.sh test_argument
                    b:
                      - ./b.sh

                  test: !embed
                    cmd: {generate_script_path}'''),
        files={generate_script_path: (
            dedent('''\
                #!/usr/bin/env python3
                print(\'\'\'a:
                  - ./test-a.sh
                b:
                  - ./test-b.sh \'\'\')'''),
            lambda fname: os.chmod(fname, os.stat(fname).st_mode | stat.S_IEXEC))})
    assert result.exit_code == 0
    assert not expected


def test_embed_variants_syntax_error(capfd, run_hopic):
    generate_script_path = "generate-variants.py"

    (result,) = run_hopic(
        ("build",),
        config=dedent(f'''\
                phases:
                  test: !embed
                    cmd: {generate_script_path}
                '''),
        files={generate_script_path: (dedent('''\
                #!/usr/bin/env python3
                print(\'\'\'a:
                  - ./a-test.sh
                b:
                  - ./b-test.sh
                yaml_error \'\'\')
                '''), lambda fname: os.chmod(fname, os.stat(fname).st_mode | stat.S_IEXEC))})
    assert result.exit_code == 42
    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)
    assert 'An error occurred when parsing the hopic configuration file' in out


@pytest.mark.parametrize('init_version, build, commit_count, dirty, expected_version, expected_pure_version, expected_debversion', (
    ('0.0.0', None   , 0, False, '0.0.0+g{commit}'                    , '0.0.0'                          , '0.0.0+g{commit}'                   ),
    ('1.2.3', None   , 2, False, '1.2.4-2+g{commit}'                  , '1.2.4-2'                        , '1.2.4~2+g{commit}'                 ),
    ('2.0.0', None   , 0, True , '2.0.1-0.dirty.{timestamp}+g{commit}', '2.0.1-0.dirty.{timestamp}'      , '2.0.1~0+dirty{timestamp}+g{commit}'),
    ('2.5.1', None   , 1, True , '2.5.2-1.dirty.{timestamp}+g{commit}', '2.5.2-1.dirty.{timestamp}'      , '2.5.2~1+dirty{timestamp}+g{commit}'),
    ('0.0.0', '1.0.0', 0, False, '0.0.0+g{commit}'                    , '0.0.0'                          , '0.0.0+g{commit}'                   ),
))
def test_version_variables_content(
    capfd,
    run_hopic,
    init_version,
    build,
    commit_count,
    dirty,
    expected_version,
    expected_pure_version,
    expected_debversion,
):
    (result,) = run_hopic(
        ("build",),
        config=dedent(f"""\
                version:
                  format: semver
                  tag:    true
                  bump:   patch
                {('  build: ' + build) if build else ''}

                phases:
                  test:
                    version:
                      - echo ${{VERSION}}
                      - sh -c 'echo $${{VERSION}}'
                      - echo ${{PURE_VERSION}}
                      - sh -c 'echo $${{PURE_VERSION}}'
                      - echo ${{DEBVERSION}}
                      - sh -c 'echo $${{DEBVERSION}}'
                """),
        tag=init_version,
        commit_count=commit_count,
        dirty=dirty,
    )
    assert result.exit_code == 0
    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)

    # Length needs to match the length of a commit hash of `git describe`
    commit_hash = str(result.commit)[:14]

    expected_version = expected_version.format(commit=commit_hash, timestamp='19700108000000')
    expected_pure_version = expected_pure_version.format(commit=commit_hash, timestamp='19700108000000')
    expected_debversion = expected_debversion.format(commit=commit_hash, timestamp='19700108000000')

    assert out.splitlines()[0] == expected_version
    assert out.splitlines()[0] == out.splitlines()[1]
    assert out.splitlines()[2] == expected_pure_version
    assert out.splitlines()[2] == out.splitlines()[3]
    assert out.splitlines()[4] == expected_debversion
    assert out.splitlines()[4] == out.splitlines()[5]


def test_execute_list(monkeypatch, run_hopic):
    def mock_check_call(args, *popenargs, **kwargs):
        assert tuple(args) == ('echo', "an argument, with spaces and ' quotes", 'and-another-without')

    monkeypatch.setattr(subprocess, 'check_call', mock_check_call)
    (result,) = run_hopic(
        ("build",),
        config=dedent('''\
                phases:
                  build:
                    a:
                      - sh: ['echo', "an argument, with spaces and ' quotes", 'and-another-without']
                '''),
    )
    assert result.exit_code == 0


def test_with_credentials_keyring_variable_names(monkeypatch, run_hopic, capfd):
    username = 'test_username'
    password = 'super_secret'
    credential_id = 'test_credentialId'
    project_name = 'test_project'

    def get_credential_id(project_name_arg, cred_id):
        assert credential_id == cred_id
        assert project_name == project_name_arg
        return username, password

    monkeypatch.setattr(credentials, 'get_credential_by_id', get_credential_id)

    (result,) = run_hopic(
        ("build",),
        config=dedent(f'''\
                project-name: {project_name}
                phases:
                  build_and_test:
                    clang-tidy:
                      - with-credentials:
                        - id: {credential_id}
                          type: username-password
                      - sh -c "echo $USERNAME $PASSWORD"
                      - sh -c "echo $$USERNAME $$PASSWORD"
                    coverage:
                      - with-credentials:
                        - id: {credential_id}
                          type: username-password
                          username-variable: 'TEST_USER'
                          password-variable: 'TEST_PASSWORD'
                      - sh -c "echo $TEST_USER $TEST_PASSWORD"
                      - sh -c "echo $$TEST_USER $$TEST_PASSWORD"
                '''),
    )
    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)
    assert out.splitlines()[0] == f'{username} {password}'
    assert out.splitlines()[1] == f'{username} {password}'
    assert out.splitlines()[2] == f'{username} {password}'
    assert out.splitlines()[3] == f'{username} {password}'
    assert result.exit_code == 0


@pytest.mark.parametrize('username, expected_username, password, expected_password', (
    ('test_username', None,                 '$&+,/:;=?@', '%24%26%2B%2C%2F%3A%3B%3D%3F%40'),
    ('señor_tester', 'se%C3%B1or_tester'  , 'password'  , None),
    ('señor_tester', 'se%C3%B1or_tester'  , '$&+,/:;=?@', '%24%26%2B%2C%2F%3A%3B%3D%3F%40'),
    ('señor tester', 'se%C3%B1or%20tester', 'the secret', 'the%20secret'),
))
def test_with_credentials_with_url_encoding(monkeypatch, run_hopic, capfd, username, expected_username, password, expected_password):
    if expected_username is None:
        expected_username = username
    if expected_password is None:
        expected_password = password
    credential_id = 'test_credentialId'
    project_name = 'test_project'

    def get_credential_id(project_name_arg, cred_id):
        assert credential_id == cred_id
        assert project_name == project_name_arg
        return username, password

    monkeypatch.setattr(credentials, 'get_credential_by_id', get_credential_id)

    (result,) = run_hopic(
        ("build",),
        config=dedent(f'''\
                project-name: {project_name}
                phases:
                  build_and_test:
                    clang-tidy:
                      - with-credentials:
                        - id: {credential_id}
                          type: username-password
                          encoding: url
                      - sh -c "echo $USERNAME $PASSWORD"
                '''),
    )
    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)
    assert out.splitlines()[0] == f'{expected_username} {expected_password}'
    assert result.exit_code == 0


def test_dry_run_build(monkeypatch, run_hopic):
    template_build_command = ['build b from template']

    expected = [
        ['https://test.pypi.org/simple/', 'test_template>=42.42'],
        ['[dry-run] would execute:'],
        ['generate doc/build/html/output.txt'],
        template_build_command,
        ['docker run', '/test/dir:/tmp', 'test-image:42.42 invalid command a'],
        ['invalid command b'],
    ]

    def mock_check_call(args, *popenargs, **kwargs):
        assert 'https://test.pypi.org/simple/' in args and 'test_template>=42.42' in args

    monkeypatch.setattr(subprocess, 'check_call', mock_check_call)

    def mock_load_template(*args, **kwargs):
        return [{
            'sh': template_build_command
        }]
    monkeypatch.setattr(config_reader, 'load_yaml_template', mock_load_template)

    (result,) = run_hopic(
        ("build", "--dry-run"),
        config=dedent('''\
pip:
  - with-extra-index:
      - 'https://test.pypi.org/simple/'
    packages:
      - test_template>=42.42

phases:
  build:
    a:
      - worktrees:
          doc/build/html:
            commit-message: "Update documentation"

      - generate doc/build/html/output.txt
    b: !template 'test_template'
  test:
    a:
      - image: test-image:42.42
        volumes:
          - ./test/dir:/tmp
      - invalid command a
    b:
      - invalid command b
'''),
    )
    assert result.exit_code == 0

    for level, msg in result.logs:
        if level < logging.INFO:
            continue
        for expected_string in expected.pop(0):
            assert expected_string in msg
    assert not expected


def test_dry_run_does_not_ask_for_credentials(monkeypatch, run_hopic):
    def get_credential_id(project_name_arg, cred_id):
        assert False, "`get_credential_id` should not have been called in a dry run"

    monkeypatch.setattr(credentials, 'get_credential_by_id', get_credential_id)

    (result,) = run_hopic(
        ("build", "-n"),
        config=dedent('''\
                project-name: dummy
                phases:
                  p1:
                    v1:
                      - with-credentials:
                        - id: my-credentials
                          type: username-password
                      - sh -c 'echo $USERNAME $PASSWORD'
                      - echo $USERNAME $PASSWORD
                '''),
    )

    assert result.exit_code == 0
    assert result.logs[-2][1] == "sh -c 'echo ${USERNAME} ${PASSWORD}'"
    assert result.logs[-1][1] == "echo '${USERNAME}' '${PASSWORD}'"


def test_config_recursive_template_build(monkeypatch, run_hopic):
    extra_index = 'https://test.pypi.org/simple/'
    pkg = 'pipeline-template'
    template_pkg = 'template-in-template'
    expected_check_calls = [['pip', 'install', pkg], ['pip', 'install', template_pkg], ['echo', 'bob']]

    def mock_check_call(args, *popenargs, **kwargs):
        assert all(elem in args for elem in expected_check_calls.pop(0))

    monkeypatch.setattr(subprocess, 'check_call', mock_check_call)

    def template_template(volume_vars):
        return dedent("""\
                      config:
                        phases:
                          test:
                            variant:
                              - echo 'bob'
                        """)

    class TestTemplatePackage:
        name = template_pkg

        def load(self):
            return template_template

    def pipeline_template(volume_vars):
        return dedent(f"""\
                        pip:
                          - with-extra-index: {extra_index}
                            packages:
                              - {template_pkg}

                        config: !template {template_pkg}
                        """)

    class TestPipelinePackage:
        name = pkg

        def load(self):
            return pipeline_template

    def mock_entry_points(*, group: str):
        assert group == "hopic.plugins.yaml"
        return (TestPipelinePackage(), TestTemplatePackage())

    monkeypatch.setattr(metadata, 'entry_points', mock_entry_points)

    (result,) = run_hopic(
        ("build",),
        config=dedent(f"""\
                pip:
                  - with-extra-index: {extra_index}
                    packages:
                      - {pkg}

                config: !template {pkg}
                """),
    )

    assert result.exit_code == 0
    assert expected_check_calls == []


def test_build_list_yaml_template(monkeypatch, run_hopic):
    extra_index = 'https://test.pypi.org/simple/'
    pkg = 'variant-template'
    expected_check_calls = [['pip', 'install', pkg], ['echo', 'bob the builder'], ['echo', 'second command']]

    def mock_check_call(args, *popenargs, **kwargs):
        assert all(elem in args for elem in expected_check_calls.pop(0))

    monkeypatch.setattr(subprocess, 'check_call', mock_check_call)

    def pipeline_template(volume_vars):
        return dedent("""\
                        - echo 'bob the builder'
                        - echo 'second command'
                        """)

    class TestPipelinePackage:
        def __init__(self):
            self.name = f'{pkg}'

        def load(self):
            return pipeline_template

    def mock_entry_points(*, group: str):
        assert group == "hopic.plugins.yaml"
        return (TestPipelinePackage(),)

    monkeypatch.setattr(metadata, 'entry_points', mock_entry_points)

    (result,) = run_hopic(
        ("build",),
        config=dedent(f"""\
                pip:
                  - with-extra-index: {extra_index}
                    packages:
                      - {pkg}

                phases:
                  phase-1:
                    variant-1: !template {pkg}
                """),
    )

    assert result.exit_code == 0
    assert expected_check_calls == []


def test_with_credentials_obfuscation(monkeypatch, capfd, run_hopic):
    username = 'test_username'
    password = '\'#$%123'
    credential_id = 'test_credentialId'
    project_name = 'test_project'

    def get_credential_id(project_name_arg, cred_id):
        assert cred_id == credential_id
        assert project_name_arg == project_name
        return username, password

    monkeypatch.setattr(credentials, 'get_credential_by_id', get_credential_id)

    (result,) = run_hopic(
        ("build",),
        config=dedent(f'''\
                project-name: {project_name}
                phases:
                  build_and_test:
                    clang-tidy:
                      - with-credentials:
                        - id: {credential_id}
                          type: username-password
                      - echo $USERNAME $PASSWORD
                '''),
    )
    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)

    assert out.splitlines()[0] == f'{username} {password}'
    assert any("'${USERNAME}' '${PASSWORD}'" in msg for _, msg in result.logs)
    assert result.exit_code == 0


def test_with_missing_credentials_obfuscation(monkeypatch, capfd, run_hopic):
    credential_id = 'test_credentialId'
    project_name = 'test_project'

    def get_credential_id(*_):
        return None

    monkeypatch.setattr(credentials, 'get_credential_by_id', get_credential_id)

    (result,) = run_hopic(
        ("build",),
        config=dedent(f'''\
                project-name: {project_name}
                phases:
                  build_and_test:
                    clang-tidy:
                      - with-credentials:
                        - id: {credential_id}
                          type: username-password
                      - echo Command without credential to verify missing credential(s)
                      - with-credentials:
                        - id: {credential_id}
                          type: username-password
                      - echo $USERNAME $PASSWORD
                '''),
    )
    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)

    assert result.exit_code != 0


def test_with_credentials_obfuscation_empty_credentials(monkeypatch, capfd, run_hopic):
    def get_credential_id(project_name_arg, cred_id):
        return '', ''

    monkeypatch.setattr(credentials, 'get_credential_by_id', get_credential_id)

    (result,) = run_hopic(
        ("build",),
        config=dedent('''\
                project-name: dummy
                phases:
                  p1:
                    v1:
                      - with-credentials:
                        - id: some_empty_credentials
                          type: username-password
                      - echo $USERNAME $PASSWORD
                '''),
    )

    assert result.exit_code == 0
    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)
    assert out.splitlines()[0] == ' '


@pytest.mark.parametrize('version_config, expected_msg', (
    ('tag: true', r"^Failed to determine the current version from Git tag\."),
    ('file: version.txt', r"^Failed to determine the current version from file\."),
    ('bump: false', r"^Failed to determine the current version\."),
))
def test_version_variable_with_undetermined_version(capfd, run_hopic, version_config, expected_msg):
    (result,) = run_hopic(
        ("build",),
        config=dedent(f'''\
                version:
                  {version_config}
                phases:
                  phase-one:
                    variant-one:
                      - sh -c "set +u; echo VERSION=$$VERSION"
                      - echo $VERSION
                '''),
        tag=None,
    )

    assert isinstance(result.exception, VersioningError)
    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)
    assert out.splitlines()[0] == 'VERSION='
    assert re.search(expected_msg, result.exception.format_message(), re.MULTILINE)


@pytest.mark.parametrize("expected_hash, mtime, include_file", (
    ("c63b283df45487cb0d957e0aa799b9c72f78e45b707da6c3946701a63a514713", "2038-01-19 03:14:08.134210", "include/something/here.hpp"),
    ("a9ffb501db85f6bd1d5544ad2761c1d55449d8d36b9388c7798525a98eebdfe8", "1970-01-03 17:30:01.372010 +0000", "include/something/here.hpp"),
    ("cbbeedaa870244a7d7cdc655ad7ac890d7817f2b0172ca18203c8954b24050c4", "2038-01-19 03:14:08.134210", "include/from-really-long-directory-that-cannot-be-represented-all-that-super-well-in-the-super-teeny-tiny-little-amount-that-is-a-hundred-and-fifty-five-bytes/here.hpp"),  # noqa: E501
))
def test_normalize_artifacts(capfd, expected_hash, include_file, mtime, run_hopic):
    (result,) = run_hopic(
        ("build",),
        config=dedent(
            f"""\
            version:
              tag: true

            phases:
              a:
                x:
                  - archive:
                      artifacts: archive-${{PURE_VERSION}}.tar.gz
                  - mkdir -p {os.path.dirname(include_file)} src
                  - touch -d '{mtime}' {include_file} src/here.cpp
                  - tar czf archive-${{PURE_VERSION}}.tar.gz --format=pax include src
              b:
                x:
                  - sha256sum -b archive-${{PURE_VERSION}}.tar.gz
            """
        ),
        tag="0.0.0",
    )

    assert result.exit_code == 0
    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)
    assert out == f"{expected_hash} *archive-0.0.0.tar.gz\n", "archive's hash should not depend on build time"


@pytest.mark.parametrize("archive_key", (
    "archive",
    "fingerprint",
    "junit",
))
def test_complain_about_missing_artifacts(run_hopic, archive_key):
    (result,) = run_hopic(
        ("build",),
        config=dedent(
            f"""\
            phases:
              a:
                x:
                  - {archive_key}:
                      {'test-results' if archive_key == 'junit' else 'artifacts'}: {archive_key}-doesnotexist.txt
            """
        ),
    )

    assert isinstance(result.exception, MissingFileError)
    msg = result.exception.format_message()
    assert re.search(r"\b[Nn]one of these mandatory .*? patterns matched a file\b", msg)
    assert f"{archive_key}-doesnotexist.txt" in msg


@pytest.mark.parametrize("archive_key", (
    "archive",
    "fingerprint",
    "junit",
))
def test_accept_present_artifacts(run_hopic, archive_key):
    (result,) = run_hopic(
        ("build",),
        config=dedent(
            f"""\
            phases:
              a:
                x:
                  - {archive_key}:
                      {'test-results' if archive_key == 'junit' else 'artifacts'}: {archive_key}-exists.txt
                    sh: touch {archive_key}-exists.txt
            """
        ),
    )

    assert result.exit_code == 0


@pytest.mark.parametrize("archive_key", (
    "archive",
    "fingerprint",
))
def test_permit_missing_artifacts(run_hopic, archive_key):
    (result,) = run_hopic(
        ("build",),
        config=dedent(
            f"""\
            phases:
              a:
                x:
                  - {archive_key}:
                      artifacts: {archive_key}-doesnotexist.txt
                      allow-missing: yes
                    junit:
                      test-results: junit-doesnotexist.xml
                      allow-missing: yes
            """
        ),
    )

    assert result.exit_code == 0


def test_build_times(capfd, run_hopic):
    expected_time = datetime.utcfromtimestamp(int(source_date_epoch)).replace(tzinfo=tzutc())
    expected_duration = 42 * 60 + 42.42

    (result,) = run_hopic(
        ("build",),
        config=dedent(
            """\
            phases:
              a:
                x:
                  - echo ${GIT_COMMIT_TIME} ${BUILD_DURATION}
            """
        ),
        env=dict(
            SOURCE_DATE_EPOCH=str(source_date_epoch + expected_duration),
        ),
    )

    assert result.exit_code == 0
    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)

    git_commit_time, duration = out.split()
    git_commit_time = parse_date(git_commit_time)
    duration = float(duration)

    assert git_commit_time == expected_time
    assert duration == expected_duration


def test_build_identifiers(capfd, run_hopic):
    repo = "something"
    branch = "release/1"
    pr_number = 123
    job_build_number = 42

    expected_build_name = f"{repo}/{branch}"
    expected_build_number = f"PR-{pr_number} {job_build_number}"
    expected_build_url = f"https://some-where-jenkins.example.com/job/{repo}/job/PR-{pr_number}/{job_build_number}/"

    (result,) = run_hopic(
        ("build",),
        config=dedent(
            """\
            phases:
              a:
                x:
                  - echo ${BUILD_NAME}
                  - echo ${BUILD_NUMBER}
                  - echo ${BUILD_URL}
            """
        ),
        env=dict(
            BUILD_NAME=expected_build_name,
            BUILD_NUMBER=expected_build_number,
            BUILD_URL=expected_build_url,
        ),
    )

    assert result.exit_code == 0
    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)

    build_name, build_number, build_url = out.splitlines()

    assert build_name == expected_build_name
    assert build_number == expected_build_number
    assert build_url == expected_build_url


def _timeout_mock_time_monotonic(clock_state):
    clock_state["time"] = clock_state.get("time", 0) + 1e-6
    return clock_state["time"]


def _timeout_mock_check_call(clock_state, args, *popenargs, timeout=None, **kwargs):
    cmd, delay = args
    assert cmd == "sleep"
    delay = float(delay)
    assert delay > 0
    if timeout is not None and delay > timeout:
        raise subprocess.TimeoutExpired(args, timeout)
    clock_state["time"] = clock_state.get("time", 0) + delay


@pytest.mark.parametrize("sleep", (0.002, 0.004, 0.006, 0.008), ids=lambda n: f"sleep={n}")
@pytest.mark.parametrize("timeout", (0.001, 0.003, 0.005, 0.007), ids=lambda n: f"timeout={n}")
def test_local_timeout(monkeypatch, run_hopic, sleep, timeout):
    clock_state = {}
    monkeypatch.setattr(time, "monotonic", functools.partial(_timeout_mock_time_monotonic, clock_state))
    monkeypatch.setattr(subprocess, "check_call", functools.partial(_timeout_mock_check_call, clock_state))

    (result,) = run_hopic(
        ("build",),
        config=dedent(
            f"""\
            phases:
              a:
                x:
                  - timeout: {timeout}
                    sh: sleep {sleep}
            """
        ),
    )
    if sleep < timeout:
        assert result.exception is None
    else:
        assert result.exception is not None
        if not isinstance(result.exception, StepTimeoutExpiredError):
            raise result.exception


def test_global_timeout_expire(monkeypatch, run_hopic):
    clock_state = {}
    monkeypatch.setattr(time, "monotonic", functools.partial(_timeout_mock_time_monotonic, clock_state))
    monkeypatch.setattr(subprocess, "check_call", functools.partial(_timeout_mock_check_call, clock_state))

    (result,) = run_hopic(
        ("build",),
        config=dedent(
            """\
            phases:
              a:
                x:
                  - timeout: 0.006
                  - timeout: 0.002
                    sh: sleep 0.001
                  - timeout: 0.002
                    sh: sleep 0.001
                  - sh: sleep 0.004
            """
        ),
    )
    assert isinstance(result.exception, StepTimeoutExpiredError)

    timeout_msg_re = re.compile(r"\brestrict.*?\bmax.*\bseconds?\b")
    timeout_msgs = tuple(msg for _, msg in result.logs if timeout_msg_re.search(msg))
    assert timeout_msgs, f"Didn't find any timeout related messages matching '{timeout_msg_re.pattern}'"

    assert "global" in timeout_msgs[-1], "timeout expiration wasn't caused by the _global_ timeout"
