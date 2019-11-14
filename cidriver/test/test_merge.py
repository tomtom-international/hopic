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
import pytest
import sys


_source_date_epoch = 42 * 365 * 24 * 3600
_git_time = '{} +0000'.format(_source_date_epoch)


def run(*args, env=None):
    runner = CliRunner(mix_stderr=False, env=env)
    with runner.isolated_filesystem():
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


def test_autosquash_base(capfd, tmp_path):
    author = git.Actor('Bob Tester', 'bob@example.net')
    commitargs = dict(
            author_date=_git_time,
            commit_date=_git_time,
            author=author,
            committer=author,
        )

    toprepo = tmp_path / 'repo'
    with git.Repo.init(str(toprepo), expand_vars=False) as repo:
        with (toprepo / 'hopic-ci-config.yaml').open('w') as f:
            f.write('''\
version:
  bump: no

phases:
  build:
    test:
      - foreach: AUTOSQUASHED_COMMIT
        sh: git log -1 --format=%P ${AUTOSQUASHED_COMMIT}
''')
        repo.index.add(('hopic-ci-config.yaml',))
        base_commit = repo.index.commit(message='Initial commit', **commitargs)

        # Main branch moves on
        with (toprepo / 'A.txt').open('w') as f:
            f.write('A')
        repo.index.add(('A.txt',))
        final_commit = repo.index.commit(message='feat: add A', **commitargs)

        # PR branch from just before the main branch's HEAD
        repo.head.reference = repo.create_head('something-useful', base_commit)
        assert not repo.head.is_detached
        repo.head.reset(index=True, working_tree=True)

        # Some change
        with (toprepo / 'something.txt').open('w') as f:
            f.write('usable')
        repo.index.add(('something.txt',))
        repo.index.commit(message='feat: add something useful', **commitargs)

        # A fixup on top of that change
        with (toprepo / 'something.txt').open('w') as f:
            f.write('useful')
        repo.index.add(('something.txt',))
        repo.index.commit(message='fixup! feat: add something useful', **commitargs)

    # Successful checkout and build
    result = run(
            ('checkout-source-tree', '--target-remote', str(toprepo), '--target-ref', 'master'),
            ('prepare-source-tree',
                'merge-change-request', '--source-remote', str(toprepo), '--source-ref', 'something-useful'),
            ('build',),
        )
    assert result.exit_code == 0

    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)

    build_out = ''.join(out.splitlines(keepends=True)[2:])
    autosquashed_commits = build_out.split()
    assert str(final_commit) not in autosquashed_commits
    assert str(base_commit) in autosquashed_commits
