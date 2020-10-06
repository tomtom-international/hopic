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
from textwrap import dedent

import git
import os
import pytest
import sys


_git_time = f"{7 * 24 * 3600} +0000"
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

            if result.exit_code != 0:
                return result

    return result


def test_clean_submodule_checkout(capfd, tmp_path):
    author = git.Actor('Bob Tester', 'bob@example.net')
    commitargs = dict(
            author_date=_git_time,
            commit_date=_git_time,
            author=author,
            committer=author,
        )

    dummy_content = 'Lalalala!\n'
    subrepo = tmp_path / 'subrepo'
    with git.Repo.init(str(subrepo), expand_vars=False) as repo:
        with (subrepo / 'dummy.txt').open('w') as f:
            f.write(dummy_content)
        repo.index.add(('dummy.txt',))
        repo.index.commit(message='Initial dummy commit', **commitargs)

    toprepo = tmp_path / 'repo'
    with git.Repo.init(str(toprepo), expand_vars=False) as repo:
        with (toprepo / 'hopic-ci-config.yaml').open('w') as f:
            f.write('''\
phases:
  build:
    test:
      - cat subrepo/dummy.txt
''')
        repo.index.add(('hopic-ci-config.yaml',))
        repo.git.submodule(('add', '../subrepo', 'subrepo'))
        repo.index.commit(message='Initial commit', **commitargs)

    # Successful checkout and build
    result = run(
            ('checkout-source-tree', '--clean', '--target-remote', str(toprepo), '--target-ref', 'master'),
            ('build',),
        )
    assert result.exit_code == 0
    out, err = capfd.readouterr()
    build_out = ''.join(out.splitlines(keepends=True)[1:])
    assert build_out == dummy_content

    # Make submodule checkout fail
    subrepo.rename(subrepo.parent / 'old-subrepo')

    # Expected failure
    with pytest.raises(git.GitCommandError, match=r'(?is)submodule.*repository.*\bdoes not exist\b'):
        result = run(('checkout-source-tree', '--clean', '--target-remote', str(toprepo), '--target-ref', 'master'))

    # Ignore submodule failure only
    result = run(('checkout-source-tree', '--clean', '--ignore-initial-submodule-checkout-failure', '--target-remote', str(toprepo), '--target-ref', 'master'))
    assert result.exit_code == 0


def test_clean_checkout_in_non_empty_dir(capfd, tmp_path):
    toprepo = tmp_path / 'repo'
    with git.Repo.init(str(toprepo), expand_vars=False) as repo:
        repo.index.commit(message='Initial commit', **_commitargs)

    non_empty_dir = tmp_path / 'non-empty-clone'
    non_empty_dir.mkdir(parents=True)
    garbage_file = non_empty_dir / 'not-empty'
    with open(garbage_file, 'w') as f:
        f.write('Garbage!')

    # Verify first that a checkout without --clean fails
    with pytest.raises(git.GitCommandError, match=r'(?is).*\bfatal:\s+destination\s+path\b.*\bexists\b.*\bnot\b.*\bempty\s+directory\b'):
        run(('--workspace', str(non_empty_dir), 'checkout-source-tree', '--target-remote', str(toprepo), '--target-ref', 'master'))
    assert garbage_file.exists()

    # Now notice the difference and expect it to succeed with --clean
    clean_result = run(('--workspace', str(non_empty_dir), 'checkout-source-tree', '--clean', '--target-remote', str(toprepo), '--target-ref', 'master'))
    assert clean_result.exit_code == 0
    assert not garbage_file.exists()


def test_checkout_in_newly_initialized_repo(capfd, tmp_path):
    toprepo = tmp_path / 'repo'
    with git.Repo.init(str(toprepo), expand_vars=False) as repo:
        repo.index.commit(message='Initial commit', **_commitargs)

    new_init_repo = tmp_path / 'non-empty-clone'
    with git.Repo.init(str(new_init_repo), expand_vars=False):
        pass

    result = run(('--workspace', str(new_init_repo), 'checkout-source-tree', '--target-remote', str(toprepo), '--target-ref', 'master'))
    assert result.exit_code == 0


def test_default_clean_checkout_option(capfd, tmp_path):
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
{}
''')
        temp_test_file = 'random_file.txt'
        with (toprepo / temp_test_file).open('w') as f:
            f.write('''\
