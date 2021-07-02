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
from textwrap import dedent
import json
import os
import stat


def test_order(run_hopic):
    """
    The order of phase/variant combinations must be the same in the output JSON as in the config.
    """

    (result,) = run_hopic(
        ("getinfo",),
        config='''\
phases:
  build:
    a:
      - sh: ./build.sh a
    b:
      - sh: ./build.sh b
  test:
    a:
      - sh: ./test.sh a
  upload:
    a:
      - sh: ./upload.sh a
    b:
      - sh: ./upload.sh a
''',
    )

    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)

    assert tuple(output.keys()) == ('build', 'test', 'upload')
    assert tuple(output['build' ].keys()) == ('a', 'b')         # noqa: E202
    assert tuple(output['test'  ].keys()) == ('a',)             # noqa: E202
    assert tuple(output['upload'].keys()) == ('a', 'b')


def test_variants_without_metadata(run_hopic):
    """
    Phase/variant combinations without meta data should still appear in the output JSON.
    """

    (result,) = run_hopic(
        ("getinfo",),
        config='''\
phases:
  build:
    a:
      - ./build.sh a
    b:
      - sh: ./build.sh b
  test:
    a:
      - ./test.sh a
  upload:
    a:
      - ./upload.sh a
    b:
      - sh: ./upload.sh a
''',
    )

    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)

    assert 'build'  in output  # noqa: E272
    assert 'test'   in output  # noqa: E272
    assert 'upload' in output  # noqa: E272

    assert 'a' in output['build']
    assert 'b' in output['build']
    assert 'a' in output['test']
    assert 'a' in output['upload']
    assert 'b' in output['upload']


def test_with_credentials_format(run_hopic):
    (result,) = run_hopic(
        ("getinfo",),
        config=dedent(
            '''\
    phases:
      build:
        a:
        - with-credentials: test_id
        - with-credentials:
            id: second_id
        - with-credentials:
            - id: third_id
            - id: fourth_id
            '''
        ),
    )

    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)

    with_credentials = output['build']['a']['with-credentials']
    assert isinstance(with_credentials, Sequence)
    assert len(with_credentials) == 4
    assert 'test_id' in with_credentials[0]['id']
    assert 'second_id' in with_credentials[1]['id']
    assert 'third_id' in with_credentials[2]['id']
    assert 'fourth_id' in with_credentials[3]['id']


def test_embed_variants_file(run_hopic):
    generate_script_path = "generate-variants.py"
    (result,) = run_hopic(
        ("getinfo",),
        config=dedent(
            f'''\
            phases:
              build:
                a: []

              test: !embed
                cmd: {generate_script_path}
            '''
        ),
        files={
            generate_script_path: (
                dedent(
                    '''\
            #!/usr/bin/env python3

            print(\'\'\'test-variant:
              - echo Bob the builder\'\'\')
                    '''
                ),
                lambda fname: os.chmod(fname, os.stat(fname).st_mode | stat.S_IEXEC),
            ),
        },
    )

    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)

    assert 'build' in output
    assert 'a' in output['build']
    assert 'test' in output
    assert 'test-variant' in output['test']


def test_embed_variants_non_existing_file(run_hopic):
    generate_script_path = "generate-variants.py"
    (result,) = run_hopic(
        ("getinfo",),
        config=dedent(
            f'''\
            phases:
              build:
                a: []

              test: !embed
                cmd: {generate_script_path}
            '''
        ),
    )

    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)

    assert 'build' in output
    assert 'a' in output['build']
    assert 'test' in output
    assert 'error-variant' in output['test']


def test_embed_variants_error_in_file(run_hopic):
    generate_script_path = "generate-variants.py"
    (result,) = run_hopic(
        ("getinfo",),
        config=dedent(
            f'''\
            phases:
              build:
                a: []

              test: !embed
                cmd: {generate_script_path}
            '''
        ),
        files={
            generate_script_path: (
                dedent(
                    '''\
            #!/usr/bin/env python3
            print(\'\'\'test-variant:
            error\'\'\')
                    '''
                ),
                lambda fname: os.chmod(fname, os.stat(fname).st_mode | stat.S_IEXEC),
            ),
        },
    )

    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)

    assert 'build' in output
    assert 'a' in output['build']
    assert 'test' in output
    assert 'error-variant' in output['test']


def test_embed_variants_script_with_arguments(run_hopic):
    generate_script_path = "generate-variants.py"
    generate_script_args = 'argument-variant'
    (result,) = run_hopic(
        ("getinfo",),
        config=dedent(
            f'''\
            phases:
              test: !embed
                cmd: '{generate_script_path} {generate_script_args}'
            '''
        ),
        files={
            generate_script_path: (
                dedent(
                    '''\
            #!/usr/bin/env python3
            import sys

            print(\'\'\'test-%s:
              - echo Bob the builder\'\'\' % sys.argv[1])
                    '''
                ),
                lambda fname: os.chmod(fname, os.stat(fname).st_mode | stat.S_IEXEC),
             ),
        },
    )

    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)

    assert 'test' in output
    assert f'test-{generate_script_args}' in output['test']


def test_embed_variants_cmd(run_hopic):
    cmd = dedent("'printf \"%s\"'" % '''test-variant:\n
  - Bob the builder''')

    (result,) = run_hopic(
        ("getinfo",),
        config=dedent(
            f'''\
                phases:
                  test: !embed
                    cmd: {cmd}
            '''
        ),
    )

    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)

    assert 'test' in output
    assert 'test-variant' in output['test']


def test_wait_on_full_previous_phase_dependency(run_hopic):
    (result,) = run_hopic(
        ("getinfo",),
        config=dedent(
            """\
            phases:
              x:
                a:
                  - touch sheep
                b:
                  - touch monkey
              y:
                a:
                  - cat sheep
                b:
                  - wait-on-full-previous-phase: no
                c:
                  - touch pig
            """
        ),
    )

    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)

    assert 'wait-on-full-previous-phase' in output['y']['b']
    assert not output['y']['b']['wait-on-full-previous-phase']


def test_mark_nops(run_hopic):
    (result,) = run_hopic(
        ("getinfo",),
        config=dedent(
            """\
            phases:
              x:
                a: []
              y:
                a:
                  - wait-on-full-previous-phase: no
            """
        ),
    )

    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)

    assert output["x"]["a"]["nop"] is True
    assert output["y"]["a"]["nop"] is True


def test_variant_timeout(run_hopic):
    (result,) = run_hopic(
        ("getinfo",),
        config=dedent(
            """\
            phases:
              x:
                a:
                  - timeout: 90
                b: []
                c:
                  - timeout: 5
                    sh: echo mooh
                d:
                  - timeout: 4.2
                  - sh: echo mooh
            """
        ),
    )

    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)

    assert output["x"]["a"]["timeout"] == 90
    assert "timeout" not in output["x"]["b"]
    assert "timeout" not in output["x"]["c"]
    assert output["x"]["d"]["timeout"] == 4.2


def test_post_submit_summed_timeout(run_hopic):
    (result,) = run_hopic(
        ("getinfo", "--post-submit"),
        config=dedent(
            """\
            post-submit:
              a:
                - timeout: 42
                - timeout: 7
                  sh: echo mooh
              b:
                - timeout: 37
            """
        ),
    )

    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)

    assert output["timeout"] == 42 + 37
