# Copyright (c) 2018 - 2021 TomTom N.V.
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

import click

from . import (
        autocomplete,
        build,
        extensions,
        utils,
    )
from .utils import (
        determine_config_file_name,
        get_package_version,
        is_publish_branch,
    )
from commisery.commit import parse_commit_message
from ..build import (
    HopicGitInfo,
)
from ..config_reader import (
        JSONEncoder,
        expand_vars,
        read as read_config,
    )
from ..execution import echo_cmd_click as echo_cmd
from ..git_time import (
    GitVersion,
    determine_git_version,
    determine_version,
    restore_mtime_from_git,
    to_git_time,
)
from .global_obj import initialize_global_variables_from_config
from ..versioning import (
    hotfix_id,
    replace_version,
)
from collections import OrderedDict
from collections.abc import (
        Mapping,
        MutableMapping,
        MutableSequence,
        Set,
    )
from configparser import (
        NoOptionError,
        NoSectionError,
    )
from copy import copy
from datetime import datetime
from dateutil.parser import parse as date_parse
from dateutil.tz import (tzoffset, tzlocal)
import git
import gitdb
from io import (
        BytesIO,
        StringIO,
    )
import json
import logging
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
from typing import (
    List,
    Optional,
    Tuple,
    Union,
)
from textwrap import dedent
from yaml.error import YAMLError

from .main import main
from ..errors import (
    CommitAncestorMismatchError,
    GitNotesMismatchError,
    VersionBumpMismatchError,
    VersioningError,
)


PACKAGE : str = __package__.split('.')[0]

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


for submodule in (
    build,
    extensions,
):
    for subcmd in dir(submodule):
        if subcmd.startswith('_'):
            continue
        cmd = getattr(submodule, subcmd)
        if not isinstance(cmd, click.Command):
            continue
        main.add_command(cmd)


class DateTime(click.ParamType):
    name = 'date'
    stamp_re = re.compile(r'^@(?P<utcstamp>\d+(?:\.\d+)?)(?:\s+(?P<tzdir>[-+])(?P<tzhour>\d{1,2}):?(?P<tzmin>\d{2}))?$')

    def convert(self, value, param, ctx):
        if value is None or isinstance(value, datetime):
            return value

        try:
            stamp = self.stamp_re.match(value)
            if stamp:
                def int_or_none(i):
                    if i is None:
                        return None
                    return int(i)

                tzdir  = (-1 if stamp.group('tzdir') == '-' else 1)
                tzhour = int_or_none(stamp.group('tzhour'))
                tzmin  = int_or_none(stamp.group('tzmin'))

                if tzhour is not None:
                    tz = tzoffset(None, tzdir * (tzhour * 3600 + tzmin * 60))
                else:
                    tz = tzlocal()
                return datetime.fromtimestamp(float(stamp.group('utcstamp')), tz)

            dt = date_parse(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tzlocal())
            return dt
        except ValueError as e:
            self.fail('Could not parse datetime string "{value}": {e}'.format(value=value, e=' '.join(e.args)), param, ctx)


@main.command()
@click.pass_context
def may_publish(ctx):
    """
    Check if the target branch name is allowed to be published, according to publish-from-branch in the config file.
    """

    ctx.exit(0 if is_publish_branch(ctx) else 1)


def checkout_tree(
    tree,
    remote,
    ref,
    *,
    commit: Optional[str] = None,
    clean: bool = False,
    remote_name: str = 'origin',
    tags: bool = True,
    allow_submodule_checkout_failure: bool = False,
    clean_config: Union[List, Tuple] = (),
):
    try:
        repo = git.Repo(tree)
        # Cleanup potential existing submodules to avoid conflicts in PR's where submodules are added
        # Cannot use config file here to determine if feature is enabled since config is not parsed during checkout-source-tree
        repo.git.submodule(["deinit", "--all", "--force"])
        modules_dir = "%s/modules" % repo.git_dir
        if os.path.isdir(modules_dir):
            # Hacky way to restore git repo to clean state
            shutil.rmtree(modules_dir)
    except (git.InvalidGitRepositoryError, git.NoSuchPathError):
        if clean and os.path.exists(tree):
            # Wipe the directory to allow 'git clone' to succeed.
            # It would fail if it isn't empty.

            # We're deleting only the content of the directory, because deleting 'tree' when it's the current working
            # directory of processes would cause getcwd(3) to fail.
            for name in os.listdir(tree):
                path = os.path.join(tree, name)
                if os.path.isdir(path):
                    shutil.rmtree(tree)
                else:
                    os.remove(path)

        repo = git.Repo.clone_from(remote, tree)

    with repo:
        with repo.config_writer() as cfg:
            cfg.remove_section('hopic.code')
            cfg.set_value('color', 'ui', 'always')
            cfg.set_value('hopic.code', 'cfg-clean', str(clean))

        clean_tags = tags and repo.tags
        if clean_tags:
            repo.delete_tag(*clean_tags)

        try:
            # Delete, instead of update, existing remotes.
            # This is because of https://github.com/gitpython-developers/GitPython/issues/719
            repo.delete_remote(remote_name)
        except git.GitCommandError:
            pass
        origin = repo.create_remote(remote_name, remote)

        fetch_info, *_ = origin.fetch(ref, tags=tags)

        if commit is not None and not repo.is_ancestor(commit, fetch_info.commit):
            raise CommitAncestorMismatchError(commit, fetch_info.commit, ref)

        commit = repo.commit(commit) if commit else fetch_info.commit

        repo.head.reference = commit
        repo.head.reset(index=True, working_tree=True)
        # Remove potentially moved submodules
        repo.git.submodule(["deinit", "--all", "--force"])

        # Ensure we have the exact same view of all Hopic notes as are present upstream
        origin.fetch("+refs/notes/hopic/*:refs/notes/hopic/*", prune=True)

        try:
            update_submodules(repo, clean)
        except git.GitCommandError as e:
            log.error(dedent("""\
                    Failed to checkout submodule for ref '%s'
                    error:
                    %s"""), ref, e)
            if not allow_submodule_checkout_failure:
                raise

        if clean:
            clean_repo(repo, clean_config)

        with repo.config_writer() as cfg:
            section = f"hopic.{commit}"
            cfg.set_value(section, 'ref', ref)
            cfg.set_value(section, 'remote', remote)

    return commit


