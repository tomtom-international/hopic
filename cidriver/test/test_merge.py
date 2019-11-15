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
import os
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


def hopic_config_subdir_version_file_tester(capfd, config_dir, hopic_config, version_file, version_input, expected_version, tmp_path):
    author = git.Actor('Bob Tester', 'bob@example.net')
    commitargs = dict(
        author_date=_git_time,
        commit_date=_git_time,
        author=author,
        committer=author,
    )

    toprepo = tmp_path / 'repo'
    with git.Repo.init(str(toprepo), expand_vars=False) as repo:
        if not os.path.exists(toprepo / config_dir):
            os.mkdir(toprepo / config_dir)
        with (toprepo / config_dir / 'hopic-ci-config.yaml').open('w') as f:
            f.write(hopic_config)

        with (toprepo / config_dir / version_file).open('w') as f:
            f.write(version_input)
        repo.index.add((os.path.join(config_dir, 'hopic-ci-config.yaml'),))
        repo.index.add((os.path.join(config_dir, version_file),))
        base_commit = repo.index.commit(message='Initial commit', **commitargs)

        # PR branch from just before the main branch's HEAD
        repo.head.reference = repo.create_head('something-useful', base_commit)
        assert not repo.head.is_detached
        repo.head.reset(index=True, working_tree=True)

        # Some change
        with (toprepo / 'something.txt').open('w') as f:
            f.write('usable')
        repo.index.add(('something.txt',))
        repo.index.commit(message='feat: add something useful', **commitargs)

    # Successful checkout and build
    result = run(
        ('--workspace', './', '--config', os.path.join(config_dir, 'hopic-ci-config.yaml'),
         'checkout-source-tree', '--target-remote', str(toprepo), '--target-ref', 'master'),
        ('--workspace', './', '--config', os.path.join(config_dir, 'hopic-ci-config.yaml'), 'prepare-source-tree',
         'merge-change-request', '--source-remote', str(toprepo), '--source-ref', 'something-useful'),
        ('--workspace', './', '--config', os.path.join(config_dir, 'hopic-ci-config.yaml'), 'submit'),
    )
    assert result.exit_code == 0
    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)
    version_out = out.splitlines()[2]
    assert version_out == expected_version
    with git.Repo(str(toprepo), expand_vars=False) as repo:
        repo.git.checkout('master')
        assert expected_version == repo.git.tag(l=True)

    return toprepo


def test_hopic_config_subdir_version_file(capfd, tmp_path):
    version = "0.0.1-SNAPSHOT"
    commit_version = "0.0.1"
    version_file = "revision_test.txt"
    config_dir = "test_config"
    hopic_config_subdir_version_file_tester(capfd,
                                            config_dir,
                                            '''\
version:
  file: {}
  tag:  true
  bump: patch
  format: semver'''.format(version_file),
                                            version_file,
                                            '''\
version={}'''.format(version),
                                            commit_version,
                                            tmp_path)


def test_hopic_config_subdir_version_file_after_submit(capfd, tmp_path):
    version = "0.0.42-SNAPSHOT"
    commit_version = "0.0.42"
    version_file = "revision_test.txt"
    config_dir = ".ci"
    test_repo = hopic_config_subdir_version_file_tester(capfd,
                                                        config_dir,
                                                        '''\
version:
  file: {}
  tag:  true
  bump: patch
  format: semver
  after-submit:
    bump: prerelease
    prerelease-seed: PRERELEASE-TEST'''.format(version_file),
                                                        version_file,
                                                        '''\
version={}'''.format(version),
                                                        commit_version,
                                                        tmp_path)
    with (test_repo / config_dir / version_file).open('r') as f:
        assert f.read() == "version=0.0.43-PRERELEASE-TEST"


