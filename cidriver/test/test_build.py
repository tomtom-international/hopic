# Copyright (c) 2019 - 2019 TomTom N.V. (https://tomtom.com)
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

from __future__ import print_function
from ..cli import cli
from .markers import *

from click.testing import CliRunner
import git
import os
import pytest
import subprocess
import sys


_source_date_epoch = 7 * 24 * 3600


def run_with_config(config, *args, files={}, env=None):
    runner = CliRunner(mix_stderr=False, env=env)
    with runner.isolated_filesystem():
        with git.Repo.init() as repo:
            with open('hopic-ci-config.yaml', 'w') as f:
                f.write(config)
            for fname, content in files.items():
                if '/' in fname and not os.path.exists(os.path.dirname(fname)):
                    os.makedirs(os.path.dirname(fname))
                with open(fname, 'w') as f:
                    f.write(content)
            repo.index.add(('hopic-ci-config.yaml',) + tuple(files.keys()))
            git_time = '{} +0000'.format(_source_date_epoch)
            repo.index.commit(message='Initial commit', author_date=git_time, commit_date=git_time)
        for arg in args:
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


def test_with_manifest(monkeypatch):
    def mock_check_call(args, *popenargs, **kwargs):
        assert args[0] == 'docker'
        assert tuple(args[-3:]) == ('buildpack-deps:18.04', 'cat', '/etc/lsb-release')

    with monkeypatch.context() as m:
        m.setattr(subprocess, 'check_call', mock_check_call)
        result = run_with_config('''\
image: !image-from-ivy-manifest {}

phases:
  build:
    test:
      - cat /etc/lsb-release
''', ('build',),
    files={
        'dependency_manifest.xml': '''\
<?xml version="1.0" encoding="UTF-8"?>
<ivy-module version="2.0">
  <dependencies>
    <dependency name="buildpack-deps" rev="18.04">
      <conf mapped="toolchain" name="default" />
    </dependency>
  </dependencies>
</ivy-module>
'''
    })
    assert result.exit_code == 0


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


@docker
def test_container_with_env_var():
    result = run_with_config('''\
image: buildpack-deps:18.04

pass-through-environment-vars:
  - THE_ENVIRONMENT

phases:
  build:
    test:
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