def update_submodules(repo, clean):
    for submodule in repo.submodules:
        log.info("Updating submodule: %s and clean = %s" % (submodule, clean))
        repo.git.submodule(["sync", "--recursive"])
        # Cannot use submodule.update call here since this call doesn't use git submodules call
        # It tries to emulate the behaviour with a git clone call, but this doesn't work with relative submodule URL's
        # See https://github.com/gitpython-developers/GitPython/issues/944
        repo.git.submodule(["update", "--init", "--recursive"])

        with git.Repo(os.path.join(repo.working_dir, submodule.path)) as sub_repo:
            update_submodules(sub_repo, clean)
            if clean:
                clean_repo(sub_repo)


def clean_repo(repo, clean_config=[]):
    def substitute_home(arg):
        volume_vars = {'HOME': os.path.expanduser('~')}
        return expand_vars(volume_vars, os.path.expanduser(arg))
    for cmd in clean_config:
        cmd = [substitute_home(arg) for arg in shlex.split(cmd)]
        try:
            echo_cmd(subprocess.check_call, cmd, cwd=repo.working_dir)
        except subprocess.CalledProcessError as e:
            log.error("Command fatally terminated with exit code %d", e.returncode)
            sys.exit(e.returncode)

    clean_output = repo.git.clean(x=True, d=True, force=(True, True))
    if clean_output:
        log.info('%s', clean_output)

    # Only restore mtimes when doing a clean build. This prevents problems with timestamp-based build sytems.
    # I.e. make and ninja and probably half the world.
    restore_mtime_from_git(repo)


def install_extensions_and_parse_config():
    initialize_global_variables_from_config(extensions.install_extensions.callback())


@main.command()
@click.option('--target-remote'     , metavar='<url>')
@click.option('--target-ref'        , metavar='<ref>')
@click.option('--target-commit'     , metavar='<commit>')
@click.option('--clean/--no-clean'  , default=False, help='''Clean workspace of non-tracked files''')
@click.option('--ignore-initial-submodule-checkout-failure/--no-ignore-initial-submodule-checkout-failure',
              default=False, help='''Ignore git submodule errors during initial checkout''')
@click.pass_context
def checkout_source_tree(
    ctx,
    target_remote,
    target_ref,
    target_commit,
    clean,
    ignore_initial_submodule_checkout_failure,
):
    """
    Checks out a source tree of the specified remote's ref to the workspace.
    """

    workspace = ctx.obj.workspace
    # Check out specified repository
    click.echo(
        checkout_tree(
            workspace,
            target_remote,
            target_ref,
            commit=target_commit,
            clean=clean,
            allow_submodule_checkout_failure=ignore_initial_submodule_checkout_failure,
        )
    )

    try:
        ctx.obj.config = read_config(determine_config_file_name(ctx), ctx.obj.volume_vars)
        if clean:
            with git.Repo(workspace) as repo:
                clean_repo(repo, ctx.obj.config['clean'])
        git_cfg = ctx.obj.config['scm']['git']
    except (click.BadParameter, KeyError, TypeError, OSError, IOError, YAMLError):
        return

    if 'worktrees' in git_cfg:
        with git.Repo(workspace) as repo:

            worktrees = git_cfg['worktrees'].items()
            fetch_result = repo.remotes.origin.fetch([ref for subdir, ref in worktrees])

            worktrees = dict((subdir, fetchinfo.ref) for (subdir, refname), fetchinfo in zip(worktrees, fetch_result))
            log.debug("Worktree config: %s", worktrees)

            for subdir, ref in worktrees.items():
                try:
                    os.remove(workspace / subdir / '.git')
                except (OSError, IOError):
                    pass
                clean_output = repo.git.clean('-xd', subdir, force=True)
                if clean_output:
                    log.info('%s', clean_output)

            repo.git.worktree('prune')

            for subdir, ref in worktrees.items():
                repo.git.worktree('add', subdir, ref.commit)

    if 'remote' not in git_cfg and 'ref' not in git_cfg:
        return

    code_dir_re = re.compile(r'^code(?:-\d+)$')
    code_dirs = sorted(Path(dir) for dir in os.listdir(workspace) if code_dir_re.match(dir))
    for dir in code_dirs:
        try:
            with git.Repo(workspace / dir):
                pass
        except (git.InvalidGitRepositoryError, git.NoSuchPathError):
            pass
        else:
            code_dir = dir
            break
    else:
        seq = 0
        while True:
            dir = Path('code' if seq == 0 else f"code-{seq:03}")
            seq += 1
            if dir not in code_dirs:
                code_dir = dir
                break

    # Check out configured repository and mark it as the code directory of this one
    ctx.obj.code_dir = workspace / code_dir
    with git.Repo(workspace) as repo, repo.config_writer() as cfg:
        cfg.remove_section('hopic.code')
        cfg.set_value('hopic.code', 'dir', str(code_dir))
        cfg.set_value('hopic.code', 'cfg-remote', target_remote)
        cfg.set_value('hopic.code', 'cfg-ref', target_ref)
        cfg.set_value('hopic.code', 'cfg-clean', str(clean))

    checkout_tree(
        ctx.obj.code_dir,
        git_cfg.get("remote", target_remote),
        git_cfg.get("ref", target_ref),
        clean=clean,
        clean_config=ctx.obj.config["clean"],
    )


@main.group()
# git
@click.option('--author-name'               , metavar='<name>'                 , help='''Name of change-request's author''')
@click.option('--author-email'              , metavar='<email>'                , help='''E-mail address of change-request's author''')
@click.option('--author-date'               , metavar='<date>', type=DateTime(), help='''Time of last update to the change-request''')
@click.option('--commit-date'               , metavar='<date>', type=DateTime(), help='''Time of starting to build this change-request''')
def prepare_source_tree(*args, **kwargs):
    """
    Prepares the source tree for building a change performed by a subcommand.
    """

    pass


