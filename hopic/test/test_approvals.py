# -*- coding: utf-8 -*-

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

import os.path

from commisery.commit import ConventionalCommit
import git
import pytest

_GIT_TIME = f"{42 * 365 * 24 * 3600} +0000"
_BASE_APPROVER = 'Joe Approver1 <joe.approver1@nerds-r-us.eu>'
_PRESQUASH_APPROVER = 'Bob Approvér2 <bob.approver2@acme.net>'
_POSTSQUASH_APPROVER = '"Hank: The Approver 3" <hank.approver3@business.ie>'


@pytest.fixture
def repo_with_fixup(run_hopic):
    author = git.Actor('Joe Engineer', 'joe@engineeringinc.net')
    commitargs = dict(
        author_date=_GIT_TIME,
        commit_date=_GIT_TIME,
        author=author,
        committer=author,)

    with git.Repo.init(run_hopic.toprepo, expand_vars=False) as repo:
        (run_hopic.toprepo / "hopic-ci-config.yaml").write_text('''\
version:
  bump: no

phases:
  build:
    test:
      - echo "Build successful!"
''')
        repo.index.add(('hopic-ci-config.yaml',))
        (run_hopic.toprepo / "README.md").write_text("Test\n")
        repo.index.add(('README.md',))
        base_commit = repo.index.commit(message='feat: initial commit', **commitargs)

        # PR branch from just before the main branch's HEAD
        repo.head.reference = repo.create_head('merge-branch', base_commit)

        (run_hopic.toprepo / "README.md").write_text("Imagine smth useful here\n")
        repo.index.add(('README.md',))
        repo.index.commit(message='docs: add useful documentation', **commitargs)

        (run_hopic.toprepo / "README.md").write_text("Imagine something useful here\n")
        repo.index.add(('README.md',))
        repo.index.commit(message='fixup! docs: add useful documentation', **commitargs).hexsha
        yield repo


def _perform_merge(run_hopic, repo, approvals):
    (*_, result) = run_hopic(
            ('-v', 'DEBUG', 'checkout-source-tree', '--target-remote', repo.working_dir, '--target-ref', 'master'),
            ('prepare-source-tree',
                '--author-name', 'Damièn Evelopér',
                '--author-email', 'd.eveloper@mycompany.com',
                '--author-date', f"@{_GIT_TIME}",
                '--commit-date', f"@{_GIT_TIME}",
                'merge-change-request',
                '--source-remote', repo.working_dir,
                '--source-ref', 'merge-branch',
                '--change-request=42',
                '--title=feat: something interesting',

                *('--approved-by={}:{}'.format(*approval) for approval in approvals),
             ),
            ('submit',),
        )
    assert result.exit_code == 0

    return ConventionalCommit(repo.heads.master.commit)


def test_approval_still_valid_on_autosquash(repo_with_fixup, run_hopic):
    presquash_commit_sha = repo_with_fixup.head.commit.hexsha
    repo_with_fixup.git.rebase('HEAD~~', interactive=True, autosquash=True, kill_after_timeout=5, env={
            'GIT_SEQUENCE_EDITOR': ':',
            'GIT_COMMITTER_NAME': 'My Name is Nobody',
            'GIT_COMMITTER_EMAIL': 'nobody@example.com',
        })
    squashed_commit_sha = repo_with_fixup.head.commit.hexsha
    base_sha = repo_with_fixup.commit('HEAD~').hexsha

    commit = _perform_merge(run_hopic, repo_with_fixup, (
            (_BASE_APPROVER, base_sha),
            (_PRESQUASH_APPROVER, presquash_commit_sha),
            (_POSTSQUASH_APPROVER, squashed_commit_sha),
        ))

    assert set(commit.footers['Acked-By']) == {_PRESQUASH_APPROVER, _POSTSQUASH_APPROVER}


