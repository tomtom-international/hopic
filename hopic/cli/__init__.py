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
from commisery.commit import CommitMessage, parse_commit_message
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
    Version,
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
import typing
from typing import (
    AbstractSet,
    Any,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
    Tuple,
    Union,
    overload,
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
from ..types import PathLike


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
    tree: PathLike,
    remote: Optional[str],
    ref: str,
    *,
    commit: Union[str, git.Commit, None] = None,
    clean: bool = False,
    remote_name: str = 'origin',
    tags: bool = True,
    allow_submodule_checkout_failure: bool = False,
    clean_config: Union[List, Tuple] = (),
):
    fresh = clean
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

        assert remote is not None
        repo = git.Repo.clone_from(remote, tree)
        fresh = True

    with repo:
        with repo.config_writer() as cfg:
            cfg.remove_section('hopic.code')
            cfg.set_value('color', 'ui', 'always')
            cfg.set_value('hopic.code', 'cfg-clean', str(clean))
            cfg.set_value("hopic.code", "fresh", str(fresh))

        if remote is not None:
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

            # Ensure we have the exact same view of all Hopic notes as are present upstream
            origin.fetch("+refs/notes/hopic/*:refs/notes/hopic/*", prune=True)
        else:
            assert commit is not None
            if isinstance(commit, str):
                commit = repo.commit(commit)

        repo.head.reference = commit
        repo.head.reset(index=True, working_tree=True)
        # Remove potentially moved submodules
        repo.git.submodule(["deinit", "--all", "--force"])

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
        if fresh:
            # Only restore mtimes when doing a clean build or working on a fresh clone. This prevents problems with timestamp-based build sytems.
            # I.e. make and ninja and probably half the world.
            restore_mtime_from_git(repo)

        with repo.config_writer() as cfg:
            section = f"hopic.{commit}"
            cfg.set_value(section, 'ref', ref)
            if remote is not None:
                cfg.set_value(section, "remote", remote)

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


def install_extensions_and_parse_config(constraints: Optional[str] = None):
    initialize_global_variables_from_config(extensions.install_extensions.callback(constraints=constraints))


_code_dir_re = re.compile(r"^code(?:-\d+)$")


def find_code_dir(workspace: PathLike) -> Path:
    code_dirs = sorted(Path(dir) for dir in os.listdir(workspace) if _code_dir_re.match(dir))
    for dir in code_dirs:
        try:
            with git.Repo(workspace / dir):
                pass
        except (git.InvalidGitRepositoryError, git.NoSuchPathError):
            pass
        else:
            return dir
    else:
        seq = 0
        while True:
            dir = Path("code" if seq == 0 else f"code-{seq:03}")
            seq += 1
            if dir not in code_dirs:
                return dir


def checkout_worktrees(workspace: Path, worktrees: Dict[PathLike, str]):
    if not worktrees:
        return

    with git.Repo(workspace) as repo:
        fetch_result = repo.remotes.origin.fetch([ref for subdir, ref in worktrees.items()])

        worktree_refs = {Path(subdir): fetchinfo.ref for (subdir, refname), fetchinfo in zip(worktrees.items(), fetch_result)}
        log.debug("Worktree config: %s", worktree_refs)

        for subdir, ref in worktree_refs.items():
            try:
                os.remove(workspace / subdir / ".git")
            except (OSError, IOError):
                pass
            clean_output = repo.git.clean("-xd", subdir, force=True)
            if clean_output:
                log.info("%s", clean_output)

        repo.git.worktree("prune")

        for subdir, ref in worktree_refs.items():
            repo.git.worktree("add", subdir, ref.commit)


