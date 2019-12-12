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
from collections import OrderedDict
import git
import json
import os
import pytest
import re
import sys


_source_date_epoch = 7 * 24 * 3600


def run_with_config(config, args, files={}, env=None, cfg_file='hopic-ci-config.yaml'):
    runner = CliRunner(mix_stderr=False, env=env)
    with runner.isolated_filesystem():
        with git.Repo.init() as repo:
            if '/' in cfg_file and not os.path.exists(os.path.dirname(cfg_file)):
                os.makedirs(os.path.dirname(cfg_file))
            with open(cfg_file, 'w') as f:
                f.write(config)
            for fname, content in files.items():
                if '/' in fname and not os.path.exists(os.path.dirname(fname)):
                    os.makedirs(os.path.dirname(fname))
                with open(fname, 'w') as f:
                    f.write(content)
            repo.index.add((cfg_file,) + tuple(files.keys()))
            git_time = '{} +0000'.format(_source_date_epoch)
            repo.index.commit(message='Initial commit', author_date=git_time, commit_date=git_time)
        if cfg_file != 'hopic-ci-config.yaml':
            args = ('--config', cfg_file) + tuple(args)
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


def test_default_image():
    result = run_with_config('''\
image: example
''', ('show-config',))
    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)
    assert output['image']['default'] == 'example'


def test_default_image_type_error(capfd):
    result = run_with_config('''\
image: yes
''', ('show-config',))

    assert result.exit_code == 32

    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)

    assert re.search(r"^Error: configuration error in '.*?\bhopic-ci-config\.yaml': .*\bimage\b.*\bmust be\b.*\bstring\b", err, re.MULTILINE)


def test_image_type_error(capfd):
    result = run_with_config('''\
image:
  exemplare:
    repository: example.com
    path: example
    name: exemplar
    rev: 3.1.4
''', ('show-config',))

    assert result.exit_code == 32

    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)

    assert re.search(r"^Error: configuration error in '.*?\bhopic-ci-config\.yaml': .*\bimage\b.*\bexemplare\b.*\bmust be\b.*\bstring\b", err, re.MULTILINE)


def test_bad_version_config(capfd):
    result = run_with_config('''\
version: patch
''', ('show-config',))

    assert result.exit_code == 32

    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)

    assert re.search(r"^Error: configuration error in '.*?\bhopic-ci-config\.yaml': .*\bversion\b.*\bmust be\b.*\bmapping\b", err, re.MULTILINE)


def test_default_version_bumping_config(capfd):
    result = run_with_config('''\
{}
''', ('show-config',))

    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)
    assert output['version']['bump']['policy'] == 'constant'


def test_default_version_bumping_backwards_compatible_policy(capfd):
    result = run_with_config('''\
version:
  bump: patch
''', ('show-config',))

    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)
    assert output['version']['bump']['policy'] == 'constant'
    assert output['version']['bump']['field'] == 'patch'


def test_disabled_version_bumping(capfd):
    result = run_with_config('''\
version:
  bump: no
''', ('show-config',))

    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)
    assert output['version']['bump']['policy'] == 'disabled'
    assert 'field' not in output['version']['bump']


def test_default_conventional_bumping(capfd):
    result = run_with_config('''\
version:
  format: semver
  tag: 'v.{version.major}.{version.minor}.{version.patch}'
  bump:
    policy: conventional-commits
''', ('show-config',))

    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)
    assert output['version']['bump']['policy'] == 'conventional-commits'
    assert output['version']['bump']['strict'] == False

    reject_breaking_changes_on = re.compile(output['version']['bump']['reject-breaking-changes-on'])
    reject_new_features_on = re.compile(output['version']['bump']['reject-new-features-on'])
    for major_branch in (
            'release/42',
            'rel-42',
        ):
        assert reject_breaking_changes_on.match(major_branch)
        assert not reject_new_features_on.match(major_branch)
    for minor_branch in (
            'release/42.21',
            'rel-42.21',
        ):
        assert reject_breaking_changes_on.match(minor_branch)
        assert reject_new_features_on.match(minor_branch)


def test_default_workspace_is_repo_toplevel(capfd):
    """This checks whether the default workspace, when a --config option is given but not a --workspace option,
    is the toplevel directory of the repository the --config file resides in."""
    result = run_with_config('''\
volumes:
  - ${CFGDIR}:/cfg
''', ('show-config',), cfg_file='.ci/some-special-config/hopic-ci-config.yaml')

    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)
    workspace = output['volumes']['/code']['source']
    cfgdir = output['volumes']['/cfg']['source']
    assert not cfgdir.endswith('hopic-ci-config.yaml')
    assert os.path.relpath(workspace, cfgdir) == '../..'
