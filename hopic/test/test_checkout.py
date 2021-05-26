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

import os
import sys
from textwrap import dedent

import git
import pytest


_git_time = f"{7 * 24 * 3600} +0000"
_author = git.Actor('Bob Tester', 'bob@example.net')
_commitargs = dict(
    author_date=_git_time,
    commit_date=_git_time,
    author=_author,
    committer=_author,
)


def test_clean_submodule_checkout(capfd, run_hopic, tmp_path):
    author = git.Actor('Bob Tester', 'bob@example.net')
    commitargs = dict(
            author_date=_git_time,
            commit_date=_git_time,
            author=author,
            committer=author,
        )

    dummy_content = 'Lalalala!\n'
    subrepo = tmp_path / 'subrepo'
    with git.Repo.init(subrepo, expand_vars=False) as repo:
        (subrepo / 'dummy.txt').write_text(dummy_content)
        repo.index.add(('dummy.txt',))
        repo.index.commit(message='Initial dummy commit', **commitargs)

    with git.Repo.init(run_hopic.toprepo, expand_vars=False) as repo:
        (run_hopic.toprepo / 'hopic-ci-config.yaml').write_text('''\
phases:
  build:
    test:
      - cat subrepo/dummy.txt
''')
        repo.index.add(('hopic-ci-config.yaml',))
        repo.git.submodule(('add', '../subrepo', 'subrepo'))
        repo.index.commit(message='Initial commit', **commitargs)

    # Successful checkout and build
    (_, result) = run_hopic(
            ('checkout-source-tree', '--clean', '--target-remote', run_hopic.toprepo, '--target-ref', 'master'),
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
        (_,) = run_hopic(('checkout-source-tree', '--clean', '--target-remote', run_hopic.toprepo, '--target-ref', 'master'))

    # Ignore submodule failure only
    (result,) = run_hopic(
        ('checkout-source-tree', '--clean', '--ignore-initial-submodule-checkout-failure', '--target-remote', run_hopic.toprepo, '--target-ref', 'master'),
    )
    assert result.exit_code == 0


def test_clean_checkout_in_non_empty_dir(run_hopic, tmp_path):
    with git.Repo.init(run_hopic.toprepo, expand_vars=False) as repo:
        repo.index.commit(message='Initial commit', **_commitargs)

    non_empty_dir = tmp_path / 'non-empty-clone'
    non_empty_dir.mkdir(parents=True)
    garbage_file = non_empty_dir / 'not-empty'
    garbage_file.write_text('Garbage!')

    # Verify first that a checkout without --clean fails
    with pytest.raises(git.GitCommandError, match=r'(?is).*\bfatal:\s+destination\s+path\b.*\bexists\b.*\bnot\b.*\bempty\s+directory\b'):
        (_,) = run_hopic(('--workspace', non_empty_dir, 'checkout-source-tree', '--target-remote', run_hopic.toprepo, '--target-ref', 'master'))
    assert garbage_file.exists()

    # Now notice the difference and expect it to succeed with --clean
    (clean_result,) = run_hopic(
        ('--workspace', non_empty_dir, 'checkout-source-tree', '--clean', '--target-remote', run_hopic.toprepo, '--target-ref', 'master'),
    )
    assert clean_result.exit_code == 0
    assert not garbage_file.exists()


def test_checkout_in_newly_initialized_repo(run_hopic, tmp_path):
    with git.Repo.init(run_hopic.toprepo, expand_vars=False) as repo:
        repo.index.commit(message='Initial commit', **_commitargs)

    new_init_repo = tmp_path / 'non-empty-clone'
    with git.Repo.init(new_init_repo, expand_vars=False):
        pass

    (result,) = run_hopic(('--workspace', new_init_repo, 'checkout-source-tree', '--target-remote', run_hopic.toprepo, '--target-ref', 'master'))
    assert result.exit_code == 0


def test_default_clean_checkout_option(run_hopic):
    author = git.Actor('Bob Tester', 'bob@example.net')
    commitargs = dict(
        author_date=_git_time,
        commit_date=_git_time,
        author=author,
        committer=author,
    )

    with git.Repo.init(run_hopic.toprepo, expand_vars=False) as repo:
        (run_hopic.toprepo / 'hopic-ci-config.yaml').write_text('''\
{}
''')
        temp_test_file = run_hopic.toprepo / "random_file.txt"
        temp_test_file.write_text('''\
nothing to see here
''')

        repo.index.add(('hopic-ci-config.yaml',))
        repo.index.commit(message='Initial commit', **commitargs)
        commit = list(repo.iter_commits('master', max_count=1))[0]
        (result,) = run_hopic(
            ('--workspace', run_hopic.toprepo, 'checkout-source-tree', '--clean', '--target-remote', run_hopic.toprepo, '--target-ref', 'master'),
        )
        assert result.exit_code == 0
        assert not temp_test_file.is_file()
        assert commit.committed_date == (run_hopic.toprepo / 'hopic-ci-config.yaml').stat().st_mtime


def test_clean_option_custom_command_is_run_before_default_command(run_hopic):
    author = git.Actor('Bob Tester', 'bob@example.net')
    commitargs = dict(
        author_date=_git_time,
        commit_date=_git_time,
        author=author,
        committer=author,
    )

    with git.Repo.init(run_hopic.toprepo, expand_vars=False) as repo:
        (run_hopic.toprepo / 'hopic-ci-config.yaml').write_text('''\
clean:
    - touch dummy.txt
''')
        temp_test_file = 'random_file.txt'
        (run_hopic.toprepo / temp_test_file).write_text('''\
nothing to see here
''')

        repo.index.add(('hopic-ci-config.yaml',))
        repo.index.commit(message='Initial commit', **commitargs)
        commit = list(repo.iter_commits('master', max_count=1))[0]
        (result,) = run_hopic(
            ('--workspace', run_hopic.toprepo, 'checkout-source-tree', '--clean', '--target-remote', run_hopic.toprepo, '--target-ref', 'master'),
        )
        assert result.exit_code == 0
        assert not (run_hopic.toprepo / temp_test_file).is_file()
        assert not (run_hopic.toprepo / 'dummy.txt').is_file()
        assert commit.committed_date == (run_hopic.toprepo / 'hopic-ci-config.yaml').stat().st_mtime


def test_clean_option_custom_command_is_executed(capfd, run_hopic):
    author = git.Actor('Bob Tester', 'bob@example.net')
    commitargs = dict(
        author_date=_git_time,
        commit_date=_git_time,
        author=author,
        committer=author,
    )

    std_out_message = 'test dummy'
    with git.Repo.init(run_hopic.toprepo, expand_vars=False) as repo:
        (run_hopic.toprepo / 'hopic-ci-config.yaml').write_text(
            dedent(
                f"""\
                clean:
                    - echo '{std_out_message}'
                """
            )
        )
        temp_test_file = 'random_file.txt'
        (run_hopic.toprepo / temp_test_file).write_text('''\
nothing to see here
''')

        repo.index.add(('hopic-ci-config.yaml',))
        repo.index.commit(message='Initial commit', **commitargs)
        commit = list(repo.iter_commits('master', max_count=1))[0]
        (result,) = run_hopic(
            ('--workspace', run_hopic.toprepo, 'checkout-source-tree', '--clean', '--target-remote', run_hopic.toprepo, '--target-ref', 'master'),
        )
        assert result.exit_code == 0
        out, err = capfd.readouterr()
        sys.stdout.write(out)
        sys.stderr.write(err)
        clean_out = out.splitlines()[0]
        assert clean_out == std_out_message
        assert not (run_hopic.toprepo / temp_test_file).is_file()
        assert commit.committed_date == (run_hopic.toprepo / 'hopic-ci-config.yaml').stat().st_mtime


def test_clean_option_home_annotations(capfd, run_hopic):
    author = git.Actor('Bob Tester', 'bob@example.net')
    commitargs = dict(
        author_date=_git_time,
        commit_date=_git_time,
        author=author,
        committer=author,
    )
    home_path = os.path.expanduser("~")
    with git.Repo.init(run_hopic.toprepo, expand_vars=False) as repo:
        (run_hopic.toprepo / 'hopic-ci-config.yaml').write_text('''\
clean:
    - echo '$HOME'
    - echo '~'
''')

        repo.index.add(('hopic-ci-config.yaml',))
        repo.index.commit(message='Initial commit', **commitargs)
        (result,) = run_hopic(
            ('--workspace', run_hopic.toprepo, 'checkout-source-tree', '--clean', '--target-remote', run_hopic.toprepo, '--target-ref', 'master'),
        )
        assert result.exit_code == 0
        out, err = capfd.readouterr()
        sys.stdout.write(out)
        sys.stderr.write(err)
        clean_home_out = out.splitlines()[0]
        clean_tilde_out = out.splitlines()[1]
        assert clean_home_out == home_path
        assert clean_tilde_out == home_path


def test_handle_syntax_error_in_optional_hopic_file(run_hopic):
    with git.Repo.init(run_hopic.toprepo, expand_vars=False) as repo:
        (run_hopic.toprepo / 'hopic-ci-config.yaml').write_text(
            dedent(
                '''
                phases:
                  build:
                    test:
                      - image:
                        some-image-with-an-entrypoint:0.0.42
                      - "-o subrepo/dummy.txt""
                '''
            )
        )
        repo.index.add(('hopic-ci-config.yaml',))
        repo.index.commit(message='Commit incorrect hopic file', **_commitargs)

    # checkout-source-tree should be successful
    (result,) = run_hopic(
            ('checkout-source-tree', '--target-remote', run_hopic.toprepo, '--target-ref', 'master'),
        )
    assert result.exit_code == 0


def test_checkout_non_head_commit(run_hopic):
    dummy = run_hopic.toprepo / "dummy.txt"
    first_content = "Lalalala!\n"
    with git.Repo.init(run_hopic.toprepo, expand_vars=False) as repo:
        dummy.write_text(first_content)
        repo.index.add((str(dummy.relative_to(run_hopic.toprepo)),))
        first_commit = repo.index.commit(message="Initial dummy commit", **_commitargs)

        dummy.write_text("Mooh!\n")
        repo.index.add((str(dummy.relative_to(run_hopic.toprepo)),))
        repo.index.commit(message="Subsequent dummy commit", **_commitargs)

    (result,) = run_hopic(
        ("checkout-source-tree", "--target-remote", run_hopic.toprepo, "--target-ref", "master", "--target-commit", first_commit),
    )
    assert result.exit_code == 0


def test_reject_checkout_out_of_branch_commit(run_hopic):
    dummy = run_hopic.toprepo / "dummy.txt"
    first_content = "Lalalala!\n"
    with git.Repo.init(run_hopic.toprepo, expand_vars=False) as repo:
        dummy.write_text(first_content)
        repo.index.add((str(dummy.relative_to(run_hopic.toprepo)),))
        first_commit = repo.index.commit(message="Initial dummy commit", **_commitargs)

        dummy.write_text("Mooh!\n")
        repo.index.add((str(dummy.relative_to(run_hopic.toprepo)),))
        final_commit = repo.index.commit(message="Subsequent dummy commit", **_commitargs)
        repo.heads.master.commit = first_commit
        repo.head.reference = repo.heads.master
        repo.head.reset(index=True, working_tree=True)

    (result,) = run_hopic(
        ("checkout-source-tree", "--target-remote", run_hopic.toprepo, "--target-ref", "master", "--target-commit", final_commit),
    )
    assert result.exit_code == 37