def store_commit_meta(repo: git.Repo, commit_meta: Dict[str, Any], *, commit: git.Commit, old_commit: Optional[git.Commit] = None) -> None:
    with repo.config_writer() as cfg:
        if old_commit is not None:
            cfg.remove_section(f"hopic.{old_commit}")
        section = f"hopic.{commit}"
        for key, val in commit_meta.items():
            if val is None:
                continue
            if isinstance(val, bool):
                cfg.set_value(section, key, "true" if val else "false")
            elif isinstance(val, git.Object):
                cfg.set_value(section, key, str(val))
            elif isinstance(val, list):
                cfg.set_value(section, "refspecs", " ".join(shlex.quote(refspec) for refspec in val))
            else:
                cfg.set_value(section, key, val)


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

    checkout_worktrees(workspace, git_cfg["worktrees"])

    if 'remote' not in git_cfg and 'ref' not in git_cfg:
        return

    code_dir = find_code_dir(workspace)

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
# fmt: off
# git
@click.option('--author-name'               , metavar='<name>'                 , help='''Name of change-request's author''')
@click.option('--author-email'              , metavar='<email>'                , help='''E-mail address of change-request's author''')
@click.option('--author-date'               , metavar='<date>', type=DateTime(), help='''Time of last update to the change-request''')
@click.option('--commit-date'               , metavar='<date>', type=DateTime(), help='''Time of starting to build this change-request''')
@click.option("--bundle"                    , metavar="<file>", type=click.Path(file_okay=True, dir_okay=False, writable=True))
@click.option("--constraints"               , metavar="<file>", type=click.Path(exists=True, dir_okay=False, readable=True), help="""Apply the provided constraints file to pip operations""")
# fmt: on
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
    bundle: Optional[PathLike],
    constraints: Optional[str] = None,
):
    ctx.obj.pip_constraints = constraints

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

        with repo.config_reader() as cfg:
            section = f"hopic.{target_commit}"
            target_ref    = cfg.get(section, 'ref', fallback=None)
            target_remote = cfg.get(section, 'remote', fallback=None)
            code_clean    = cfg.getboolean('hopic.code', 'cfg-clean', fallback=False)
            code_fresh = cfg.getboolean("hopic.code", "fresh", fallback=code_clean)

        repo.git.submodule(["deinit", "--all", "--force"])  # Remove submodules in case it is changed in change_applicator
        commit_params = change_applicator(repo, author=author, committer=committer)
        if not commit_params:
            return
        source_commit = commit_params.pop('source_commit', None)
        base_commit = commit_params.pop('base_commit', None)
        bump_message = commit_params.pop('bump_message', None)

        # Re-read config when it was not read already to ensure any changes introduced by 'change_applicator' are taken into account
        if not commit_params.pop('config_parsed', False):
            install_extensions_and_parse_config(ctx.obj.pip_constraints)

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

        commit_from, commit_to = (base_commit, target_commit) if base_commit else (target_commit, source_commit)
        source_commits = tuple(parse_commit_range(repo, commit_from, commit_to, bump))

        change_message = None
        if "message" in commit_params and bump["on-every-change"]:
            change_message = parse_commit_message(commit_params["message"], policy=bump["policy"], strict=bump.get("strict", False))

        hotfix = hotfix_id(version_info["hotfix-branch"], target_ref)

        def _is_valid_hotfix_base(version) -> bool:
            if not version.prerelease:
                # full release: valid point to start a hotfix from
                return True
            # Pre-release must be a valid hotfix prefix for the current hotfix ID
            return version.prerelease[:len(hotfix) + 1] == ("hotfix", *hotfix)

        if bump['policy'] == 'conventional-commits' and target_ref is not None:
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

        version_bumped = False
        if is_version_bump_enabled(bump, is_publish_from_branch_allowed=is_publish_allowed):
            cur_version = get_current_version(ctx)
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
                    commit_count = gitversion.commit_count
                    log.debug("checking for %r in %s..%s", version_info["hotfix-allowed-start-tags"], gitversion.tag_name, gitversion.commit_hash)
                    for commit in parse_commit_range(repo, gitversion.tag_name, gitversion.commit_hash, bump):
                        log.debug(
                            "checking whether %r of %r appears in %r",
                            getattr(commit, "type_tag", None),
                            commit.message,
                            version_info["hotfix-allowed-start-tags"],
                        )
                        if getattr(commit, "type_tag", None) in version_info["hotfix-allowed-start-tags"]:
                            commit_count -= 1
                    gitversion = GitVersion(tag_name=gitversion.tag_name, commit_hash=gitversion.commit_hash, commit_count=commit_count)
                    parse_version = gitversion.to_version(**params)
                    assert parse_version is not None
                    base_version = parse_version

                    # strip commit distance from version to ensure we're bumping the hotfix suffix instead of the commit distance suffix
                    gitversion = GitVersion(tag_name=gitversion.tag_name, commit_hash=gitversion.commit_hash)
                    parse_version = gitversion.to_version(**params)
                    assert parse_version is not None
                    cur_version = parse_version

                if not _is_valid_hotfix_base(base_version):
                    raise VersioningError(f"Creating hotfixes on anything but a full release is not supported. Currently on: {base_version}")

                release_part = str(cur_version.without_meta())
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
                if change_message is not None:
                    all_commits = (*source_commits, change_message)

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

        if code_fresh:
            # Only restore mtimes when doing a clean build or working on a fresh clone. This prevents problems with timestamp-based build sytems.
            # I.e. make and ninja and probably half the world.
            restore_mtime_from_git(repo)

        # Tagging after bumping the version
        tagref = None
        version_tag = version_info.get('tag', False)
        if version_bumped and _is_valid_hotfix_base(ctx.obj.version) and version_tag and is_publish_allowed:
            if not isinstance(version_tag, str):
                assert ctx.obj.version is not None
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
                tagname,
                submit_commit,
                force=True,
                message=f"Tagged-by: Hopic {utils.get_package_version(PACKAGE)}",
                env={
                    "GIT_COMMITTER_NAME": committer.name,
                    "GIT_COMMITTER_EMAIL": committer.email,
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
            new_version_data = new_version_file.getvalue().encode(sys.getdefaultencoding())

            old_version_blob = submit_commit.tree[relative_version_file]
            new_version_blob = git.Blob(
                repo=repo,
                binsha=repo.odb.store(gitdb.IStream(git.Blob.type, len(new_version_data), BytesIO(new_version_data))).binsha,
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

        refspecs = []
        bundle_excludes = [
            target_commit,
        ]
        bundle_refs: List[Tuple[str, Any]] = []

        if target_ref is not None:
            refspecs.append(f"{push_commit}:{target_ref}")
            bundle_refs.append((target_ref, push_commit))
        if tagref is not None:
            refspecs.append(f"{tagref.object}:{tagref.path}")
            bundle_refs.append((tagref.path, tagref.object))
        if notes_ref is not None:
            refspecs.append(notes_ref)
            notes_commit_name, notes_ref_name = notes_ref.split(":", 1)
            notes_commit = repo.commit(notes_commit_name)
            bundle_excludes.extend(notes_commit.parents)
            bundle_refs.append((notes_ref_name, notes_commit))

        commit_meta = {
            "remote": target_remote,
            "version-bumped": version_bumped,
            "ref": target_ref,
            "refspecs": refspecs,
            "target-commit": str(target_commit) if target_commit is not None else None,
            "source-commit": str(source_commit) if source_commit is not None else None,
            "autosquashed-commit": str(autosquashed_commit) if autosquashed_commit is not None else None,
        }

        store_commit_meta(repo, commit_meta, commit=submit_commit, old_commit=target_commit)

        if bundle is not None and bundle_refs:
            bundle_names = []

            bundle_ref_dir = Path(repo.git_dir) / "refs" / "bundle"
            for ref, obj in bundle_refs:
                if not ref.startswith("refs/") and not ref.startswith("heads/") and not ref.startswith("tags/"):
                    ref = f"heads/{ref}"
                elif ref.startswith("refs/"):
                    ref = ref[len("refs/") :]
                ref_path = bundle_ref_dir / ref
                ref_path.parent.mkdir(parents=True, exist_ok=True)
                ref_path.write_text(str(obj), encoding="UTF-8")
                bundle_names.append(f"refs/bundle/{ref}")

            if autosquashed_commit is not None:
                autosquashed_ref = Path(repo.git_dir) / "refs" / "hopic" / "bundle" / "autosquashed"
                autosquashed_ref.parent.mkdir(parents=True, exist_ok=True)
                autosquashed_ref.write_text(str(autosquashed_commit), encoding="UTF-8")
                bundle_names.append("refs/hopic/bundle/autosquashed")

            commit_meta_data = json.dumps(commit_meta).encode("UTF-8")
            commit_meta_blob = git.Blob(
                repo=repo,
                binsha=repo.odb.store(gitdb.IStream(git.Blob.type, len(commit_meta_data), BytesIO(commit_meta_data))).binsha,
                mode=0o100644,
                path="hopic-meta",
            )

            meta_index = repo.index.new(repo)
            meta_index.add([commit_meta_blob], write=False)

            commit_params["message"] = f"Hopic meta data for {submit_commit}"
            commit_params["parent_commits"] = ()
            # Prevent advancing HEAD
            commit_params["head"] = False

            meta_commit = meta_index.commit(
                **commit_params,
            )
            meta_ref = Path(repo.git_dir) / "refs" / "hopic" / "bundle" / "meta"
            meta_ref.parent.mkdir(parents=True, exist_ok=True)
            meta_ref.write_text(str(meta_commit), encoding="UTF-8")
            bundle_names.append("refs/hopic/bundle/meta")

            repo.git.bundle("create", bundle, *bundle_excludes, *bundle_names)

        if ctx.obj.version is not None:
            click.echo(ctx.obj.version)


@overload
def is_version_bump_enabled(bump_config: Mapping, ctx: Optional[click.Context] = None, *, is_publish_from_branch_allowed: bool) -> bool: ...


@overload
def is_version_bump_enabled(bump_config: Mapping, ctx: click.Context, *, is_publish_from_branch_allowed: Optional[bool] = None) -> bool: ...


def is_version_bump_enabled(bump_config: Mapping, ctx: Optional[click.Context] = None, *, is_publish_from_branch_allowed: Optional[bool] = None) -> bool:
    """
    Check if current branch with version configuration is allowed to bump version
    To avoid multiple is_publish_branch calls this can optionally be passed to this function
    """

    assert ctx is not None or is_publish_from_branch_allowed is not None
    if is_publish_from_branch_allowed is None:
        is_publish_from_branch_allowed = is_publish_branch(ctx)
    if not is_publish_from_branch_allowed:
        log.debug("not bumping the version because publishing from the current branch is disabled")
        return False
    if bump_config["policy"] == "disabled":
        log.debug("not bumping the version because the policy is configured to be disabled")
        return False
    if not bump_config["on-every-change"]:
        log.debug("not bumping the version because Hopic is configured not to do so on every change")
        return False
    return True


def get_current_version(ctx: click.Context) -> Version:
    version_info = ctx.obj.config["version"]
    if ctx.obj.version is None:
        if "file" in version_info:
            raise VersioningError(f"Failed to read the current version (from {version_info['file']}) while attempting to bump the version")

        msg = "Failed to determine the current version while attempting to bump the version"
        log.error(msg)
        # TODO: PIPE-309: provide an initial starting point instead
        log.info("If this is a new repository you may wish to create a 0.0.0 tag for Hopic to start bumping from")
        raise VersioningError(msg)

    return ctx.obj.version


def parse_commit_range(
    repo: git.Repo,
    from_commit: Union[None, git.objects.commit.Commit, str],
    to_commit: Union[None, git.objects.commit.Commit, str],
    bump_config: Mapping,
) -> Iterable[CommitMessage]:
    if from_commit is None or to_commit is None:
        return

    for commit in git.Commit.list_items(
        repo,
        f"{from_commit}..{to_commit}",
        first_parent=bump_config.get("first-parent", True),
        no_merges=bump_config.get("no-merges", True),
    ):
        yield parse_commit_message(commit, policy=bump_config["policy"], strict=bump_config.get("strict", False))


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
        assert hasattr(ctx.obj, "pip_constraints")
        install_extensions_and_parse_config(ctx.obj.pip_constraints)

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

        if is_version_bump_enabled(bump, ctx) and strict:
            source_commits = parse_commit_range(repo, repo.head.commit, source_commit, bump)
            base_version = get_current_version(ctx)
            new_version = base_version.next_version_for_commits(source_commits)
            merge_commit_next_version = base_version.next_version_for_commits([merge_commit])
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

    def change_applicator(repo, author, committer):
        assert hasattr(ctx.obj, "pip_constraints")
        install_extensions_and_parse_config(ctx.obj.pip_constraints)

        modality_cmds = ctx.obj.config["modality-source-preparation"][modality]

        has_changed_files = any("changed-files" in cmd for cmd in modality_cmds)

        (commit_message,) = (
            cmd.get("commit-message", cmd.get("commit-message-cmd")) for cmd in modality_cmds if "commit-message" in cmd or "commit-message-cmd" in cmd
        )

        if not has_changed_files:
            # Force clean builds when we don't know how to discover changed files
            repo.git.clean(x=True, d=True, force=True)

        volume_vars = ctx.obj.volume_vars.copy()
        volume_vars.setdefault('HOME', os.path.expanduser('~'))
        vars_from_env = {key: value for key, value in os.environ.items() if key in ctx.obj.config["pass-through-environment-vars"]}
        vars_from_env.update(volume_vars)
        volume_vars = vars_from_env

        # Set submit_commit to None to indicate that we haven't got a submittable commit (yet).
        hopic_git_info = HopicGitInfo.from_repo(repo)._replace(submit_commit=None)
        (*_,) = build.build_variant(variant=modality, cmds=modality_cmds, hopic_git_info=hopic_git_info, exec_stdout=sys.__stderr__, cwd="${CFGDIR}")

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
            log.info("No changes introduced by '%s'", modality)
            return None

        if isinstance(commit_message, str):
            commit_message = expand_vars(volume_vars, commit_message)
        else:
            assert isinstance(commit_message, Mapping)
            (commit_message,) = build.build_variant(
                variant=modality, cmds=[commit_message], hopic_git_info=hopic_git_info, exec_stdout=subprocess.PIPE, cwd="${CFGDIR}"
            )

        if commit_message[-1:] != "\n":
            commit_message += "\n"

        # Prevent splitting footers with empty lines in between, because 'git interpret-trailers' doesn't like it.
        parsed_msg = parse_commit_message(commit_message)
        if not parsed_msg.footers:
            commit_message += "\n"

        commit_message += f"Merged-by: Hopic {utils.get_package_version(PACKAGE)}\n"

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

                    Bumped-by: Hopic {utils.get_package_version(PACKAGE)}
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
# fmt: off
@click.option('--phase'      , '-p' , metavar='<phase>'  , multiple=True, help='''Build phase''', autocompletion=autocomplete.phase_from_config)
@click.option('--variant'    , '-v' , metavar='<variant>', multiple=True, help='''Configuration variant''', autocompletion=autocomplete.variant_from_config)
@click.option("--modality"   , "-m" , metavar="<modality>",               help='''Display only meta-data for the specified modality.''')
@click.option('--post-submit'       , is_flag=True       ,                help='''Display only post-submit meta-data.''')
# fmt: on
@click.pass_context
def getinfo(
    ctx: click.Context,
    phase: Sequence[str],
    variant: Sequence[str],
    modality: Optional[str],
    post_submit: Optional[str],
):
    """
    Display meta-data associated with each (or the specified) variant in each (or the specified) phase.

    The output is JSON encoded.

    If a phase or variant filter is specified the name of that will not be present in the output.
    Otherwise this is a nested dictionary of phases and variants.
    """
    info: Dict[str, Any] = OrderedDict()

    def append_meta_from_cmd(info, cmd: typing.Mapping[str, Any], permitted_fields: Set):
        assert isinstance(cmd, Mapping)

        info = info.copy()

        for key, val in cmd.items():
            if key == "timeout" and "sh" not in cmd:
                # Return _global_ timeout (not "sh"-specific) only
                pass
            elif key == "finally":
                for final_cmd in cmd["finally"]:
                    info.update(append_meta_from_cmd(info, final_cmd, permitted_fields))
                continue
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

    if modality and post_submit:
        log.error("--modality and --post-submit are mutually exclusive options")
        ctx.exit(1)
    if phase or variant:
        if modality:
            log.error("--modality is mutually exclusive with --phase and --variant")
            ctx.exit(1)
        if post_submit:
            log.error("--post-submit is mutually exclusive with --phase and --variant")
            ctx.exit(1)

    permitted_fields: AbstractSet[str]
    if modality:
        permitted_fields = {
            "with-credentials",
        }
        for cmd in ctx.obj.config["modality-source-preparation"].get(modality, ()):
            info.update(append_meta_from_cmd(info, cmd, permitted_fields))
            if "commit-message-cmd" in cmd:
                info.update(append_meta_from_cmd(info, cmd["commit-message-cmd"], permitted_fields))
    elif post_submit:
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
@click.argument("bundle", type=click.Path(file_okay=True, dir_okay=False, readable=True))
@click.pass_context
def unbundle(ctx, *, bundle: PathLike):
    """
    Unbundle the specified git bundle and setup all included refs for pushing.
    """

    with git.Repo(ctx.obj.workspace) as repo:
        target_commit = repo.head.commit

        with repo.config_reader() as cfg:
            section = f"hopic.{target_commit}"
            target_ref = cfg.get(section, "ref", fallback=None)
            target_remote = cfg.get(section, "remote", fallback=None)
            code_clean = cfg.getboolean("hopic.code", "cfg-clean", fallback=False)
            code_fresh = cfg.getboolean("hopic.code", "fresh", fallback=code_clean)

        submit_commit: Optional[git.Commit] = None
        commit_meta: Dict[str, Any] = {}
        bundle_path = "refs/bundle/"
        head_path = "heads/"
        unbundle_proc = repo.git.bundle("unbundle", bundle, as_process=True)
        for headline in unbundle_proc.stdout:
            obj, ref = headline.decode("UTF-8").rstrip("\n").split(" ", 1)
            if ref == "refs/hopic/bundle/meta":
                meta_commit = repo.commit(obj)

                m = re.match(r"^Hopic meta\s*data for \b([0-9a-fA-F]+)\b", meta_commit.message)
                if not m:
                    continue
                submit_commit = repo.commit(m.group(1))

                if "hopic-meta" not in meta_commit.tree:
                    continue
                commit_meta = json.load(meta_commit.tree["hopic-meta"].data_stream)
                store_commit_meta(repo, commit_meta, commit=submit_commit, old_commit=target_commit)
            if not ref.startswith(bundle_path):
                continue
            ref = ref[len(bundle_path) :]
            if not ref.startswith(head_path):
                ref_path = Path(repo.git_dir) / "refs" / ref
                ref_path.parent.mkdir(parents=True, exist_ok=True)
                ref_path.write_text(str(obj), encoding="UTF-8")
        try:
            unbundle_proc.terminate()
        except OSError:
            pass

    if not submit_commit or not commit_meta:
        log.error("Couldn't find Hopic meta data inside given bundle")
        ctx.exit(1)

    workspace = ctx.obj.workspace
    # Check out specified repository
    checkout_tree(
        ctx.obj.workspace,
        remote=None,
        ref=commit_meta["ref"],
        commit=submit_commit,
        clean=code_clean,
    )

    try:
        ctx.obj.config = read_config(determine_config_file_name(ctx), ctx.obj.volume_vars)
        with git.Repo(workspace) as repo:
            if code_clean:
                clean_repo(repo, ctx.obj.config["clean"])
            if code_fresh:
                # Only restore mtimes when doing a clean build or working on a fresh clone. This prevents problems with timestamp-based build sytems.
                # I.e. make and ninja and probably half the world.
                restore_mtime_from_git(repo)
        git_cfg = ctx.obj.config["scm"]["git"]
    except (click.BadParameter, KeyError, TypeError, OSError, IOError, YAMLError):
        return
    finally:
        log.info("%s", repo.git.show(submit_commit, format="fuller", stat=True, notes="*"))

    checkout_worktrees(workspace, git_cfg["worktrees"])

    if "remote" not in git_cfg and "ref" not in git_cfg:
        return

    code_dir = find_code_dir(workspace)

    # Check out configured repository and mark it as the code directory of this one
    with git.Repo(workspace) as repo, repo.config_writer() as cfg:
        cfg.remove_section("hopic.code")
        cfg.set_value("hopic.code", "dir", str(code_dir))
        cfg.set_value("hopic.code", "cfg-remote", target_remote)
        cfg.set_value("hopic.code", "cfg-ref", target_ref)
        cfg.set_value("hopic.code", "cfg-clean", str(code_clean))
        if code_fresh:
            cfg.set_value("hopic.code", "fresh", str(code_fresh))

    checkout_tree(
        code_dir,
        git_cfg.get("remote", target_remote),
        git_cfg.get("ref", target_ref),
        clean=code_clean,
        clean_config=ctx.obj.config["clean"],
    )


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
        hopic_git_info = HopicGitInfo.from_repo(repo)
        if target_remote is None:
            target_remote = hopic_git_info.submit_remote

        repo.git.push(target_remote, hopic_git_info.refspecs, atomic=True)

        with repo.config_writer() as cfg:
            cfg.remove_section(f"hopic.{repo.head.commit}")

    for phase in ctx.obj.config['post-submit'].values():
        (*_,) = build.build_variant(variant="post-submit", cmds=phase, hopic_git_info=hopic_git_info)


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
