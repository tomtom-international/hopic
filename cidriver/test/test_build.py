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

from click.testing import CliRunner
import git
import os
import pytest
import sys


def run_with_config(config, args, files={}):
    runner = CliRunner(mix_stderr=False)
    with runner.isolated_filesystem():
        with git.Repo.init() as repo:
            with open('hopic-ci-config.yaml', 'w') as f:
                f.write(config)
            for fname, content in files.items():
                if '/' in fname and not os.path.exists(os.path.dirname(fname)):
                    os.makedirs(os.path.dirname(fname))
                with open(fname, 'w') as f:
                    f.write(content)
            repo.index.add(('hopic-ci-config.yaml',))
            repo.index.commit(message='Initial commit')
        result = runner.invoke(cli, args)

    if result.stdout_bytes:
        print(result.stdout, end='')
    if result.stderr_bytes:
        print(result.stderr, end='', file=sys.stderr)

    if result.exception is not None:
        raise result.exception

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


def test_with_manifest(capsys):
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
