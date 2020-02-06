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

from ..cli import cli

from click.testing import CliRunner
from collections import OrderedDict
from collections.abc import Sequence
import json
import sys

def run_with_config(config, args):
    runner = CliRunner(mix_stderr=False)
    with runner.isolated_filesystem():
        with open('hopic-ci-config.yaml', 'w') as f:
            f.write(config)
        result = runner.invoke(cli, args)

    if result.stdout_bytes:
        print(result.stdout, end='')
    if result.stderr_bytes:
        print(result.stderr, end='', file=sys.stderr)

    return result

def test_order():
    """
    The order of phase/variant combinations must be the same in the output JSON as in the config.
    """

    result = run_with_config('''\
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
''', ('getinfo',))

    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)

    assert tuple(output.keys()) == ('build', 'test', 'upload')
    assert tuple(output['build' ].keys()) == ('a', 'b')
    assert tuple(output['test'  ].keys()) == ('a',)
    assert tuple(output['upload'].keys()) == ('a', 'b')

def test_variants_without_metadata():
    """
    Phase/variant combinations without meta data should still appear in the output JSON.
    """

    result = run_with_config('''\
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
''', ('getinfo',))

    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)

    assert 'build'  in output
    assert 'test'   in output
    assert 'upload' in output

    assert 'a' in output['build']
    assert 'b' in output['build']
    assert 'a' in output['test']
    assert 'a' in output['upload']
    assert 'b' in output['upload']


def test_with_credentials_format():
    result = run_with_config('''\
    phases:
      build:
        a:
        - with-credentials: test_id
        - with-credentials:
            id: second_id
        - with-credentials:
            - id: third_id
            - id: fourth_id
    ''', ('getinfo',))

    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)

    with_credentials = output['build']['a']['with-credentials']
    assert isinstance(with_credentials, Sequence)
    assert len(with_credentials) == 4
    assert 'test_id' in with_credentials[0]['id']
    assert 'second_id' in with_credentials[1]['id']
    assert 'third_id' in with_credentials[2]['id']
    assert 'fourth_id' in with_credentials[3]['id']