@prepare_source_tree.resultcallback()
@click.pass_context
def process_prepare_source_tree(
            ctx,
            change_applicator,
            author_name,
            author_email,
            author_date,
            commit_date,
        ):
    with git.Repo(ctx.obj.workspace) as repo:
        if author_name is None or author_email is None:
            # This relies on /etc/passwd entries as a fallback, which might contain the info we need
            # for the current UID. Hence the conditional.
            author = git.Actor.author(repo.config_reader())
            if author_name is not None:
                author.name = author_name
            if author_email is not None:
                author.email = author_email
        else:
            author = git.Actor(author_name, author_email)

        try:
            committer = git.Actor.committer(repo.config_reader())
        except KeyError:
            committer = git.Actor(None, None)
        if not committer.name:
            committer.name = author.name
        if not committer.email:
            committer.email = author.email

        target_commit = repo.head.commit

        with repo.config_writer() as cfg:
            section = f"hopic.{target_commit}"
            target_ref    = cfg.get(section, 'ref', fallback=None)
            target_remote = cfg.get(section, 'remote', fallback=None)
            code_clean    = cfg.getboolean('hopic.code', 'cfg-clean', fallback=False)

        repo.git.submodule(["deinit", "--all", "--force"])  # Remove submodules in case it is changed in change_applicator
        commit_params = change_applicator(repo, author=author, committer=committer)
        if not commit_params:
            return
        source_commit = commit_params.pop('source_commit', None)
        base_commit = commit_params.pop('base_commit', None)
        bump_message = commit_params.pop('bump_message', None)

        # Re-read config when it was not read already to ensure any changes introduced by 'change_applicator' are taken into account
        if not commit_params.pop('config_parsed', False):
            install_extensions_and_parse_config()

        # Ensure that, when we're dealing with a separated config and code repository, that the code repository is checked out again to the newer version
        if ctx.obj.code_dir != ctx.obj.workspace:
            with repo.config_reader() as cfg:
                try:
                    code_remote = ctx.obj.config['scm']['git']['remote']
                except (KeyError, TypeError):
                    code_remote = cfg.get_value('hopic.code', 'cfg-remote')
                try:
                    code_ref = ctx.obj.config["scm"]["git"]["ref"]
                except (KeyError, TypeError):
                    code_ref = cfg.get_value("hopic.code", "cfg-ref")

            checkout_tree(
                ctx.obj.code_dir,
                code_remote,
                code_ref,
                clean=code_clean,
                clean_config=ctx.obj.config["clean"],
            )

        version_info = ctx.obj.config['version']

        # Re-read version to ensure that the version policy in the reloaded configuration is used for it
        ctx.obj.version, _ = determine_version(version_info, ctx.obj.config_dir, ctx.obj.code_dir)

        # If the branch is not allowed to publish, skip version bump step
        is_publish_allowed = is_publish_branch(ctx)

        if 'file' in version_info:
            relative_version_file = os.path.relpath(os.path.join(os.path.relpath(ctx.obj.config_dir, repo.working_dir), version_info['file']))

        bump = version_info['bump'].copy()
        bump.update(commit_params.pop('bump-override', {}))
        strict = bump.get('strict', False)

        source_commits = (
                () if source_commit is None and base_commit is None
                else [
                    parse_commit_message(commit, policy=bump['policy'], strict=strict)
                    for commit in git.Commit.list_items(
                        repo,
                        (f"{base_commit}..{target_commit}"
                            if base_commit is not None
                            else f"{target_commit}..{source_commit}"),
                        first_parent=bump.get('first-parent', True),
                        no_merges=bump.get('no-merges', True),
                    )])

        hotfix = hotfix_id(version_info["hotfix-branch"], target_ref)

        def _is_valid_hotfix_base(version) -> bool:
            if not version.prerelease:
                # full release: valid point to start a hotfix from
                return True
            # Pre-release must be a valid hotfix prefix for the current hotfix ID
            return version.prerelease[:len(hotfix) + 1] == ("hotfix", *hotfix)

        if bump['policy'] == 'conventional-commits' and target_ref is not None:
            has_fix = False
            for commit in source_commits:
                if commit.has_breaking_change():
                    if bump['reject-breaking-changes-on'].match(target_ref):
                        raise VersioningError(
                                f"Breaking changes are not allowed on '{target_ref}', but commit '{commit.hexsha}' contains one:\n{commit.message}")
                    elif hotfix:
                        raise VersioningError(
                            f"Breaking changes are not allowed on hotfix branch '{target_ref}', but commit '{commit.hexsha}' contains one:\n{commit.message}")
                if commit.has_new_feature():
                    if bump['reject-new-features-on'].match(target_ref):
                        raise VersioningError(f"New features are not allowed on '{target_ref}', but commit '{commit.hexsha}' contains one:\n{commit.message}")
                    elif hotfix:
                        raise VersioningError(
                            f"New features are not allowed on hotfix branch '{target_ref}', but commit '{commit.hexsha}' contains one:\n{commit.message}")
                if commit.has_fix():
                    has_fix = True
            if hotfix and bump["on-every-change"] and not has_fix:
                raise VersioningError(
                    f"The presence of a 'fix' commit is mandatory on hotfix branch '{target_ref}', but none of these commits contains one:\n"
                    + ', '.join(str(commit) for commit in source_commits)
                )

        version_bumped = False
        if is_publish_allowed and bump['policy'] != 'disabled' and bump['on-every-change']:
            if ctx.obj.version is None:
                if 'file' in version_info:
                    raise VersioningError(f"Failed to read the current version (from {version_info['file']}) while attempting to bump the version")
                else:
                    msg = "Failed to determine the current version while attempting to bump the version"
                    log.error(msg)
                    # TODO: PIPE-309: provide an initial starting point instead
                    log.info("If this is a new repository you may wish to create a 0.0.0 tag for Hopic to start bumping from")
                    raise VersioningError(msg)

            cur_version = ctx.obj.version
            if hotfix:
                base_version = cur_version

                if "file" not in version_info:
                    with git.Repo(ctx.obj.code_dir) as code_repo:
                        gitversion = determine_git_version(code_repo)

                    params = {}
                    try:
                        params["format"] = version_info["format"]
                    except KeyError:
                        pass

                    # strip dirty state from version to ensure we're not complaining about that in the _is_valid_hotfix_base check below
                    gitversion = GitVersion(tag_name=gitversion.tag_name, commit_hash=gitversion.commit_hash, commit_count=gitversion.commit_count)
                    base_version = gitversion.to_version(**params)

                    # strip commit distance from version to ensure we're bumping the hotfix suffix instead of the commit distance suffix
                    gitversion = GitVersion(tag_name=gitversion.tag_name, commit_hash=gitversion.commit_hash)
                    cur_version = gitversion.to_version(**params)

                if not _is_valid_hotfix_base(base_version):
                    raise VersioningError(f"Creating hotfixes on anything but a full release is not supported. Currently on: {base_version}")

                release_part = copy(cur_version)
                release_part.build = ()
                release_part = str(release_part)
                if re.search(f"\\b{re.escape(release_part)}\\b", str(hotfix)):
                    raise VersioningError(f"Hotfix ID '{hotfix}' is not allowed to contain the base version '{release_part}'")

            if bump['policy'] == 'constant':
                params = {}
                if 'field' in bump:
                    params['bump'] = bump['field']
                if hotfix:
                    params["bump"] = "prerelease"
                    params["prerelease_seed"] = ("hotfix", *hotfix)
                    assert _is_valid_hotfix_base(cur_version), "implementation error: invalid hotfix bases should have been caught already"
                new_version = cur_version.next_version(**params)
            elif bump['policy'] in ('conventional-commits',):
                all_commits = source_commits
                if 'message' in commit_params:
                    all_commits = (*source_commits, parse_commit_message(commit_params['message'], policy=bump['policy'], strict=strict))

                if log.isEnabledFor(logging.DEBUG):
                    log.debug("bumping based on conventional commits:")
                    for commit in all_commits:
                        breaking = ('breaking' if commit.has_breaking_change() else '')
                        feat = ('feat' if commit.has_new_feature() else '')
                        fix = ('fix' if commit.has_fix() else '')
                        try:
                            hash_prefix = click.style(commit.hexsha, fg='yellow') + ': '
                        except AttributeError:
                            hash_prefix = ''
                        log.debug("%s[%-8s][%-4s][%-3s]: %s", hash_prefix, breaking, feat, fix, commit.full_subject)
                new_version = cur_version.next_version_for_commits(all_commits)
                if hotfix and new_version != cur_version:
                    assert (new_version.major, new_version.minor) == (cur_version.major, cur_version.minor), (
                        "bumping anything other than 'patch' shouldn't happen for hotfix branches and should have been caught already"
                    )
                    assert _is_valid_hotfix_base(cur_version), "implementation error: invalid hotfix bases should have been caught already"
                    new_version = cur_version.next_prerelease(seed=("hotfix", *hotfix))
            else:
                raise NotImplementedError(f"unsupported version bumping policy {bump['policy']}")

            assert new_version >= cur_version, f"the new version {new_version} should be more recent than the old one {cur_version}"

            if new_version != cur_version:
                log.info("bumped version to: %s (from %s)", click.style(str(new_version), fg='blue'), click.style(str(ctx.obj.version), fg='blue'))
                version_bumped = True
                ctx.obj.version = new_version

                if 'file' in version_info:
                    replace_version(ctx.obj.config_dir / version_info['file'], ctx.obj.version)
                    repo.index.add((relative_version_file,))
                    if bump_message is not None:
                        commit_params.setdefault('message', bump_message)
        else:
            log.info("Skip version bumping due to the configuration or the target branch is not allowed to publish")

        commit_params.setdefault('author', author)
        commit_params.setdefault('committer', committer)
        if author_date is not None:
            commit_params['author_date'] = to_git_time(author_date)
        if commit_date is not None:
            commit_params['commit_date'] = to_git_time(commit_date)

        if bump_message is not None and not version_bumped:
            log.error("Version bumping requested, but the version policy '%s' decided not to bump from '%s'", bump['policy'], ctx.obj.version)
            return

        notes_ref = None
        if 'message' in commit_params:
            submit_commit = repo.index.commit(**commit_params)

            pkgs = utils.installed_pkgs()
            if pkgs and target_ref:
                notes_ref = f"refs/notes/hopic/{target_ref}"
                env = {
                        'GIT_AUTHOR_NAME': author.name,
                        'GIT_AUTHOR_EMAIL': author.email,
                        'GIT_COMMITTER_NAME': committer.name,
                        'GIT_COMMITTER_EMAIL': committer.email,
                    }
                if 'author_date' in commit_params:
                    env['GIT_AUTHOR_DATE'] = commit_params['author_date']
                if 'commit_date' in commit_params:
                    env['GIT_COMMITTER_DATE'] = commit_params['commit_date']

                hopic_commit_version = f"Committed-by: Hopic {utils.get_package_version(PACKAGE)}"
                notes_message = dedent("""\
                {hopic_commit_version}

                With Python version: {python_version}

                And with these installed packages:
                {pkgs}
                """).format(
                    hopic_commit_version=hopic_commit_version,
                    pkgs=pkgs,
                    python_version=platform.python_version())

                try:
                    notes = repo.git.notes('show', submit_commit.hexsha, ref=notes_ref)
                    if hopic_commit_version not in notes:
                        raise GitNotesMismatchError(submit_commit.hexsha, notes_message, notes)
                except git.GitCommandError:
                    notes = None

                if notes is None:
                    repo.git.notes(
                        'add', submit_commit.hexsha,
                        '--message=' + notes_message,
                        ref=notes_ref, env=env)

                notes_ref = f"{repo.commit(notes_ref)}:refs/notes/hopic/{target_ref}"
        else:
            submit_commit = repo.head.commit
        click.echo(submit_commit)

        autosquash_commits = [
                commit
                for commit in source_commits
                if commit.needs_autosquash()
            ]

        # Autosquash the merged commits (if any) to discover how that would look like.
        autosquash_base = None
        if autosquash_commits:
            commit = autosquash_commits[0]
            log.debug("Found an autosquash-commit in the source commits: '%s': %s", commit.subject, click.style(commit.hexsha, fg='yellow'))
            autosquash_base = repo.merge_base(target_commit, source_commit)
        autosquashed_commit = None
        if autosquash_base:
            repo.head.reference = source_commit
            repo.head.reset(index=True, working_tree=True)
            try:
                try:
                    env = {'GIT_SEQUENCE_EDITOR': ':'}
                    if 'commit_date' in commit_params:
                        env['GIT_COMMITTER_DATE'] = commit_params['commit_date']
                    repo.git.rebase(autosquash_base, interactive=True, autosquash=True, env=env, kill_after_timeout=300)
                except git.GitCommandError as e:
                    log.warning('Failed to perform auto squashing rebase: %s', e)
                else:
                    autosquashed_commit = repo.head.commit
                    if log.isEnabledFor(logging.DEBUG):
                        log.debug('Autosquashed to:')
                        for commit in git.Commit.list_items(repo, f"{target_commit}..{autosquashed_commit}", first_parent=True, no_merges=True):
                            subject = commit.message.splitlines()[0]
                            log.debug('%s %s', click.style(str(commit), fg='yellow'), subject)
            finally:
                repo.head.reference = submit_commit
                repo.head.reset(index=True, working_tree=True)

        update_submodules(repo, code_clean)

        if code_clean:
            restore_mtime_from_git(repo)

        # Tagging after bumping the version
        tagref = None
        version_tag = version_info.get('tag', False)
        if version_bumped and _is_valid_hotfix_base(ctx.obj.version) and version_tag and is_publish_allowed:
            if not isinstance(version_tag, str):
                version_tag = ctx.obj.version.default_tag_name
            tagname = version_tag.format(
                    version        = ctx.obj.version,                                           # noqa: E251 "unexpected spaces around '='"
                    build_sep      = ('+' if getattr(ctx.obj.version, 'build', None) else ''),  # noqa: E251 "unexpected spaces around '='"
                )
            if hotfix and "-hotfix." not in tagname:
                raise VersioningError(
                    f"Tag '{tagname}' for hotfix version '{ctx.obj.version}' does not contain 'hotfix' in its prerelease portion.\n"
                    f"Likely the tag pattern ('{version_tag}') omitted the prerelease portion"
                )
            if 'build' in version_info and '+' not in tagname:
                tagname += f"+{version_info['build']}"
            tagref = repo.create_tag(
                    tagname, submit_commit, force=True,
                    message=f"Tagged-by: Hopic {get_package_version(PACKAGE)}",
                    env={
                        'GIT_COMMITTER_NAME': committer.name,
                        'GIT_COMMITTER_EMAIL': committer.email,
                    },
                )

        # Re-read version to ensure that the newly created tag is taken into account
        ctx.obj.version, _ = determine_version(version_info, ctx.obj.config_dir, ctx.obj.code_dir)

        log.info('%s', repo.git.show(submit_commit, format='fuller', stat=True, notes='*'))

        push_commit = submit_commit
        if (ctx.obj.version is not None
                and 'file' in version_info and 'bump' in version_info.get('after-submit', {})
                and is_publish_allowed and bump['on-every-change']):
            params = {'bump': version_info['after-submit']['bump']}
            try:
                params['prerelease_seed'] = version_info['after-submit']['prerelease-seed']
            except KeyError:
                pass
            after_submit_version = ctx.obj.version.next_version(**params)
            log.debug("bumped post-submit version to: %s", click.style(str(after_submit_version), fg='blue'))

            new_version_file = StringIO()
            replace_version(ctx.obj.config_dir / version_info['file'], after_submit_version, outfile=new_version_file)
            new_version_file = new_version_file.getvalue().encode(sys.getdefaultencoding())

            old_version_blob = submit_commit.tree[relative_version_file]
            new_version_blob = git.Blob(
                    repo=repo,
                    binsha=repo.odb.store(gitdb.IStream(git.Blob.type, len(new_version_file), BytesIO(new_version_file))).binsha,
                    mode=old_version_blob.mode,
                    path=old_version_blob.path,
                )
            new_index = repo.index.from_tree(repo, submit_commit)
            new_index.add([new_version_blob], write=False)

            commit_params['author'] = commit_params['committer']
            if 'author_date' in commit_params and 'commit_date' in commit_params:
                commit_params['author_date'] = commit_params['commit_date']
            commit_params['message'] = f"[ Release build ] new version commit: {after_submit_version}\n"
            commit_params['parent_commits'] = (submit_commit,)
            # Prevent advancing HEAD
            commit_params['head'] = False

            push_commit = new_index.commit(**commit_params)
            log.info('%s', repo.git.show(push_commit, format='fuller', stat=True))

        with repo.config_writer() as cfg:
            cfg.remove_section(f"hopic.{target_commit}")
            section = f"hopic.{submit_commit}"
            if target_remote is not None:
                cfg.set_value(section, 'remote', target_remote)
            cfg.set_value(section, "version-bumped", str(version_bumped))
            refspecs = []
            if target_ref is not None:
                cfg.set_value(section, 'ref', target_ref)
                refspecs.append(f"{push_commit}:{target_ref}")
            if tagref is not None:
                refspecs.append(f"{tagref.object}:{tagref.path}")
            if notes_ref is not None:
                refspecs.append(notes_ref)
            if refspecs:
                cfg.set_value(section, 'refspecs', ' '.join(shlex.quote(refspec) for refspec in refspecs))
            if source_commit:
                cfg.set_value(section, 'target-commit', str(target_commit))
                cfg.set_value(section, 'source-commit', str(source_commit))
            if autosquashed_commit:
                cfg.set_value(section, 'autosquashed-commit', str(autosquashed_commit))
        if ctx.obj.version is not None:
            click.echo(ctx.obj.version)


