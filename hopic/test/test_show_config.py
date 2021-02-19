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

from . import hopic_cli

from click.testing import CliRunner
from collections import OrderedDict
from collections.abc import Sequence
import git
import json
import os
from pathlib import Path
import re
import sys
from textwrap import dedent

import pytest


_git_time = f"{7 * 24 * 3600} +0000"
_author = git.Actor('Bob Tester', 'bob@example.net')
_commitargs = dict(
        author_date=_git_time,
        commit_date=_git_time,
        author=_author,
        committer=_author,
    )


def run_with_config(
    config,
    args,
    *,
    files={},
    env=None,
    cfg_file: str = "hopic-ci-config.yaml",
    is_default_cfg_file: bool = False,
):
    runner = CliRunner(mix_stderr=False, env=env)
    with runner.isolated_filesystem():
        with git.Repo.init() as repo:
            if not os.path.isabs(cfg_file) and cfg_file != os.devnull:
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
            repo.index.commit(message='Initial commit', **_commitargs)
        if cfg_file != "hopic-ci-config.yaml" and not is_default_cfg_file:
            args = ('--config', cfg_file) + tuple(args)
        result = runner.invoke(hopic_cli, args)

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


def test_image_from_cfgdir_relative_manifest():
    result = run_with_config('''\
image: !image-from-ivy-manifest
  manifest: ../dependency_manifest.xml
  repository: example.com
  path: example
''', ('show-config',),
        cfg_file='.ci/some-special-config/hopic-ci-config.yaml',
        files={
            '.ci/dependency_manifest.xml': '''\
<?xml version="1.0" encoding="UTF-8"?>
<ivy-module version="2.0">
  <dependencies>
    <dependency name="relative-exemplar" rev="2.7.1">
      <conf mapped="toolchain" name="default" />
    </dependency>
  </dependencies>
</ivy-module>
'''
    })
    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)
    assert output['image']['default'] == 'example.com/example/relative-exemplar:2.7.1'


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


def test_image_in_variant_type_error(capfd):
    result = run_with_config('''\
phases:
  build:
    a:
      - image:
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

    assert re.search(r"^Error: configuration error in '.*?\bhopic-ci-config\.yaml': .*\bimage\b.*\ba\b.*\bmust be\b.*\bstring\b", err, re.MULTILINE)


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
    assert output['version']['bump']['strict'] is False

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


@pytest.mark.parametrize("cfg_file, subdir, name", (
    ("hopic-ci-config.yaml"    , "."  , "hopic-ci-config.yaml"),
    (".ci/hopic-ci-config.yaml", ".ci", "hopic-ci-config.yaml"),
))
def test_default_paths(capfd, cfg_file, subdir, name):
    result = run_with_config(
        dedent(
            """\
            volumes:
              - ${CFGDIR}:/cfg
            """
        ),
        ("show-config",),
        cfg_file=cfg_file,
        is_default_cfg_file=True,
    )

    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)
    workspace = Path(output["volumes"]["/code"]["source"])
    cfgdir = Path(output["volumes"]["/cfg"]["source"])
    assert cfgdir.name != name
    assert cfgdir.relative_to(workspace) == Path(subdir)


def test_default_volume_mapping_set():
    result = run_with_config('', ('show-config',))
    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)
    volumes = output['volumes']

    assert set(volumes.keys()) == {'/code', '/etc/passwd', '/etc/group'}


def test_delete_volumes_from_default_set():
    result = run_with_config(dedent('''\
            volumes:
              - source: null
                target: /etc/passwd
              - source: null
                target: /etc/group
            '''), ('show-config',))
    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)
    volumes = output['volumes']

    assert '/etc/passwd' not in volumes
    assert '/etc/group' not in volumes


def test_disallow_phase_name_reuse(capfd):
    result = run_with_config('''\
phases:
    a: {}
    b: {}
    a:
        x: []
        y: []
''', ('show-config',))

    assert result.exit_code == 32

    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)

    assert re.search(r"^Error: configuration error: [Dd]uplicate entry for key .* mapping is not permitted\b", err, re.MULTILINE)


def test_reject_sequence_in_phase(capfd):
    result = run_with_config('''\
phases:
  a:
    - 'true'
''', ('show-config',))

    assert result.exit_code == 32

    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)

    assert re.search(r"^Error: configuration error in '.*?\bhopic-ci-config\.yaml': phase `a`.*\bmapping\b", err, re.MULTILINE)


def test_reject_mapping_in_variant(capfd):
    result = run_with_config('''\
phases:
  a:
    x:
      o:
        - 'true'
''', ('show-config',))

    assert result.exit_code == 32

    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)

    assert re.search(r"^Error: configuration error in '.*?\bhopic-ci-config\.yaml': variant `a.x`.*\bsequence\b", err, re.MULTILINE)


def test_devnull_config():
    result = run_with_config(None, ('show-config',), cfg_file=os.devnull)
    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)
    assert output


def test_global_config_block():
    result = run_with_config(dedent('''\
                                config:
                                  version:
                                    bump: no

                                  phases:
                                    test-phase:
                                      test-variant:
                                       - echo 'bob the builder'
                                    '''),
                             ('show-config',))

    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)
    assert output['version']['bump']['policy'] == 'disabled'
    assert 'field' not in output['version']['bump']
    print(output)
    assert isinstance(output['phases']['test-phase']['test-variant'], Sequence)


def test_post_submit_type_error(capfd):
    result = run_with_config(dedent('''\
                            post-submit:
                                - echo 'hello Bob'
                            '''), ('show-config',))

    assert result.exit_code == 32

    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)

    assert re.search(r"^Error: configuration error in '.*?\bhopic-ci-config\.yaml': `post-submit` doesn't contain a mapping but a list", err, re.MULTILINE)


def test_post_submit_forbidden_field(capfd):
    result = run_with_config(dedent('''\
                            post-submit:
                              stash-phase:
                                - stash:
                                    includes: stash/stash.tx
                                - echo 'hello Bob'
'''), ('show-config',))

    assert result.exit_code == 32

    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)
    assert "`post-submit`.`stash-phase` contains not permitted field `stash`" in err


def test_post_submit():
    result = run_with_config(dedent('''\
                            post-submit:
                                some-phase:
                                    - echo 'hello Bob'
                            '''), ('show-config',))

    assert result.exit_code == 0


def test_config_is_mapping_failure(capfd):
    result = run_with_config(dedent('''\
                                  - phases:
                                    test-phase:
                                      test-variant:
                                       - echo 'bob the builder'
                                    '''),
                             ('show-config',))
    assert result.exit_code == 32
    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)
    assert re.search(r"^Error: configuration error in '.*?\bhopic-ci-config\.yaml': top level configuration should be a map, but is a list",
                     err, re.MULTILINE)


def test_config_is_mapping_empty():
    result = run_with_config(dedent(''''''), ('show-config',))
    assert result.exit_code == 0
