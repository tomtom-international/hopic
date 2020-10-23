# Copyright (c) 2020 - 2020 TomTom N.V. (https://tomtom.com)
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

from .. import execution
import hopic
import click
import copy
import shlex
import subprocess
import sys


def test_echo_cmd_dry_run_argument_parsing(monkeypatch):
    expected_cmd = ['echo', 'Bob the builder']
    expected_args = ['a', 'b', 'c']
    expected_kwargs = {'d': 'test string', 'e': 'different string'}

    def mock_check_call(arg, *args, **kwargs):
        assert arg == expected_cmd
        args_array = list(args)
        for element in args:
            assert expected_args.pop(0) in args
            args_array.remove(element)
        assert args_array == []

        for kwarg in copy.deepcopy(expected_kwargs):
            assert kwarg in kwargs
            assert kwargs[kwarg] == expected_kwargs[kwarg]
            del expected_kwargs[kwarg]

    monkeypatch.setattr(subprocess, 'check_call', mock_check_call)
    execution.echo_cmd(subprocess.check_call, expected_cmd, *expected_args, dry_run=False, **expected_kwargs)
    assert expected_args == []
    assert expected_kwargs == {}


def test_echo_cmd_dry_run(capfd, monkeypatch):
    expected_cmd = ['echo']
    expected_cmd_args = ['-e', 'Bob the builder']

    def mock_check_call(arg, *args, **kwargs):
        assert False

    monkeypatch.setattr(subprocess, 'check_call', mock_check_call)
    execution.echo_cmd(subprocess.check_call, expected_cmd + expected_cmd_args, **{'a': 'aa', 'b': 'bbb'}, dry_run=True)
    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)
    assert ' '.join(expected_cmd) + ' ' + ' '.join([shlex.quote(x) for x in expected_cmd_args]) in err


def test_echo_cmd_return_value():
    def mock_executor(arg, *args, **kwargs):
        assert arg == 'command'
        return args[0]

    ctx = click.Context(hopic.cli.getinfo)
    with ctx:
        assert execution.echo_cmd_click(mock_executor, 'command', '42') == '42'
        assert execution.echo_cmd_click(mock_executor, 'command', b'42') == '42'