@prepare_source_tree.command()
@click.pass_context
# git
@click.option('--source-remote' , metavar='<url>', help='<source> remote to merge into <target>')
@click.option('--source-ref'    , metavar='<ref>', help='ref of <source> remote to merge into <target>')
@click.option('--change-request', metavar='<identifier>'           , help='Identifier of change-request to use in merge commit message')
@click.option('--title'         , metavar='<title>'                , help='''Change request title to incorporate in merge commit's subject line''')
@click.option('--description'   , metavar='<description>'          , help='''Change request description to incorporate in merge commit message's body''')
@click.option('--approved-by'   , metavar='<approver>'             , help='''Name of approving reviewer (can be provided multiple times).''', multiple=True)
def merge_change_request(
            ctx,
            source_remote,
            source_ref,
            change_request,
            title,
            description,
            approved_by,
        ):
    """
    Merges the change request from the specified branch.
    """

    def get_valid_approvers(repo, approved_by_list, source_remote, source_commit):
        """Inspects approvers list and, where possible, checks if approval is still valid."""

        valid_hash_re = re.compile(r"^(.+):([0-9a-zA-Z]{40})$")
        autosquash_re = re.compile(r'^(fixup|squash)!\s+')
        valid_approvers = []

        # Fetch the hashes from the remote in one go
        approved_hashes = [entry.group(2) for entry in (valid_hash_re.match(entry) for entry in approved_by_list) if entry]
        try:
            source_remote.fetch(approved_hashes)
        except git.GitCommandError:
            log.warning("One or more of the last reviewed commit hashes invalid: '%s'", ' '.join(approved_hashes))

        for approval_entry in approved_by_list:
            hash_match = valid_hash_re.match(approval_entry)
            if not hash_match:
                valid_approvers.append(approval_entry)
                continue

            approver, last_reviewed_commit_hash = hash_match.groups()
            try:
                last_reviewed_commit = repo.commit(last_reviewed_commit_hash)
            except ValueError:
                log.warning("Approval for '%s' is ignored, as the associated hash is unknown or invalid: '%s'", approver, last_reviewed_commit_hash)
                continue

            if last_reviewed_commit_hash == source_commit.hexsha:
                valid_approvers.append(approver)
                continue
            if last_reviewed_commit.diff(source_commit):
                log.warning(
                        "Approval for '%s' is not valid anymore due to content changes compared to last reviewed commit '%s'",
                        approver, last_reviewed_commit_hash)
                continue

            # Source has a different hash, but no content diffs.
            # Now 'squash' and compare metadata (author, date, commit message).
            merge_base = repo.merge_base(repo.head.commit, source_commit)

            source_commits = [
                    (commit.author, commit.authored_date, commit.message.rstrip()) for commit in
                    git.Commit.list_items(repo, merge_base[0].hexsha + '..' + source_commit.hexsha, first_parent=True, no_merges=True)]

            autosquashed_reviewed_commits = [
                    (commit.author, commit.authored_date, commit.message.rstrip()) for commit in
                    git.Commit.list_items(repo, merge_base[0].hexsha + '..' + last_reviewed_commit.hexsha, first_parent=True, no_merges=True)
                    if not autosquash_re.match(commit.message)]

            log.debug(
                    "For approver '%s', checking source commits:\n%s\n.. against squashed reviewed commits:\n%s",
                    approver, source_commits, autosquashed_reviewed_commits)

            if autosquashed_reviewed_commits == source_commits:
                log.debug("Approval for '%s' is still valid", approver)
                valid_approvers.append(approver)
            else:
                log.warning(
                        "Approval for '%s' is not valid anymore due to metadata changes compared to last reviewed commit '%s'",
                        approver, last_reviewed_commit_hash)
        return valid_approvers

    def change_applicator(repo, author, committer):
        try:
            source = repo.remotes.source
        except AttributeError:
            source = repo.create_remote('source', source_remote)
        else:
            source.set_url(source_remote)
        source_commit = source.fetch(source_ref)[0].commit

        repo.git.merge(source_commit, no_ff=True, no_commit=True, env={
            'GIT_AUTHOR_NAME': author.name,
            'GIT_AUTHOR_EMAIL': author.email,
            'GIT_COMMITTER_NAME': committer.name,
            'GIT_COMMITTER_EMAIL': committer.email,
        })

        msg = f"Merge #{change_request}"
        if title is not None:
            msg = f"{msg}: {title}\n"
        if description is not None:
            msg = f"{msg}\n{description}\n"

        # Prevent splitting footers with empty lines in between, because 'git interpret-trailers' doesn't like it.
        parsed_msg = parse_commit_message(msg)
        if not parsed_msg.footers:
            msg += u'\n'

        approvers = get_valid_approvers(repo, approved_by, source, source_commit)
        if approvers:
            msg += '\n'.join(f"Acked-by: {approver}" for approver in approvers) + u'\n'
        msg += f'Merged-by: Hopic {get_package_version(PACKAGE)}\n'

        # Reread config & install extensions after potential configuration file change
        install_extensions_and_parse_config()

        bump = ctx.obj.config['version']['bump']
        strict = bump.get('strict', False)
        try:
            merge_commit = parse_commit_message(msg, policy=bump['policy'], strict=strict)
        except Exception as e:
            if bump['policy'] == 'conventional-commits':
                log.error(
                    "The pull request title could not be parsed as a conventional commit.\n"
                    "Parsing the PR title failed due to:\n%s",
                    "".join(f" - {problem}\n" for problem in str(e).split('\n'))
                )
                ctx.exit(1)
            raise

        if bump['policy'] in ('conventional-commits',) and strict and bump['on-every-change']:
            source_commits = ([
                parse_commit_message(commit, policy=bump['policy'], strict=False)
                for commit in git.Commit.list_items(
                    repo,
                    (f"{repo.head.commit}..{source_commit}"),
                    first_parent=bump.get('first-parent', True),
                    no_merges=bump.get('no-merges', True),
                )])
            new_version = ctx.obj.version.next_version_for_commits(source_commits)
            merge_commit_next_version = ctx.obj.version.next_version_for_commits([merge_commit])
            if new_version != merge_commit_next_version:
                raise VersionBumpMismatchError(new_version, merge_commit_next_version)

        return {
                'config_parsed': True,
                'message': msg,
                'parent_commits': (
                    repo.head.commit,
                    source_commit,
                ),
                'source_commit': source_commit,
            }
    return change_applicator


