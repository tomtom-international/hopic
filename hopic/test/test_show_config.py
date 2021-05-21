# Copyright (c) 2019 - 2021 TomTom N.V.
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

from collections import OrderedDict
from collections.abc import Sequence
import json
import os
from pathlib import Path
import re
from textwrap import dedent

import pytest

from . import config_file
from ..errors import ConfigurationError


def test_image_from_manifest(run_hopic):
    (result,) = run_hopic(
        ("show-config",),
        config='''\
image: !image-from-ivy-manifest
  repository: example.com
  path: example
''',
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
        },
    )
    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)
    assert output['image']['default'] == 'example.com/example/exemplar:3.1.4'


def test_image_from_cfgdir_relative_manifest(run_hopic):
    cfg_file = ".ci/some-special-config/hopic-ci-config.yaml"
    (result,) = run_hopic(
        ("--config", cfg_file, "show-config"),
        config=config_file(
            cfg_file,
            '''\
image: !image-from-ivy-manifest
  manifest: ../dependency_manifest.xml
  repository: example.com
  path: example
'''),
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
        },
    )
    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)
    assert output['image']['default'] == 'example.com/example/relative-exemplar:2.7.1'


def test_default_image(run_hopic):
    (result,) = run_hopic(
        ("show-config",),
        config='''\
image: example
''',
    )
    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)
    assert output['image']['default'] == 'example'


def test_default_image_type_error(run_hopic):
    (result,) = run_hopic(
        ("show-config",),
        config='''\
image: yes
''',
    )

    assert isinstance(result.exception, ConfigurationError)
    err = result.exception.format_message()
    assert re.search(r"^configuration error in '.*?\bhopic-ci-config\.yaml': .*\bimage\b.*\bmust be\b.*\bstring\b", err, re.MULTILINE)


def test_image_type_error(run_hopic):
    (result,) = run_hopic(
        ("show-config",),
        config='''\
image:
  exemplare:
    repository: example.com
    path: example
    name: exemplar
    rev: 3.1.4
''',
    )

    assert isinstance(result.exception, ConfigurationError)
    err = result.exception.format_message()
    assert re.search(r"^configuration error in '.*?\bhopic-ci-config\.yaml': .*\bimage\b.*\bexemplare\b.*\bmust be\b.*\bstring\b", err, re.MULTILINE)


def test_image_in_variant_type_error(run_hopic):
    (result,) = run_hopic(
        ("show-config",),
        config='''\
phases:
  build:
    a:
      - image:
          exemplare:
            repository: example.com
            path: example
            name: exemplar
            rev: 3.1.4
''',
    )

    assert isinstance(result.exception, ConfigurationError)
    err = result.exception.format_message()
    assert re.search(r"^configuration error in '.*?\bhopic-ci-config\.yaml': .*\bimage\b.*\ba\b.*\bmust be\b.*\bstring\b", err, re.MULTILINE)


def test_bad_version_config(run_hopic):
    (result,) = run_hopic(
        ("show-config",),
        config='''\
version: patch
''',
    )

    assert isinstance(result.exception, ConfigurationError)
    err = result.exception.format_message()
    assert re.search(r"^configuration error in '.*?\bhopic-ci-config\.yaml': .*\bversion\b.*\bmust be\b.*\bmapping\b", err, re.MULTILINE)


def test_default_version_bumping_config(run_hopic):
    (result,) = run_hopic(
        ("show-config",),
        config='''\
{}
''',
    )

    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)
    assert output['version']['bump']['policy'] == 'constant'


def test_default_version_bumping_backwards_compatible_policy(run_hopic):
    (result,) = run_hopic(
        ("show-config",),
        config='''\
version:
  bump: patch
''',
    )

    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)
    assert output['version']['bump']['policy'] == 'constant'
    assert output['version']['bump']['field'] == 'patch'


def test_disabled_version_bumping(run_hopic):
    (result,) = run_hopic(
        ("show-config",),
        config='''\
version:
  bump: no
''',
    )

    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)
    assert output['version']['bump']['policy'] == 'disabled'
    assert 'field' not in output['version']['bump']


def test_default_conventional_bumping(run_hopic):
    (result,) = run_hopic(
        ("show-config",),
        config='''\
version:
  format: semver
  tag: 'v.{version.major}.{version.minor}.{version.patch}'
  bump:
    policy: conventional-commits
''',
    )

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


def test_default_workspace_is_repo_toplevel(run_hopic):
    """This checks whether the default workspace, when a --config option is given but not a --workspace option,
    is the toplevel directory of the repository the --config file resides in."""
    cfg_file = ".ci/some-special-config/hopic-ci-config.yaml"
    (result,) = run_hopic(
        ("--config", cfg_file, "show-config"),
        config=config_file(
            cfg_file,
            '''\
volumes:
  - ${CFGDIR}:/cfg
'''),
    )

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
def test_default_paths(run_hopic, cfg_file, subdir, name):
    (result,) = run_hopic(
        ("show-config",),
        config=config_file(
            cfg_file,
            dedent(
                """\
            volumes:
              - ${CFGDIR}:/cfg
                """
            ),
        ),
    )

    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)
    workspace = Path(output["volumes"]["/code"]["source"])
    cfgdir = Path(output["volumes"]["/cfg"]["source"])
    assert cfgdir.name != name
    assert cfgdir.relative_to(workspace) == Path(subdir)