def test_version_bump_after_submit_from_repo_root_dir(capfd, tmp_path):
    version = "0.0.3-SNAPSHOT"
    commit_version = "0.0.3"
    version_file = "revision_test.txt"
    config_dir = ""
    test_repo = hopic_config_subdir_version_file_tester(capfd,
                                                        config_dir,
                                                        '''\
version:
  file: {}
  tag:  true
  bump: patch
  format: semver
  after-submit:
    bump: prerelease
    prerelease-seed: PRERELEASE-TEST  
                                                    '''.format(version_file),
                                                        version_file,
                                                        '''\
version={}'''.format(version),
                                                        commit_version,
                                                        tmp_path)
    with (test_repo / config_dir / version_file).open('r') as f:
        assert f.read() == "version=0.0.4-PRERELEASE-TEST"


def merge_conventional_bump(capfd, tmp_path, message, strict=False, on_every_change=True):
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
  format: semver
  tag:    true
  bump:
    policy: conventional-commits
''')
            if strict:
                f.write('    strict: yes\n')
            if not on_every_change:
                f.write('    on-every-change: no\n')
        repo.index.add(('hopic-ci-config.yaml',))
        repo.index.commit(message='Initial commit', **commitargs)
        repo.create_tag('0.0.0')

        # PR branch
        repo.head.reference = repo.create_head('something-useful')
        assert not repo.head.is_detached

        # Some change
        with (toprepo / 'something.txt').open('w') as f:
            f.write('usable')
        repo.index.add(('something.txt',))
        repo.index.commit(message=message, **commitargs)

    # Successful checkout and build
    return run(
            ('checkout-source-tree', '--target-remote', str(toprepo), '--target-ref', 'master'),
            ('prepare-source-tree', '--author-date', '@{_git_time}'.format(_git_time=_git_time), '--commit-date', '@{_git_time}'.format(_git_time=_git_time),
                'merge-change-request', '--source-remote', str(toprepo), '--source-ref', 'something-useful'),
        )


def test_merge_conventional_fix_bump(capfd, tmp_path):
    result = merge_conventional_bump(capfd, tmp_path, message='fix: some problem')
    assert result.exit_code == 0

    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)

    checkout_commit, merge_commit, merge_version = out.splitlines()
    assert merge_version.startswith('0.0.1+g')


def test_merge_conventional_feat_bump(capfd, tmp_path):
    result = merge_conventional_bump(capfd, tmp_path, message='feat: add something useful')
    assert result.exit_code == 0

    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)

    checkout_commit, merge_commit, merge_version = out.splitlines()
    assert merge_version.startswith('0.1.0+g')


def test_merge_conventional_breaking_change_bump(capfd, tmp_path):
    result = merge_conventional_bump(capfd, tmp_path, message='refactor!: make the API type better')
    assert result.exit_code == 0

    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)

    checkout_commit, merge_commit, merge_version = out.splitlines()
    assert merge_version.startswith('1.0.0+g')


def test_merge_conventional_feat_with_breaking_bump(capfd, tmp_path):
    result = merge_conventional_bump(capfd, tmp_path, message='''\
refactor!: add something awesome

This adds the new awesome feature.

BREAKING CHANGE: unfortunately this was incompatible with the old feature for
the same purpose, so you'll have to migrate.
''')
    assert result.exit_code == 0

    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)

    checkout_commit, merge_commit, merge_version = out.splitlines()
    assert merge_version.startswith('1.0.0+g')


def test_merge_conventional_broken_feat(capfd, tmp_path):
    with pytest.raises(RuntimeError):
        merge_conventional_bump(capfd, tmp_path, message='feat add something useful', strict=True)


def test_merge_conventional_feat_bump_not_on_change(capfd, tmp_path):
    result = merge_conventional_bump(capfd, tmp_path, message='feat: add something useful', on_every_change=False)
    assert result.exit_code == 0

    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)

    checkout_commit, merge_commit, merge_version = out.splitlines()
    assert merge_version.startswith('0.0.1-2+g')