_env_var_re = re.compile(r'^(?P<var>[A-Za-z_][0-9A-Za-z_]*)=(?P<val>.*)$')
@prepare_source_tree.command()  # noqa: E302 'expected 2 blank lines'
@click.argument('modality', autocompletion=autocomplete.modality_from_config)
@click.pass_context
def apply_modality_change(
            ctx,
            modality,
        ):
    """
    Applies the changes specific to the specified modality.
    """

    # Ensure any required extensions are available
    install_extensions_and_parse_config()

    modality_cmds = ctx.obj.config.get('modality-source-preparation', {}).get(modality, ())

    def change_applicator(repo, author, committer):
        has_changed_files = False
        commit_message = modality
        for cmd in modality_cmds:
            try:
                cmd["changed-files"]
            except (KeyError, TypeError):
                pass
            else:
                has_changed_files = True
            try:
                commit_message = cmd["commit-message"]
            except (KeyError, TypeError):
                pass

        if not has_changed_files:
            # Force clean builds when we don't know how to discover changed files
            repo.git.clean('-xd', force=True)

        volume_vars = ctx.obj.volume_vars.copy()
        volume_vars.setdefault('HOME', os.path.expanduser('~'))
        vars_from_env = {key: value for key, value in os.environ.items() if key in ctx.obj.config["pass-through-environment-vars"]}
        vars_from_env.update(volume_vars)
        volume_vars = vars_from_env

        commit_message = expand_vars(volume_vars, commit_message)

        for cmd in modality_cmds:
            if isinstance(cmd, str):
                cmd = {"sh": cmd}

            if 'description' in cmd:
                desc = cmd['description']
                log.info('Performing: %s', click.style(desc, fg='cyan'))

            if 'sh' in cmd:
                args = shlex.split(cmd['sh'])
                env = os.environ.copy()
                while args:
                    m = _env_var_re.match(args[0])
                    if not m:
                        break
                    env[m.group('var')] = expand_vars(volume_vars, m.group('val'))
                    args.pop(0)

                args = [expand_vars(volume_vars, arg) for arg in args]
                try:
                    echo_cmd(subprocess.check_call, args, cwd=repo.working_dir, env=env, stdout=sys.__stderr__)
                except subprocess.CalledProcessError as e:
                    log.error("Command fatally terminated with exit code %d", e.returncode)
                    ctx.exit(e.returncode)

            if 'changed-files' in cmd:
                changed_files = cmd["changed-files"]
                if isinstance(changed_files, str):
                    changed_files = [changed_files]
                changed_files = [expand_vars(volume_vars, f) for f in changed_files]
                repo.index.add(changed_files)

        if not has_changed_files:
            # 'git add --all' equivalent (excluding the code_dir)
            add_files = set(repo.untracked_files)
            remove_files = set()
            with repo.config_reader() as cfg:
                try:
                    code_dir = cfg.get_value('hopic.code', 'dir')
                except (NoOptionError, NoSectionError):
                    pass
                else:
                    if code_dir in add_files:
                        add_files.remove(code_dir)
                    if (code_dir + '/') in add_files:
                        add_files.remove(code_dir + '/')

            for diff in repo.index.diff(None):
                if not diff.deleted_file:
                    add_files.add(diff.b_path)
                remove_files.add(diff.a_path)
            remove_files -= add_files
            if remove_files:
                repo.index.remove(remove_files)
            if add_files:
                repo.index.add(add_files)

        if not repo.index.diff(repo.head.commit):
            log.info("No changes introduced by '%s'", commit_message)
            return None
        commit_message = dedent(f"""\
            {commit_message.rstrip()}

            Merged-by: Hopic {get_package_version(PACKAGE)}
            """)

        commit_params = {'message': commit_message}
        # If this change was a merge make sure to produce a merge commit for it
        try:
            commit_params['parent_commits'] = (
                    repo.commit('ORIG_HEAD'),
                    repo.commit('MERGE_HEAD'),
                )
        except git.BadName:
            pass
        return commit_params

    return change_applicator