def test_default_volume_mapping_set(run_hopic):
    (result,) = run_hopic(("show-config",), config="")
    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)
    volumes = output['volumes']

    assert set(volumes.keys()) == {'/code', '/etc/passwd', '/etc/group'}


def test_delete_volumes_from_default_set(run_hopic):
    (result,) = run_hopic(
        ("show-config",),
        config=dedent(
            '''\
            volumes:
              - source: null
                target: /etc/passwd
              - source: null
                target: /etc/group
            '''
        ),
    )
    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)
    volumes = output['volumes']

    assert '/etc/passwd' not in volumes
    assert '/etc/group' not in volumes


def test_disallow_phase_name_reuse(run_hopic):
    (result,) = run_hopic(
        ("show-config",),
        config='''\
phases:
    a: {}
    b: {}
    a:
        x: []
        y: []
''',
    )

    assert isinstance(result.exception, ConfigurationError)
    err = result.exception.format_message()
    assert re.search(r"^configuration error: [Dd]uplicate entry for key .* mapping is not permitted\b", err, re.MULTILINE)


def test_reject_sequence_in_phase(run_hopic):
    (result,) = run_hopic(
        ("show-config",),
        config='''\
phases:
  a:
    - 'true'
''',
    )

    assert isinstance(result.exception, ConfigurationError)
    err = result.exception.format_message()
    assert re.search(r"^configuration error in '.*?\bhopic-ci-config\.yaml': phase `a`.*\bmapping\b", err, re.MULTILINE)


def test_reject_mapping_in_variant(run_hopic):
    (result,) = run_hopic(
        ("show-config",),
        config='''\
phases:
  a:
    x:
      o:
        - 'true'
''',
    )

    assert isinstance(result.exception, ConfigurationError)
    err = result.exception.format_message()
    assert re.search(r"^configuration error in '.*?\bhopic-ci-config\.yaml': variant `a.x`.*\bsequence\b", err, re.MULTILINE)


def test_devnull_config(run_hopic):
    (result,) = run_hopic(("--config", os.devnull, "show-config"))
    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)
    assert output


def test_global_config_block(run_hopic):
    (result,) = run_hopic(
        ("show-config",),
        config=dedent(
                                """\
                                config:
                                  version:
                                    bump: no

                                  phases:
                                    test-phase:
                                      test-variant:
                                       - echo 'bob the builder'
                                """
        ),
    )

    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)
    assert output['version']['bump']['policy'] == 'disabled'
    assert 'field' not in output['version']['bump']
    print(output)
    assert isinstance(output['phases']['test-phase']['test-variant'], Sequence)


def test_post_submit_type_error(run_hopic):
    (result,) = run_hopic(
        ("show-config",),
        config=dedent(
            """\
                            post-submit:
                                - echo 'hello Bob'
            """
        ),
    )

    assert isinstance(result.exception, ConfigurationError)
    err = result.exception.format_message()
    assert re.search(r"^configuration error in '.*?\bhopic-ci-config\.yaml': `post-submit` doesn't contain a mapping but a list", err, re.MULTILINE)


def test_post_submit_forbidden_field(run_hopic):
    (result,) = run_hopic(
        ("show-config",),
        config=dedent(
            """\
                            post-submit:
                              stash-phase:
                                - stash:
                                    includes: stash/stash.tx
                                - echo 'hello Bob'
            """
        ),
    )

    assert isinstance(result.exception, ConfigurationError)
    assert "`post-submit`.`stash-phase` contains not permitted field `stash`" in result.exception.format_message()


def test_post_submit(run_hopic):
    (result,) = run_hopic(
        ("show-config",),
        config=dedent(
            """\
                            post-submit:
                                some-phase:
                                    - echo 'hello Bob'
            """
        ),
    )

    assert result.exit_code == 0


def test_config_is_mapping_failure(run_hopic):
    (result,) = run_hopic(
        ("show-config",),
        config=dedent(
            """\
                                  - phases:
                                    test-phase:
                                      test-variant:
                                       - echo 'bob the builder'
            """
        ),
    )
    assert isinstance(result.exception, ConfigurationError)
    assert re.search(
        r"configuration error in '.*?\bhopic-ci-config\.yaml': top level configuration should be a map, but is a list",
        result.exception.format_message(),
        re.MULTILINE,
    )


def test_config_is_mapping_empty(run_hopic):
    (result,) = run_hopic(("show-config",), config="")
    assert result.exit_code == 0
