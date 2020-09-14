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

from . import hopic_cli

from click.testing import CliRunner
import git
import subprocess
import sys
from textwrap import dedent


_git_time = f"{42 * 365 * 24 * 3600} +0000"
_author = git.Actor('Bob Tester', 'bob@example.net')
_commitargs = dict(
        author_date=_git_time,
        commit_date=_git_time,
        author=_author,
        committer=_author,
    )


def run_with_config(config, *args, env=None):
    runner = CliRunner(mix_stderr=False, env=env)
    with runner.isolated_filesystem():
        with git.Repo.init() as repo:
            with open('hopic-ci-config.yaml', 'w') as f:
                f.write(config)
            repo.index.add(('hopic-ci-config.yaml',))
            repo.index.commit(message='Initial commit', **_commitargs)
            repo.create_tag('0.0.0')
        for arg in args:
            result = runner.invoke(hopic_cli, arg)

            if result.stdout_bytes:
                print(result.stdout, end='')
            if result.stderr_bytes:
                print(result.stderr, end='', file=sys.stderr)

            if result.exception is not None and not isinstance(result.exception, SystemExit):
                raise result.exception

            yield result

            if result.exit_code != 0:
                return


def test_install_extensions_from_multiple_indices(monkeypatch):
    expected_pkgs = [
            ('--extra-index-url', 'https://test.pypi.org/simple/', 'hopic>=1.19<2'    ,),
            ('--index-url'      , 'https://test.pypi.org/simple/', 'commisery>=0.2,<1',),
            (                                                      'flake8'           ,),  # noqa: E201
        ]

    def mock_check_call(args, *popenargs, **kwargs):
        pkg = expected_pkgs.pop(0)

        if '--user' in args:
            args.remove('--user')
        if '--verbose' in args:
            args.remove('--verbose')
        # del ['-c', constraints_file]
        del args[4:6]

        assert [*args] == [sys.executable, '-m', 'pip', 'install', *pkg]

    monkeypatch.setattr(subprocess, 'check_call', mock_check_call)

    result, = run_with_config(
        dedent(f"""\
                pip:
                  - with-extra-index:
                      - {expected_pkgs[0][1]}
                    packages:
                      - {expected_pkgs[0][2]}
                  - from-index: {expected_pkgs[1][1]}
                    packages:
                      - {expected_pkgs[1][2]}
                  - {expected_pkgs[2][0]}
                """),
        ('install-extensions',))

    assert result.exit_code == 0
    assert not expected_pkgs