@prepare_source_tree.command()
@click.pass_context
def bump_version(ctx):
    """
    Bump the version based on the configuration.
    """

    def change_applicator(repo, author, committer):
        gitversion = determine_git_version(repo)
        if gitversion.exact:
            log.info("Not bumping because no new commits are present since the last tag '%s'", gitversion.tag_name)
            return None
        tag = repo.tags[gitversion.tag_name]
        return {
            'bump_message': dedent(f"""\
                    chore: release new version

                    Bumped-by: Hopic {get_package_version(PACKAGE)}
                    """),
            'base_commit': tag.commit,
            'bump-override': {
                'on-every-change': True,
                'strict': False,
                'first-parent': False,
                'no-merges': False,
            },
        }

    return change_applicator


@main.command()
@click.option('--phase'      , '-p' , metavar='<phase>'  , multiple=True, help='''Build phase''', autocompletion=autocomplete.phase_from_config)
@click.option('--variant'    , '-v' , metavar='<variant>', multiple=True, help='''Configuration variant''', autocompletion=autocomplete.variant_from_config)
@click.option('--post-submit'       , is_flag=True       ,                help='''Display only post-submit meta-data.''')
@click.pass_context
def getinfo(ctx, phase, variant, post_submit):
    """
    Display meta-data associated with each (or the specified) variant in each (or the specified) phase.

    The output is JSON encoded.

    If a phase or variant filter is specified the name of that will not be present in the output.
    Otherwise this is a nested dictionary of phases and variants.
    """
    info = OrderedDict()

    def append_meta_from_cmd(info, cmd, permitted_fields: Set):
        assert isinstance(cmd, Mapping)

        info = info.copy()

        for key, val in cmd.items():
            if key == "timeout" and "sh" not in cmd:
                # Return _global_ timeout (not "sh"-specific) only
                pass
            elif key not in permitted_fields:
                continue

            try:
                val = expand_vars(ctx.obj.volume_vars, val)
            except KeyError:
                pass
            else:
                if key == "timeout":
                    if key in info:
                        info[key] += val
                    else:
                        info[key] = val
                elif isinstance(info.get(key), Mapping):
                    assert isinstance(info[key], MutableMapping)
                    for subkey, subval in val.items():
                        info[key].setdefault(subkey, subval)
                elif isinstance(info.get(key), MutableSequence):
                    info[key].extend(val)
                else:
                    info.setdefault(key, val)

        return info

    if post_submit:
        permitted_fields = frozenset({
            'node-label',
            'with-credentials',
        })
        for phasename, cmds in ctx.obj.config['post-submit'].items():
            for cmd in cmds:
                info.update(append_meta_from_cmd(info, cmd, permitted_fields))
    else:
        permitted_fields = frozenset({
            'archive',
            'wait-on-full-previous-phase',
            'fingerprint',
            'junit',
            'node-label',
            'run-on-change',
            'stash',
            'with-credentials',
            'worktrees',
        })
        for phasename, curphase in ctx.obj.config['phases'].items():
            if phase and phasename not in phase:
                continue
            for variantname, curvariant in curphase.items():
                if variant and variantname not in variant:
                    continue

                # Only store phase/variant keys if we're not filtering on a single one of them.
                var_info = info
                if len(phase) != 1:
                    var_info = var_info.setdefault(phasename, OrderedDict())
                if len(variant) != 1:
                    var_info = var_info.setdefault(variantname, OrderedDict())

                for cmd in curvariant:
                    var_info.update(append_meta_from_cmd(var_info, cmd, permitted_fields))

                # mark empty variants as being a nop
                if (
                    len(curvariant) == 0
                    or (
                        len(curvariant) == 1
                        and dict(curvariant[0]) == {"wait-on-full-previous-phase": False}
                    )
                ):
                    var_info["nop"] = True
    click.echo(json.dumps(info, indent=4, separators=(',', ': '), cls=JSONEncoder))


