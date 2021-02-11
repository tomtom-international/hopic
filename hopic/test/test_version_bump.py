# Copyright (c) 2020 - 2021 TomTom N.V. (https://tomtom.com)
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
import pytest
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


def run(*args, env=None):
    runner = CliRunner(mix_stderr=False, env=env)
    with runner.isolated_filesystem():
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


@pytest.mark.parametrize('version_build', ('1.2.3', None))
@pytest.mark.parametrize('version_file', ('revision.txt', None))
def test_conventional_bump(version_build, version_file, monkeypatch, tmp_path):
    toprepo = tmp_path / 'repo'
    init_version = f'0.0.0+{version_build}' if version_build else '0.0.0'
    with git.Repo.init(str(toprepo), expand_vars=False) as repo:
        cfg_file = 'hopic-ci-config.yaml'

        with (toprepo / cfg_file).open('w') as fp:
            fp.write(dedent(f"""\
                    version:
                      tag: yes
                      format: semver
                      bump:
                        policy: conventional-commits
                        strict: yes
                        on-every-change: no
                    {('  file: ' + version_file) if version_file else ''}
                    {('  build: ' + version_build) if version_build else ''}

                    phases:
                      style:
                        commit-messages: !template "commisery"
                    """))
        if version_file:
            with (toprepo / version_file).open('w') as fp:
                fp.write(f'version={init_version}\n')
            repo.index.add((version_file,))

        repo.index.add((cfg_file,))
        base_commit = repo.index.commit(message='Initial commit', **_commitargs)
        repo.git.branch('master', move=True)
        repo.create_tag(init_version)

        base_commit = repo.index.commit(message='Invalid For Conventional Commits', **_commitargs)

        # PR branch
        repo.head.reference = repo.create_head('something-useful', base_commit)
        assert not repo.head.is_detached
        repo.head.reset(index=True, working_tree=True)

        repo.index.commit(message='fix: something useful', **_commitargs)

        repo.git.checkout('master')
        repo.git.merge('something-useful', no_ff=True, no_commit=True, env={
            'GIT_COMMITTER_NAME': 'My Name is Nobody',
            'GIT_COMMITTER_EMAIL': 'nobody@example.com',
        })
        repo.index.commit(message='Merge #1: feat: something useful', **_commitargs)

        expected_version = '0.1.0'
        expected_tag = expected_version + (f'+{version_build}' if version_build else '')

        # Make sure we're not on master: it would make the 'git push' from 'hopic submit' fail
        repo.git.checkout('something-useful')

    # Successful checkout and bump
    results = list(run(
            ('checkout-source-tree', '--target-remote', str(toprepo), '--target-ref', 'master'),
            ('prepare-source-tree',
                '--author-date', f"@{_git_time}",
                '--commit-date', f"@{_git_time}",
                '--author-name', _author.name,
                '--author-email', _author.email,
                'bump-version'),
            ('build',),
            ('submit',),
        ))

    assert all(result.exit_code == 0 for result in results)
    assert results[1].stdout.splitlines()[-1].split('+')[0] == expected_version

    with git.Repo(str(toprepo)) as repo:
        # Switch back to master to be able to easily look at its contents
        repo.git.checkout('master')

        assert repo.tags[expected_tag].commit == repo.head.commit
        if version_file:
            with (toprepo / version_file).open('r') as fp:
                assert fp.read() == f'version={expected_version}\n'


def test_bump_skipped_when_no_new_commits(monkeypatch, tmp_path):
    toprepo = tmp_path / 'repo'
    with git.Repo.init(str(toprepo), expand_vars=False) as repo:
        cfg_file = 'hopic-ci-config.yaml'

        with (toprepo / cfg_file).open('w') as fp:
            fp.write(dedent("""\
                    version:
                      tag: yes
                      format: semver
                      bump:
                        policy: conventional-commits
                        strict: yes
                        on-every-change: no

                    phases:
                      style:
                        commit-messages: !template "commisery"
                    """))
        repo.index.add((cfg_file,))
        repo.index.commit(message='Initial commit', **_commitargs)
        repo.git.branch('master', move=True)
        repo.create_tag('0.0.0')

    # Successful checkout and bump
    *_, result = run(
            ('checkout-source-tree', '--target-remote', str(toprepo), '--target-ref', 'master'),
            ('prepare-source-tree',
                '--author-date', f"@{_git_time}",
                '--commit-date', f"@{_git_time}",
                '--author-name', _author.name,
                '--author-email', _author.email,
                'bump-version'),
        )

    assert result.exit_code == 0
    assert result.stdout == ''
    assert "Not bumping because no new commits are present since the last tag '0.0.0'" in result.stderr.splitlines()


def test_bump_skipped_when_no_bumpable_commits(monkeypatch, tmp_path):
    toprepo = tmp_path / 'repo'
    with git.Repo.init(str(toprepo), expand_vars=False) as repo:
        cfg_file = 'hopic-ci-config.yaml'

        with (toprepo / cfg_file).open('w') as fp:
            fp.write(dedent("""\
                    version:
                      tag: yes
                      format: semver
                      bump:
                        policy: conventional-commits
                        strict: yes
                        on-every-change: no

                    phases:
                      style:
                        commit-messages: !template "commisery"
                    """))
        repo.index.add((cfg_file,))
        repo.index.commit(message='Initial commit', **_commitargs)
        repo.git.branch('master', move=True)
        repo.create_tag('0.0.0')

        repo.index.commit(message='ci: bla bla', **_commitargs)

    # Successful checkout and bump
    *_, result = run(
            ('checkout-source-tree', '--target-remote', str(toprepo), '--target-ref', 'master'),
            ('prepare-source-tree',
                '--author-date', f"@{_git_time}",
                '--commit-date', f"@{_git_time}",
                '--author-name', _author.name,
                '--author-email', _author.email,
                'bump-version'),
        )

    assert result.exit_code == 0
    assert result.stdout == ''
    assert ("error: Version bumping requested, but the version policy 'conventional-commits' decided not to bump from '0.0.1-1+gee3642c057a2af'"
            in result.stderr.splitlines())
