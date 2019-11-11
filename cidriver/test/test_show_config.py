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
from ..config_reader import ConfigurationError

from click.testing import CliRunner
from collections import OrderedDict
import git
import json
import pytest
import sys


_source_date_epoch = 7 * 24 * 3600


def run_with_config(config, args, files={}, env=None):
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
        result = runner.invoke(cli, args)

    if result.stdout_bytes:
        print(result.stdout, end='')
    if result.stderr_bytes:
        print(result.stderr, end='', file=sys.stderr)

    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception

    return result


def test_image_from_manifest():
    result = run_with_config('''\
image: !image-from-ivy-manifest
  repository: example.com
  path: example
''', ('show-config',),
    files={
        'dependency_manifest.xml': '''\
<?xml version="1.0" encoding="UTF-8"?>
<ivy-module version="2.0">
  <dependencies>
    <dependency name="exemplar" rev="3.1.4">
      <conf mapped="toolchain" name="default" />
    </dependency>
  </dependencies>
</ivy-module>
'''
    })
    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)
    assert output['image']['default'] == 'example.com/example/exemplar:3.1.4'


def test_default_image(capfd):
    result = run_with_config('''\
image: example
''', ('show-config',))
    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)
    assert output['image']['default'] == 'example'


def test_default_image_type_error(capfd):
    with pytest.raises(ConfigurationError, match=r'image\b.*\bmust be\b.*\bstring\b'):
        run_with_config('''\
image: yes
''', ('show-config',))


def test_image_type_error(capfd):
    with pytest.raises(ConfigurationError, match=r'image\b.*\bexemplare\b.*\bmust be\b.*\bstring\b'):
        run_with_config('''\
image:
  exemplare:
    repository: example.com
    path: example
    name: exemplar
    rev: 3.1.4
''', ('show-config',))