@main.command()
@click.option('--bundle', metavar='<file>', help='Git bundle to use', type=click.Path(file_okay=True, dir_okay=False, readable=True, resolve_path=True))
@click.pass_context
def unbundle_worktrees(ctx, bundle):
    """
    Unbundle a git bundle and fast-forward all the configured worktrees that are included in it.
    """

    with git.Repo(ctx.obj.workspace) as repo:
        submit_commit = repo.head.commit
        section = f"hopic.{submit_commit}"
        with repo.config_reader() as git_cfg:
            try:
                refspecs = list(shlex.split(git_cfg.get_value(section, 'refspecs')))
            except (NoOptionError, NoSectionError):
                refspecs = []

        head_path = 'refs/heads/'
        worktrees = dict((v, k) for k, v in ctx.obj.config['scm']['git']['worktrees'].items())
        for headline in repo.git.bundle('list-heads', bundle).splitlines():
            commit, ref = headline.split(' ', 1)
            if not ref.startswith(head_path):
                continue
            ref = ref[len(head_path):]
            if ref not in worktrees:
                continue

            subdir = worktrees[ref]
            log.debug("Checkout worktree '%s' to '%s' (proposed branch '%s')", subdir, commit, ref)
            checkout_tree(
                ctx.obj.workspace / subdir,
                bundle,
                ref,
                remote_name="bundle",
                tags=False,
            )
            refspecs.append(f"{commit}:{ref}")

        # Eliminate duplicate pushes to the same ref and replace it by a single push to the _last_ specified object
        seen_refs = set()
        new_refspecs = []
        for refspec in reversed(refspecs):
            _, ref = refspec.rsplit(':', 1)
            if ref in seen_refs:
                continue
            new_refspecs.insert(0, refspec)
            seen_refs.add(ref)
        refspecs = new_refspecs

        with repo.config_writer() as cfg:
            cfg.set_value(section, 'refspecs', ' '.join(shlex.quote(refspec) for refspec in refspecs))