def test_approval_invalid_on_commit_msg_change(repo_with_fixup, run_hopic):
    presquash_commit_sha = repo_with_fixup.head.commit.hexsha

    repo_with_fixup.git.rebase('HEAD~~', interactive=True, autosquash=True, kill_after_timeout=5, env={
            'GIT_SEQUENCE_EDITOR': ':',
            'GIT_COMMITTER_NAME': 'My Name is Nobody',
            'GIT_COMMITTER_EMAIL': 'nobody@example.com',
        })
    repo_with_fixup.git.commit('--amend', '-m', 'fix: sneakily changed the commit message into something stoopid', env={
            'GIT_COMMITTER_NAME': 'My Name is Nobody',
            'GIT_COMMITTER_EMAIL': 'nobody@example.com',
        })
    squashed_commit_sha = repo_with_fixup.head.commit.hexsha
    base_sha = repo_with_fixup.commit('HEAD~').hexsha

    commit = _perform_merge(run_hopic, repo_with_fixup, (
            (_BASE_APPROVER, base_sha),
            (_PRESQUASH_APPROVER, presquash_commit_sha),
            (_POSTSQUASH_APPROVER, squashed_commit_sha),
        ))
    assert set(commit.footers['Acked-By']) == {_POSTSQUASH_APPROVER}


def test_approval_invalid_on_author_change(repo_with_fixup, run_hopic):
    presquash_commit_sha = repo_with_fixup.head.commit.hexsha

    repo_with_fixup.git.rebase('HEAD~~', interactive=True, autosquash=True, kill_after_timeout=5, env={
            'GIT_SEQUENCE_EDITOR': ':',
            'GIT_COMMITTER_NAME': 'My Name is Nobody',
            'GIT_COMMITTER_EMAIL': 'nobody@example.com',
        })
    repo_with_fixup.git.commit('--amend', '--author', 'Mysterious Stranger <not.who.you.thought@company.biz>', '--no-edit', env={
            'GIT_COMMITTER_NAME': 'My Name is Nobody',
            'GIT_COMMITTER_EMAIL': 'nobody@example.com',
        })

    squashed_commit_sha = repo_with_fixup.head.commit.hexsha
    base_sha = repo_with_fixup.commit('HEAD~').hexsha

    commit = _perform_merge(run_hopic, repo_with_fixup, (
            (_BASE_APPROVER, base_sha),
            (_PRESQUASH_APPROVER, presquash_commit_sha),
            (_POSTSQUASH_APPROVER, squashed_commit_sha),
        ))

    assert set(commit.footers['Acked-By']) == {_POSTSQUASH_APPROVER}


def test_approval_invalid_on_content_change(repo_with_fixup, run_hopic):
    presquash_commit_sha = repo_with_fixup.head.commit.hexsha

    repo_with_fixup.git.rebase('HEAD~~', interactive=True, autosquash=True, kill_after_timeout=5, env={
            'GIT_SEQUENCE_EDITOR': ':',
            'GIT_COMMITTER_NAME': 'My Name is Nobody',
            'GIT_COMMITTER_EMAIL': 'nobody@example.com',
        })

    # Amend the just-squashed-commit with a random content change
    with open(os.path.join(repo_with_fixup.working_dir, 'some_file.txt'), 'w') as somefile:
        somefile.write('Some random addition')
    repo_with_fixup.git.add('some_file.txt')
    repo_with_fixup.git.commit('--amend', '--no-edit', env={
            'GIT_COMMITTER_NAME': 'My Name is Nobody',
            'GIT_COMMITTER_EMAIL': 'nobody@example.com',
        })
    base_sha = repo_with_fixup.commit('HEAD~').hexsha

    squashed_commit_sha = repo_with_fixup.head.commit.hexsha
    commit = _perform_merge(run_hopic, repo_with_fixup, (
            (_BASE_APPROVER, base_sha),
            (_PRESQUASH_APPROVER, presquash_commit_sha),
            (_POSTSQUASH_APPROVER, squashed_commit_sha),
        ))

    assert set(commit.footers['Acked-By']) == {_POSTSQUASH_APPROVER}


def test_approval_handle_invalid_shas_gracefully(repo_with_fixup, run_hopic):
    presquash_commit_sha = repo_with_fixup.head.commit.hexsha

    repo_with_fixup.git.rebase('HEAD~~', interactive=True, autosquash=True, kill_after_timeout=5, env={
            'GIT_SEQUENCE_EDITOR': ':',
            'GIT_COMMITTER_NAME': 'My Name is Nobody',
            'GIT_COMMITTER_EMAIL': 'nobody@example.com',
        })

    invalid_sha = 'dead00000000000000000000000000000000dead'

    commit = _perform_merge(run_hopic, repo_with_fixup, (
            (_BASE_APPROVER, invalid_sha),
            (_PRESQUASH_APPROVER, presquash_commit_sha),
            (_POSTSQUASH_APPROVER, invalid_sha),
        ))
    assert set(commit.footers['Acked-By']) == {_PRESQUASH_APPROVER}