nothing to see here
''')

        repo.index.add(('hopic-ci-config.yaml',))
        repo.index.commit(message='Initial commit', **commitargs)
        commit = list(repo.iter_commits('master', max_count=1))[0]
        result = run(('--workspace', str(toprepo), 'checkout-source-tree', '--clean', '--target-remote', str(toprepo), '--target-ref', 'master'))
        assert result.exit_code == 0
        assert not (toprepo / temp_test_file).is_file()
        assert commit.committed_date == (toprepo / 'hopic-ci-config.yaml').stat().st_mtime


def test_clean_option_custom_command_is_run_before_default_command(capfd, tmp_path):
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
clean:
    - touch dummy.txt
''')
        temp_test_file = 'random_file.txt'
        with (toprepo / temp_test_file).open('w') as f:
            f.write('''\
nothing to see here
''')

        repo.index.add(('hopic-ci-config.yaml',))
        repo.index.commit(message='Initial commit', **commitargs)
        commit = list(repo.iter_commits('master', max_count=1))[0]
        result = run(('--workspace', str(toprepo), 'checkout-source-tree', '--clean', '--target-remote', str(toprepo), '--target-ref', 'master'))
        assert result.exit_code == 0
        assert not (toprepo / temp_test_file).is_file()
        assert not (toprepo / 'dummy.txt').is_file()
        assert commit.committed_date == (toprepo / 'hopic-ci-config.yaml').stat().st_mtime


def test_clean_option_custom_command_is_executed(capfd, tmp_path):
    author = git.Actor('Bob Tester', 'bob@example.net')
    commitargs = dict(
        author_date=_git_time,
        commit_date=_git_time,
        author=author,
        committer=author,
    )

    toprepo = tmp_path / 'repo'
    std_out_message = 'test dummy'
    with git.Repo.init(str(toprepo), expand_vars=False) as repo:
        with (toprepo / 'hopic-ci-config.yaml').open('w') as f:
            f.write('''\
clean:
    - echo '%s'
''' % std_out_message)
        temp_test_file = 'random_file.txt'
        with (toprepo / temp_test_file).open('w') as f:
            f.write('''\
nothing to see here
''')

        repo.index.add(('hopic-ci-config.yaml',))
        repo.index.commit(message='Initial commit', **commitargs)
        commit = list(repo.iter_commits('master', max_count=1))[0]
        result = run(('--workspace', str(toprepo), 'checkout-source-tree', '--clean', '--target-remote', str(toprepo), '--target-ref', 'master'))
        assert result.exit_code == 0
        out, err = capfd.readouterr()
        sys.stdout.write(out)
        sys.stderr.write(err)
        clean_out = out.splitlines()[0]
        assert clean_out == std_out_message
        assert not (toprepo / temp_test_file).is_file()
        assert commit.committed_date == (toprepo / 'hopic-ci-config.yaml').stat().st_mtime


def test_clean_option_home_annotations(capfd, tmp_path):
    author = git.Actor('Bob Tester', 'bob@example.net')
    commitargs = dict(
        author_date=_git_time,
        commit_date=_git_time,
        author=author,
        committer=author,
    )
    home_path = os.path.expanduser("~")
    toprepo = tmp_path / 'repo'
    with git.Repo.init(str(toprepo), expand_vars=False) as repo:
        with (toprepo / 'hopic-ci-config.yaml').open('w') as f:
            f.write('''\
clean:
    - echo '$HOME'
    - echo '~'
''')

        repo.index.add(('hopic-ci-config.yaml',))
        repo.index.commit(message='Initial commit', **commitargs)
        result = run(('--workspace', str(toprepo), 'checkout-source-tree', '--clean', '--target-remote', str(toprepo), '--target-ref', 'master'))
        assert result.exit_code == 0
        out, err = capfd.readouterr()
        sys.stdout.write(out)
        sys.stderr.write(err)
        clean_home_out = out.splitlines()[0]
        clean_tilde_out = out.splitlines()[1]
        assert clean_home_out == home_path
        assert clean_tilde_out == home_path


def test_handle_syntax_error_in_optional_hopic_file(capfd, tmp_path):
    toprepo = tmp_path / 'repo'
    with git.Repo.init(str(toprepo), expand_vars=False) as repo:
        with (toprepo / 'hopic-ci-config.yaml').open('w') as f:
            f.write(dedent('''
                phases:
                  build:
                    test:
                      - image:
                        some-image-with-an-entrypoint:0.0.42
                      - "-o subrepo/dummy.txt""
                '''))
        repo.index.add(('hopic-ci-config.yaml',))
        repo.index.commit(message='Commit incorrect hopic file', **_commitargs)

    # checkout-source-tree should be successful
    result = run(
            ('checkout-source-tree', '--target-remote', str(toprepo), '--target-ref', 'master'),
        )
    assert result.exit_code == 0