@main.command()
@click.option('--target-remote', metavar='<url>', help='''The remote to push to, if not specified this will default to the checkout remote.''')
@click.pass_context
def submit(ctx, target_remote):
    """
    Submit the changes created by prepare-source-tree to the target remote.
    """

    with git.Repo(ctx.obj.workspace) as repo:
        section = f"hopic.{repo.head.commit}"
        with repo.config_reader() as cfg:
            if target_remote is None:
                target_remote = cfg.get_value(section, 'remote')
            refspecs = shlex.split(cfg.get_value(section, 'refspecs'))

        repo.git.push(target_remote, refspecs, atomic=True)

        hopic_git_info = HopicGitInfo.from_repo(repo)
        with repo.config_writer() as cfg:
            cfg.remove_section(section)

    for phase in ctx.obj.config['post-submit'].values():
        build.build_variant(variant='post-submit', cmds=phase, hopic_git_info=hopic_git_info)


@main.command()
@click.pass_context
def show_config(ctx):
    """
    Diagnostic helper command to display the configuration after processing.
    """

    click.echo(json.dumps(ctx.obj.config, indent=4, separators=(',', ': '), cls=JSONEncoder))


@main.command()
@click.pass_context
def show_env(ctx):
    """
    Diagnostic helper command to display the execution environment.
    """

    click.echo(json.dumps(ctx.obj.volume_vars, indent=4, separators=(',', ': '), sort_keys=True))
