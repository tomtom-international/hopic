# Copyright (c) 2018 - 2019 TomTom N.V. (https://tomtom.com)
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
import click_log

from . import binary_normalize
from .config_reader import read as read_config
from .config_reader import expand_vars
from .execution import echo_cmd
from .versioning import *
try:
    from collections.abc import (
            Mapping,
            MutableSequence,
        )
except ImportError:
    from collections import (
            Mapping,
            MutableSequence,
        )
from copy import copy
from datetime import (datetime, timedelta)
from dateutil.parser import parse as date_parse
from dateutil.tz import (tzoffset, tzlocal, tzutc)
import git
import gitdb
from io import (
        BytesIO,
        StringIO,
    )
from itertools import chain
import json
import logging
import os
import re
import shlex
from six import (
        string_types,
        text_type,
    )
import subprocess
import sys

try:
    from shlex import quote as shquote
except ImportError:
    from pipes import quote as shquote

try:
    from ConfigParser import (
            NoOptionError,
            NoSectionError,
        )
except ImportError:
    # PY3
    from configparser import (
            NoOptionError,
            NoSectionError,
        )

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


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

def is_publish_branch(ctx):
    """
    Check if the branch name is allowed to publish, if publish-from-branch is not defined in the config file, all the branches should be allowed to publish
    """

    try:
        with git.Repo(ctx.obj.workspace) as repo:
            target_commit = repo.head.commit
            with repo.config_reader() as cfg:
                section = 'ci-driver.{target_commit}'.format(**locals())
                target_ref = cfg.get_value(section, 'ref')
    except (NoOptionError, NoSectionError):
        return False

    try:
        publish_from_branch = ctx.obj.config['publish-from-branch']
    except KeyError:
        return True

    publish_branch_pattern = re.compile('(?:{})$'.format(publish_from_branch))
    return publish_branch_pattern.match(target_ref)


def determine_source_date(workspace):
    """Determine the date of most recent change to the sources in the given workspace"""
    try:
        with git.Repo(workspace) as repo:
            source_date = repo.head.commit.committed_datetime

            changes = repo.index.diff(None)
            if changes:
                # Ensure that, no matter what happens, the source date is more recent than the check-in date
                source_date = source_date + timedelta(seconds=1)

            # Ensure a more accurate source date is used if there have been any changes to the tracked sources
            for diff in changes:
                if diff.deleted_file:
                    continue

                try:
                    st = os.lstat(os.path.join(repo.working_dir, diff.b_path))
                except OSError:
                    pass
                else:
                    file_date = datetime.utcfromtimestamp(st.st_mtime).replace(tzinfo=tzlocal())
                    source_date = max(source_date, file_date)

            log.debug("Date of last modification to source: %s", source_date)
            return source_date
    except (git.InvalidGitRepositoryError, git.NoSuchPathError):
        return None


def volume_spec_to_docker_param(volume):
    if not os.path.exists(volume['source']):
        os.makedirs(volume['source'])
    param = '{source}:{target}'.format(**volume)
    try:
        param = param + ':' + ('ro' if volume['read-only'] else 'rw')
    except KeyError:
        pass
    return param


class OptionContext(object):
    def __init__(self):
        super(OptionContext, self).__init__()
        self._opts = {}
        self._missing_parameters = {}

    def __getattr__(self, name):
        if name in frozenset({'_opts', '_missing_parameters'}):
            return super(OptionContext, self).__getattr__(name)

        try:
            return self._opts[name]
        except KeyError:
            pass
        try:
            missing_param = self._missing_parameters[name].copy()
        except KeyError:
            raise AttributeError("'{}' object has no attribute '{}'.".format(self.__class__.__name__, name))
        else:
            exception_raiser = missing_param.pop('exception_raiser')
            exception_raiser(**missing_param)

    def __setattr__(self, name, value):
        if name in frozenset({'_opts', '_missing_parameters'}):
            return super(OptionContext, self).__setattr__(name, value)

        self._opts[name] = value

    def __delattr__(self, name):
        del self._opts[name]

    def register_parameter(self, ctx, param, name=None, exception_raiser=None):
        if name is None:
            name = param.human_readable_name

        if exception_raiser is None:
            def exception_raiser(**kwargs):
                raise click.MissingParameter(**kwargs)

        self._missing_parameters[name] = dict(
            ctx=ctx,
            param=param,
            exception_raiser=exception_raiser,
        )

    def register_dependent_attribute(self, name, dependency):
        self._missing_parameters[name] = self._missing_parameters[dependency]


def cli_autocomplete_get_option_from_args(args, option):
    try:
        return args[args.index(option) + 1]
    except Exception:
        for arg in args:
            if arg.startswith(option + '='):
                return arg[len(option + '='):]


def cli_autocomplet_get_config_from_args(args):
    config = os.path.expanduser(
        expand_vars(
            os.environ,
            cli_autocomplete_get_option_from_args(args, '--config'),
        ))
    return read_config(config, {})


def cli_autocomplete_phase_from_config(ctx, args, incomplete):
    try:
        cfg = cli_autocomplet_get_config_from_args(args)
        for phase in cfg['phases']:
            if incomplete in phase:
                yield phase
    except Exception:
        pass


def cli_autocomplete_variant_from_config(ctx, args, incomplete):
    try:
        cfg = cli_autocomplet_get_config_from_args(args)
        phase = cli_autocomplete_get_option_from_args(args, '--phase')

        seen_variants = set()
        for phasename, curphase in cfg['phases'].items():
            if phase is not None and phasename != phase:
                continue
            for variant in curphase:
                if variant in seen_variants:
                    continue
                seen_variants.add(variant)
                yield variant
    except Exception:
        pass


def cli_autocomplete_modality_from_config(ctx, args, incomplete):
    try:
        cfg = cli_autocomplet_get_config_from_args(args)
        for modality in cfg['modality-source-preparation']:
            if incomplete in modality:
                yield modality
    except Exception:
        pass


def cli_autocomplete_click_log_verbosity(ctx, args, incomplete):
    for level in (
            'DEBUG',
            'INFO',
            'WARNING',
            'ERROR',
            'CRITICAL',
        ):
        if incomplete in level:
            yield level


def determine_version(version_info, config_dir, code_dir=None):
    """
    Determines the current version for the given version configuration snippet.
    """

    if 'file' in version_info:
        params = {}
        if 'format' in version_info:
            params['format'] = version_info['format']
        fname = os.path.join(config_dir, version_info['file'])
        if os.path.isfile(fname):
            return read_version(fname, **params)

    if version_info.get('tag', False) and code_dir is not None:
        try:
            with git.Repo(code_dir) as repo:
                describe_out = repo.git.describe(tags=True, long=True, dirty=True, always=True)
        except (git.InvalidGitRepositoryError, git.NoSuchPathError):
            pass
        else:
            params = {}
            if 'format' in version_info:
                params['format'] = version_info['format']
            return parse_git_describe_version(describe_out, dirty_date=determine_source_date(code_dir), **params)


@click.group(context_settings=dict(help_option_names=('-h', '--help')))
@click.option('--color', type=click.Choice(('always', 'auto', 'never')), default='auto', show_default=True)
@click.option('--config', type=click.Path(exists=False, file_okay=True, dir_okay=False, readable=True, resolve_path=True), default=lambda: None, show_default='${WORKSPACE}/hopic-ci-config.yaml or ${WORKSPACE}/cfg.yml')
@click.option('--workspace', type=click.Path(exists=False, file_okay=False, dir_okay=True), default=lambda: None, show_default='current working directory')
@click.option('--whitelisted-var', multiple=True, default=['CT_DEVENV_HOME'], show_default=True)
@click_log.simple_verbosity_option(__package__, autocompletion=cli_autocomplete_click_log_verbosity)
@click_log.simple_verbosity_option('git', '--git-verbosity', autocompletion=cli_autocomplete_click_log_verbosity)
@click.pass_context
def cli(ctx, color, config, workspace, whitelisted_var):
    if color == 'always':
        ctx.color = True
    elif color == 'never':
        ctx.color = False
    else:
        # leave as is: 'auto' is the default for Click
        pass

    click_log.basic_config()

    ctx.obj = OptionContext()
    for param in ctx.command.params:
        ctx.obj.register_parameter(ctx=ctx, param=param)
        if param.human_readable_name == 'workspace' and workspace is not None:
            if ctx.invoked_subcommand != 'checkout-source-tree':
                # Require the workspace directory to exist for anything but the checkout command
                if not os.path.isdir(workspace):
                    raise click.BadParameter(
                        'Directory "{workspace}" does not exist.'.format(**locals()),
                        ctx=ctx, param=param
                    )
        elif param.human_readable_name == 'config' and config is not None:
            # Require the config file to exist everywhere that it's used
            if not os.path.isfile(config):
                def exception_raiser(ctx, param):
                    raise click.BadParameter(
                        'File "{config}" does not exist.'.format(config=config),
                        ctx=ctx, param=param
                    )
                ctx.obj.register_parameter(ctx=ctx, param=param, exception_raiser=exception_raiser)

    if workspace is None:
        # workspace default
        workspace = (os.path.dirname(config) if config is not None else os.getcwd())
    workspace = os.path.join(os.getcwd(), workspace)
    ctx.obj.workspace = workspace

    ctx.obj.volume_vars = {}
    try:
        with git.Repo(workspace) as repo, repo.config_reader() as cfg:
            code_dir = os.path.join(workspace, cfg.get_value('ci-driver.code', 'dir'))
    except (git.InvalidGitRepositoryError, git.NoSuchPathError, NoOptionError, NoSectionError):
        code_dir = workspace

    ctx.obj.code_dir = ctx.obj.volume_vars['WORKSPACE'] = code_dir
    source_date = determine_source_date(code_dir)
    if source_date is not None:
        ctx.obj.source_date = source_date
        ctx.obj.source_date_epoch = int((
            source_date - datetime.utcfromtimestamp(0).replace(tzinfo=tzutc())
        ).total_seconds())
        ctx.obj.volume_vars['SOURCE_DATE_EPOCH'] = str(ctx.obj.source_date_epoch)
    ctx.obj.register_dependent_attribute('code_dir', 'workspace')
    ctx.obj.register_dependent_attribute('source_date', 'workspace')
    ctx.obj.register_dependent_attribute('source_date_epoch', 'workspace')

    for var in whitelisted_var:
        try:
            ctx.obj.volume_vars[var] = os.environ[var]
        except KeyError:
            pass

    if config is None:
        for fname in (
                'hopic-ci-config.yaml',
                'cfg.yml',
            ):
            fname = os.path.join(workspace, fname)
            if os.path.isfile(fname):
                config = fname
                break

    cfg = {}
    if config is not None:
        if not os.path.isabs(config):
            config = os.path.join(os.getcwd(), config)
        ctx.obj.config_file = config
        ctx.obj.volume_vars['CFGDIR'] = ctx.obj.config_dir = os.path.dirname(config)
        # Prevent reading the config file _before_ performing a checkout. This prevents a pre-existing file at the same
        # location from being read as the config file. This may cause problems if that pre-checkout file has syntax
        # errors for example.
        if ctx.invoked_subcommand != 'checkout-source-tree' and os.path.isfile(config):
            cfg = ctx.obj.config = read_config(config, ctx.obj.volume_vars)
    ctx.obj.register_dependent_attribute('config_file', 'config')
    ctx.obj.register_dependent_attribute('config_dir', 'config')

    ctx.obj.version = determine_version(
            cfg.get('version', {}),
            config_dir=(config and ctx.obj.config_dir),
            code_dir=ctx.obj.code_dir,
        )
    if ctx.obj.version is not None:
        log.debug("read version: \x1B[34m%s\x1B[39m", ctx.obj.version)
        ctx.obj.volume_vars['VERSION'] = str(ctx.obj.version)
        # FIXME: make this conversion work even when not using SemVer as versioning policy
        # Convert SemVer to Debian version: '~' for pre-release instead of '-'
        ctx.obj.volume_vars['DEBVERSION'] = ctx.obj.volume_vars['VERSION'].replace('-', '~', 1).replace('.dirty.', '+dirty', 1)


@cli.command()
@click.pass_context
def may_publish(ctx):
    """
    Check if the target branch name is allowed to be published, according to publish-from-branch in the config file.
    """

    ctx.exit(0 if is_publish_branch(ctx) else 1)


def restore_mtime_from_git(repo, files=None):
    if files is None:
        files = set(filter(None, repo.git.ls_files('-z', stdout_as_string=False).split(b'\0')))

    encoding = sys.getfilesystemencoding() or sys.getdefaultencoding()
    workspace = repo.working_tree_dir.encode(encoding)

    symlink_mode = 0o120000

    # Set all files' modification times to their last commit's time
    whatchanged = repo.git.whatchanged(pretty='format:%ct', as_process=True)
    mtime = 0
    for line in whatchanged.stdout:
        if not files:
            break

        line = line.strip()
        if not line:
            continue
        if line.startswith(b':'):
            line = line[1:]

            props, filenames = line.split(b'\t', 1)
            old_mode, new_mode, old_hash, new_hash, operation = props.split(b' ')
            old_mode, new_mode = int(old_mode, 8), int(new_mode, 8)

            filenames = filenames.split(b'\t')
            if len(filenames) == 1:
                filenames.insert(0, None)
            old_filename, new_filename = filenames

            if new_filename in files:
                files.remove(new_filename)
                path = os.path.join(workspace, new_filename)
                if new_mode == symlink_mode:
                    # Only attempt to modify symlinks' timestamps when the current system supports it.
                    # E.g. Python >= 3.3 and Linux kernel >= 2.6.22
                    if os.utime in getattr(os, 'supports_follow_symlinks', set()):
                        os.utime(path, (mtime, mtime), follow_symlinks=False)
                else:
                    os.utime(path, (mtime, mtime))
        else:
            mtime = int(line)
    try:
        whatchanged.terminate()
    except OSError:
        pass


def checkout_tree(tree, remote, ref, clean=False, remote_name='origin'):
    try:
        repo = git.Repo(tree)
    except (git.InvalidGitRepositoryError, git.NoSuchPathError):
        repo = git.Repo.clone_from(remote, tree)

    with repo:
        with repo.config_writer() as cfg:
            cfg.remove_section('ci-driver.code')
            cfg.set_value('color', 'ui', 'always')
            cfg.set_value('ci-driver.code', 'cfg-clean', str(clean))

        tags = repo.tags
        if tags:
            repo.delete_tag(*repo.tags)

        try:
            # Delete, instead of update, existing remotes.
            # This is because of https://github.com/gitpython-developers/GitPython/issues/719
            repo.delete_remote(remote_name)
        except git.GitCommandError:
            pass
        origin = repo.create_remote(remote_name, remote)

        commit = origin.fetch(ref, tags=True)[0].commit
        repo.head.reference = commit
        repo.head.reset(index=True, working_tree=True)
        if clean:
            clean_output = repo.git.clean('-xd', force=True)
            if clean_output:
                log.info('%s', clean_output)

            # Only restore mtimes when doing a clean build. This prevents problems with timestamp-based build sytems.
            # I.e. make and ninja and probably half the world.
            restore_mtime_from_git(repo)

        with repo.config_writer() as cfg:
            section = 'ci-driver.{commit}'.format(**locals())
            cfg.set_value(section, 'ref', ref)
            cfg.set_value(section, 'remote', remote)

    return commit


def to_git_time(date):
    """
    Converts a datetime object to a string with Git's internal time format.
    
    This is necessary because GitPython, wrongly, interprets an ISO-8601 formatted time string as
    UTC time to be converted to the specified timezone.

    Git's internal time format actually is UTC time plus a timezone to be applied for display
    purposes, so converting it to that yields correct behavior.
    """

    utctime = int((
        date - datetime.utcfromtimestamp(0).replace(tzinfo=tzutc())
    ).total_seconds())
    return '{utctime} {date:%z}'.format(**locals())


@cli.command()
@click.option('--target-remote'     , metavar='<url>')
@click.option('--target-ref'        , metavar='<ref>')
@click.option('--clean/--no-clean'  , default=False, help='''Clean workspace of non-tracked files''')
@click.pass_context
def checkout_source_tree(ctx, target_remote, target_ref, clean):
    """
    Checks out a source tree of the specified remote's ref to the workspace.
    """

    workspace = ctx.obj.workspace

    # Check out specified repository
    click.echo(checkout_tree(workspace, target_remote, target_ref, clean))

    try:
        ctx.obj.config = read_config(ctx.obj.config_file, ctx.obj.volume_vars)
        git_cfg = ctx.obj.config['scm']['git']
    except (click.BadParameter, KeyError, TypeError, OSError, IOError):
        return

    if 'worktrees' in git_cfg:
        with git.Repo(workspace) as repo:

            worktrees = git_cfg['worktrees'].items()
            fetch_result = repo.remotes.origin.fetch([ref for subdir, ref in worktrees])

            worktrees = dict((subdir, fetchinfo.ref) for (subdir, refname), fetchinfo in zip(worktrees, fetch_result))
            log.debug("Worktree config: %s", worktrees)

            for subdir, ref in worktrees.items():
                try:
                    os.remove(os.path.join(workspace, subdir, '.git'))
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
    code_dirs = sorted(dir for dir in os.listdir(workspace) if code_dir_re.match(dir))
    for dir in code_dirs:
        try:
            with git.Repo(os.path.join(workspace, dir)):
                pass
        except (git.InvalidGitRepositoryError, git.NoSuchPathError):
            pass
        else:
            code_dir = dir
            break
    else:
        seq = 0
        while True:
            dir = ('code' if seq == 0 else 'code-{seq:03}'.format(seq=seq))
            seq += 1
            if dir not in code_dirs:
                code_dir = dir
                break

    # Check out configured repository and mark it as the code directory of this one
    ctx.obj.code_dir = os.path.join(workspace, code_dir)
    with git.Repo(workspace) as repo, repo.config_writer() as cfg:
        cfg.remove_section('ci-driver.code')
        cfg.set_value('ci-driver.code', 'dir', code_dir)
        cfg.set_value('ci-driver.code', 'cfg-remote', target_remote)
        cfg.set_value('ci-driver.code', 'cfg-ref', target_ref)
        cfg.set_value('ci-driver.code', 'cfg-clean', str(clean))

    checkout_tree(ctx.obj.code_dir, git_cfg.get('remote', target_remote), git_cfg.get('ref', target_ref), clean)


@cli.group()
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
        target_commit = repo.head.commit

        with repo.config_writer() as cfg:
            section = 'ci-driver.{target_commit}'.format(**locals())
            target_ref    = cfg.get_value(section, 'ref')
            target_remote = cfg.get_value(section, 'remote')
            code_clean    = cfg.getboolean('ci-driver.code', 'cfg-clean')

        commit_params = change_applicator(repo)
        if not commit_params:
            return
        source_commit = commit_params.pop('source_commit', None)

        # Re-read config to ensure any changes introduced by 'change_applicator' are taken into account
        try:
            ctx.obj.config = read_config(ctx.obj.config_file, ctx.obj.volume_vars)
        except (click.BadParameter, KeyError, TypeError, OSError, IOError):
            pass

        # Ensure that, when we're dealing with a separated config and code repository, that the code repository is checked out again to the newer version
        if ctx.obj.code_dir != ctx.obj.workspace:
            with repo.config_reader() as cfg:
                try:
                    code_remote = ctx.obj.config['scm']['git']['remote']
                except (KeyError, TypeError):
                    code_remote = cfg.get_value('ci-driver.code', 'cfg-remote')
                try:
                    code_commit = ctx.obj.config['scm']['git']['ref']
                except (KeyError, TypeError):
                    code_commit = cfg.get_value('ci-driver.code', 'cfg-ref')

            checkout_tree(ctx.obj.code_dir, code_remote, code_commit, code_clean)

        try:
            version_info = ctx.obj.config['version']
        except (click.BadParameter, KeyError, TypeError):
            version_info = {}

        # Re-read version to ensure that the version policy in the reloaded configuration is used for it
        ctx.obj.version = determine_version(version_info, ctx.obj.config_dir, ctx.obj.code_dir)
        
        # If the branch is not allowed to publish, skip version bump step
        is_publish_allowed = is_publish_branch(ctx)
        
        if is_publish_allowed and version_info.get('bump', True):
            if ctx.obj.version is None:
                if 'file' in version_info:
                    log.error("Failed to read the current version (from %r) while attempting to bump the version", version_info['file'])
                else:
                    log.error("Failed to determine the current version while attempting to bump the version")
                ctx.exit(1)

            params = {}
            if 'bump' in version_info:
                params['bump'] = version_info['bump']
            ctx.obj.version = ctx.obj.version.next_version(**params)
            log.debug("bumped version to: \x1B[34m%s\x1B[39m", ctx.obj.version)

            if 'file' in version_info:
                replace_version(os.path.join(ctx.obj.config_dir, version_info['file']), ctx.obj.version)
                repo.index.add([version_info['file']])
        else:
            log.info("Skip version bumping due to the configuration or the target branch is not allowed to publish")

        author = git.Actor.author(repo.config_reader())
        if author_name is not None:
            author.name = author_name
        if author_email is not None:
            author.email = author_email
        commit_params.setdefault('author', author)
        if author_date is not None:
            commit_params['author_date'] = to_git_time(author_date)
        if commit_date is not None:
            commit_params['commit_date'] = to_git_time(commit_date)

        submit_commit = repo.index.commit(**commit_params)
        click.echo(submit_commit)

        # Autosquash the merged commits (if any) to discover how that would look like.
        autosquash_base = None
        if source_commit:
            for commit in git.Commit.list_items(repo, '{target_commit}..{source_commit}'.format(**locals()), first_parent=True, no_merges=True):
                subject = commit.message.splitlines()[0]
                if subject.startswith('fixup!') or subject.startswith('squash!'):
                    autosquash_base = repo.merge_base(target_commit, submit_commit)
                    break
        autosquashed_commit = None
        if autosquash_base:
            repo.head.reference = source_commit
            repo.head.reset(index=True, working_tree=True)
            try:
                try:
                    env = {'GIT_EDITOR': 'true'}
                    if 'commit_date' in commit_params:
                        env['GIT_COMMITTER_DATE'] = commit_params['commit_date']
                    repo.git.rebase(autosquash_base, interactive=True, autosquash=True, env=env, kill_after_timeout=5)
                except git.GitCommandError:
                    pass
                else:
                    autosquashed_commit = repo.head.commit
            finally:
                repo.head.reference = submit_commit
                repo.head.reset(index=True, working_tree=True)

        if code_clean:
            restore_mtime_from_git(repo)

        # Tagging after bumping the version
        tagname = None
        version_tag = version_info.get('tag', False)
        if ctx.obj.version is not None and not ctx.obj.version.prerelease and version_tag and is_publish_allowed:
            if version_tag and not isinstance(version_tag, string_types):
                version_tag = ctx.obj.version.default_tag_name
            tagname = version_tag.format(
                    version        = ctx.obj.version,
                    build_sep      = ('+' if getattr(ctx.obj.version, 'build', None) else ''),
                )
            repo.create_tag(tagname, submit_commit, force=True)

        log.info('%s', repo.git.show(submit_commit, format='fuller', stat=True))

        push_commit = submit_commit
        if ctx.obj.version is not None and 'file' in version_info and 'bump' in version_info.get('after-submit', {}) and is_publish_allowed:
            params = {'bump': version_info['after-submit']['bump']}
            try:
                params['prerelease_seed'] = version_info['after-submit']['prerelease-seed']
            except KeyError:
                pass
            after_submit_version = ctx.obj.version.next_version(**params)
            log.debug("bumped post-submit version to: %s", click.style(str(after_submit_version), fg='blue'))

            new_version_file = StringIO()
            replace_version(os.path.join(ctx.obj.config_dir, version_info['file']), after_submit_version, outfile=new_version_file)
            new_version_file = new_version_file.getvalue().encode(sys.getdefaultencoding())

            old_version_blob = submit_commit.tree[version_info['file']]
            new_version_blob = git.Blob(
                    repo=repo,
                    binsha=repo.odb.store(gitdb.IStream(git.Blob.type, len(new_version_file), BytesIO(new_version_file))).binsha,
                    mode=old_version_blob.mode,
                    path=old_version_blob.path,
                )
            new_index = repo.index.from_tree(repo, submit_commit)
            new_index.add([new_version_blob], write=False)

            del commit_params['author']
            if 'author_date' in commit_params and 'commit_date' in commit_params:
                commit_params['author_date'] = commit_params['commit_date']
            commit_params['message'] = '[ Release build ] new version commit: {after_submit_version}\n'.format(**locals())
            commit_params['parent_commits'] = (submit_commit,)
            # Prevent advancing HEAD
            commit_params['head'] = False

            push_commit = new_index.commit(**commit_params)
            log.info('%s', repo.git.show(push_commit, format='fuller', stat=True))

        with repo.config_writer() as cfg:
            cfg.remove_section('ci-driver.{target_commit}'.format(**locals()))
            section = 'ci-driver.{submit_commit}'.format(**locals())
            cfg.set_value(section, 'remote', target_remote)
            cfg.set_value(section, 'ref', target_ref)
            refspecs = ['{push_commit}:{target_ref}'.format(**locals())]
            if tagname is not None:
                refspecs.append('refs/tags/{tagname}:refs/tags/{tagname}'.format(**locals()))
            cfg.set_value(section, 'refspecs', ' '.join(shquote(refspec) for refspec in refspecs))
            if source_commit:
                cfg.set_value(section, 'target-commit', text_type(target_commit))
                cfg.set_value(section, 'source-commit', text_type(source_commit))
            if autosquashed_commit:
                cfg.set_value(section, 'autosquashed-commit', text_type(autosquashed_commit))
        if ctx.obj.version is not None:
            click.echo(ctx.obj.version)


@prepare_source_tree.command()
# git
@click.option('--source-remote' , metavar='<url>', help='<source> remote to merge into <target>')
@click.option('--source-ref'    , metavar='<ref>', help='ref of <source> remote to merge into <target>')
@click.option('--change-request', metavar='<identifier>'           , help='Identifier of change-request to use in merge commit message')
@click.option('--title'         , metavar='<title>'                , help='''Change request title to incorporate in merge commit's subject line''')
@click.option('--description'   , metavar='<description>'          , help='''Change request description to incorporate in merge commit message's body''')
@click.option('--approved-by'   , metavar='<approver>'             , help='''Name of approving reviewer (can be provided multiple times).''', multiple=True)
def merge_change_request(
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

    def change_applicator(repo):
        try:
            source = repo.remotes.source
        except AttributeError:
            source = repo.create_remote('source', source_remote)
        else:
            source.set_url(source_remote)
        source_commit = source.fetch(source_ref)[0].commit

        repo.git.merge(source_commit, no_ff=True, no_commit=True)

        msg = u"Merge #{}".format(change_request)
        if title is not None:
            msg = u"{msg}: {title}\n".format(msg=msg, title=title)
        if description is not None:
            msg = u"{msg}\n{description}\n".format(msg=msg, description=description)
        if approved_by:
            msg += u'\n' + u'\n'.join(u'Acked-by: {approval}'.format(**locals()) for approval in approved_by) + u'\n'
        return {
                'message': msg,
                'parent_commits': (
                    repo.head.commit,
                    source_commit,
                ),
                'source_commit': source_commit,
            }
    return change_applicator


_env_var_re = re.compile(r'^(?P<var>[A-Za-z_][0-9A-Za-z_]*)=(?P<val>.*)$')
@prepare_source_tree.command()
@click.argument('modality', autocompletion=cli_autocomplete_modality_from_config)
@click.pass_context
def apply_modality_change(
        ctx,
        modality,
    ):
    """
    Applies the changes specific to the specified modality.
    """

    modality_cmds = ctx.obj.config.get('modality-source-preparation', {}).get(modality, ())

    def change_applicator(repo):
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

        for cmd in modality_cmds:
            if isinstance(cmd, string_types):
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
                echo_cmd(subprocess.check_call, args, cwd=repo.working_dir, env=env, stdout=sys.stderr)

            if 'changed-files' in cmd:
                changed_files = cmd["changed-files"]
                if isinstance(changed_files, string_types):
                    changed_files = [changed_files]
                changed_files = [expand_vars(volume_vars, f) for f in changed_files]
                repo.index.add(changed_files)

        if not has_changed_files:
            # 'git add --all' equivalent (excluding the code_dir)
            add_files = set(repo.untracked_files)
            remove_files = set()
            with repo.config_reader() as cfg:
                try:
                    code_dir = cfg.get_value('ci-driver.code', 'dir')
                except (NoOptionError, NoSectionError):
                    pass
                else:
                    if code_dir in add_files:
                        add_files.remove(code_dir)
                    if (code_dir + '/') in add_files:
                        add_files.remove(code_dir + '/')

            for diff in repo.index.diff(None):
                add_files.add(diff.b_path)
                remove_files.add(diff.a_path)
            if remove_files:
                repo.index.remove(remove_files)
            if add_files:
                repo.index.add(add_files)

        if not repo.index.diff(repo.head.commit):
            log.info("No changes introduced by '%s'", commit_message)
            return None
        if not commit_message.endswith(u'\n'):
            commit_message += u'\n'
        return {'message': commit_message}
    return change_applicator


@cli.command()
@click.pass_context
def phases(ctx):
    """
    Enumerate all available phases.
    """

    for phase in ctx.obj.config['phases']:
        click.echo(phase)


@cli.command()
@click.option('--phase'             , metavar='<phase>'  , help='''Build phase to show variants for''', autocompletion=cli_autocomplete_phase_from_config)
@click.pass_context
def variants(ctx, phase):
    """
    Enumerates all available variants. Optionally this can be limited to all variants within a single phase.
    """

    variants = []
    for phasename, curphase in ctx.obj.config['phases'].items():
        if phase is not None and phasename != phase:
            continue
        for variant in curphase:
            # Only add when not a duplicate, but preserve order from config file
            if variant not in variants:
                variants.append(variant)
    for variant in variants:
        click.echo(variant)


@cli.command()
@click.option('--phase'             , metavar='<phase>'  , required=True, help='''Build phase''', autocompletion=cli_autocomplete_phase_from_config)
@click.option('--variant'           , metavar='<variant>', required=True, help='''Configuration variant''', autocompletion=cli_autocomplete_variant_from_config)
@click.pass_context
def getinfo(ctx, phase, variant):
    """
    Display meta-data associated with the specified variant in the given phase as JSON.
    """

    info = {}
    for var in ctx.obj.config['phases'][phase][variant]:
        if isinstance(var, string_types):
            continue
        for key, val in var.items():
            try:
                val = expand_vars(ctx.obj.volume_vars, val)
            except KeyError:
                pass
            else:
                if key in info and isinstance(info[key], Mapping):
                    info[key].update(val)
                elif key in info and isinstance(info[key], MutableSequence):
                    info[key].extend(val)
                else:
                    info[key] = val
    click.echo(json.dumps(info, indent=4, separators=(',', ': ')))


@cli.command()
@click.option('--phase'             , metavar='<phase>'  , help='''Build phase to execute''', autocompletion=cli_autocomplete_phase_from_config)
@click.option('--variant'           , metavar='<variant>', help='''Configuration variant to build''', autocompletion=cli_autocomplete_variant_from_config)
@click.pass_context
def build(ctx, phase, variant):
    """
    Build for the specified commit.

    This defaults to building all variants for all phases.
    It's possible to limit building to either all variants for a single phase, all phases for a single variant or a
    single variant for a single phase.
    """

    cfg = ctx.obj.config

    refspecs = []
    source_commits = []
    autosquashed_commits = []
    try:
        with git.Repo(ctx.obj.workspace) as repo:
            submit_commit = repo.head.commit
            section = 'ci-driver.{submit_commit}'.format(**locals())
            with repo.config_reader() as git_cfg:
                if git_cfg.has_option(section, 'refspecs'):
                    refspecs = list(shlex.split(git_cfg.get_value(section, 'refspecs')))
                if git_cfg.has_option(section, 'target-commit') and git_cfg.has_option(section, 'source-commit'):
                    target_commit = repo.commit(git_cfg.get_value(section, 'target-commit'))
                    source_commit = repo.commit(git_cfg.get_value(section, 'source-commit'))
                    source_commits = git.Commit.list_items(repo, '{target_commit}..{source_commit}'.format(**locals()), first_parent=True, no_merges=True)
                    autosquashed_commits = source_commits
                    log.debug('Building for source commits: %s', source_commits)
                if git_cfg.has_option(section, 'autosquashed-commit'):
                    autosquashed_commit = repo.commit(git_cfg.get_value(section, 'autosquashed-commit'))
                    autosquashed_commits = git.Commit.list_items(repo, '{target_commit}..{autosquashed_commit}'.format(**locals()), first_parent=True, no_merges=True)
    except NoSectionError:
        pass
    has_change = bool(refspecs)

    worktree_commits = {}
    for phasename, curphase in cfg['phases'].items():
        if phase is not None and phasename != phase:
            continue
        for curvariant, cmds in curphase.items():
            if variant is not None and curvariant != variant:
                continue

            image = cfg.get('image', None)
            if image is not None and not isinstance(image, string_types):
                try:
                    image = image[curvariant]
                except KeyError:
                    image = image.get('default', None)

            volume_vars = ctx.obj.volume_vars
            # Give commands executing inside a container image a different view than outside
            if image is not None:
                volume_vars = volume_vars.copy()
                volume_vars['WORKSPACE'] = '/code'

            artifacts = []
            
            # If the branch is not allowed to publish, skip the publish phase. If run_on_change is set to 'always', phase will be run anyway regardless of this condition
            # For build phase, run_on_change is set to 'always' by default, so build will always happen
            is_publish_allowed = is_publish_branch(ctx)
            for cmd in cmds:
                worktrees = {}
                foreach = None
                if not isinstance(cmd, string_types):
                    try:
                        run_on_change = cmd['run-on-change']
                    except (KeyError, TypeError):
                        pass
                    else:
                        if run_on_change == 'always':
                            pass
                        elif run_on_change == 'only' and not (has_change and is_publish_allowed):
                            break
                    try:
                        desc = cmd['description']
                    except (KeyError, TypeError):
                        pass
                    else:
                        log.info('Performing: %s', click.style(desc, fg='cyan'))
                    for artifact_key in (
                            'archive',
                            'fingerprint',
                        ):
                        try:
                            artifacts.extend(expand_vars(volume_vars, (
                                artifact['pattern'] for artifact in cmd[artifact_key]['artifacts'] if 'pattern' in artifact)))
                        except (KeyError, TypeError):
                            pass

                    try:
                        worktrees = cmd['worktrees']

                        # Force clean builds when we don't know how to discover changed files
                        for subdir, worktree in worktrees.items():
                            if 'changed-files' not in worktree:
                                with git.Repo(os.path.join(ctx.obj.workspace, subdir)) as repo:
                                    clean_output = repo.git.clean('-xd', subdir, force=True)
                                    if clean_output:
                                        log.info('%s', clean_output)
                    except KeyError:
                        pass

                    try:
                        foreach = cmd['foreach']
                    except KeyError:
                        pass

                    try:
                        cmd = cmd['sh']
                    except (KeyError, TypeError):
                        continue

                cmd = shlex.split(cmd)
                env = (dict(
                    HOME            = '/home/sandbox',
                    _JAVA_OPTIONS   = '-Duser.home=/home/sandbox',
                ) if image is not None else {})
                for varname in (
                        'SOURCE_DATE_EPOCH',
                        'VERSION',
                        'DEBVERSION',
                    ):
                    if varname in ctx.obj.volume_vars:
                        env[varname] = ctx.obj.volume_vars[varname]

                foreach_items = (None,)
                if foreach == 'SOURCE_COMMIT':
                    foreach_items = source_commits
                elif foreach == 'AUTOSQUASHED_COMMIT':
                    foreach_items = autosquashed_commits

                for foreach_item in foreach_items:
                    cfg_vars = volume_vars.copy()
                    if foreach in (
                            'SOURCE_COMMIT',
                            'AUTOSQUASHED_COMMIT',
                        ):
                        cfg_vars[foreach] = text_type(foreach_item)

                    # Strip off prefixed environment variables from this command-line and apply them
                    final_cmd = copy(cmd)
                    while final_cmd:
                        m = _env_var_re.match(final_cmd[0])
                        if not m:
                            break
                        env[m.group('var')] = expand_vars(cfg_vars, m.group('val'))
                        final_cmd.pop(0)
                    final_cmd = [expand_vars(cfg_vars, arg) for arg in final_cmd]

                    # Handle execution inside docker
                    if image is not None:
                        uid, gid = os.getuid(), os.getgid()
                        docker_run = ['docker', 'run',
                                      '--rm',
                                      '--net=host',
                                      '--tty',
                                      '--tmpfs', '{}:uid={},gid={}'.format(env['HOME'], uid, gid),
                                      '-u', '{}:{}'.format(uid, gid),
                                      '-v', '/etc/passwd:/etc/passwd:ro',
                                      '-v', '/etc/group:/etc/group:ro',
                                      '-w', '/code',
                                      ] + list(chain(*[
                                          ['-e', '{}={}'.format(k, v)] for k, v in env.items()
                                      ]))
                        for volume in cfg['volumes']:
                            docker_run += ['-v', volume_spec_to_docker_param(volume)]
                        docker_run.append(image)
                        final_cmd = docker_run + final_cmd
                    new_env = os.environ.copy()
                    if image is None:
                        new_env.update(env)
                    try:
                        echo_cmd(subprocess.check_call, final_cmd, env=new_env, cwd=ctx.obj.code_dir)
                    except subprocess.CalledProcessError as e:
                        log.exception("Command fatally terminated with exit code %d", e.returncode)
                        sys.exit(e.returncode)

                for subdir, worktree in worktrees.items():
                    with git.Repo(os.path.join(ctx.obj.workspace, subdir)) as repo:
                        worktree_commits.setdefault(subdir, [
                            text_type(repo.head.commit),
                            text_type(repo.head.commit),
                        ])

                        if 'changed-files' in worktree:
                            changed_files = worktree["changed-files"]
                            if isinstance(changed_files, string_types):
                                changed_files = [changed_files]
                            changed_files = [expand_vars(volume_vars, f) for f in changed_files]
                            repo.index.add(changed_files)
                        else:
                            # 'git add --all' equivalent (excluding the code_dir)
                            add_files = set(repo.untracked_files)
                            remove_files = set()
                            for diff in repo.index.diff(None):
                                add_files.add(diff.b_path)
                                remove_files.add(diff.a_path)
                            repo.index.remove(remove_files)
                            repo.index.add(add_files)

                        commit_message = expand_vars(volume_vars, worktree['commit-message'])
                        if not commit_message.endswith(u'\n'):
                            commit_message += u'\n'
                        with git.Repo(ctx.obj.workspace) as parent_repo:
                            parent = parent_repo.head.commit
                            submit_commit = repo.index.commit(
                                    message     = commit_message,
                                    author      = parent.author,
                                    author_date = to_git_time(parent.authored_datetime),
                                )
                        restore_mtime_from_git(repo)
                        worktree_commits[subdir][1] = text_type(submit_commit)
                        log.info('%s', repo.git.show(submit_commit, format='fuller', stat=True))

            if worktrees:
                with git.Repo(ctx.obj.workspace) as repo, repo.config_writer() as cfg:
                    bundle_commits = []
                    for subdir, (base_commit, submit_commit) in worktree_commits.items():
                        worktree_ref = ctx.obj.config['scm']['git']['worktrees'][subdir]
                        if worktree_ref in repo.heads:
                            repo.heads[worktree_ref].set_commit(submit_commit, logmsg='Prepare for git-bundle')
                        else:
                            repo.create_head(worktree_ref, submit_commit)
                        bundle_commits.append('{base_commit}..{worktree_ref}'.format(**locals()))
                        refspecs.append('{submit_commit}:{worktree_ref}'.format(**locals()))
                    repo.git.bundle('create', os.path.join(ctx.obj.workspace, 'worktree-transfer.bundle'), *bundle_commits)

                    submit_commit = repo.head.commit
                    section = 'ci-driver.{submit_commit}'.format(**locals())
                    cfg.set_value(section, 'refspecs', ' '.join(shquote(refspec) for refspec in refspecs))

            # Post-processing to make these artifacts as reproducible as possible
            for artifact in artifacts:
                binary_normalize.normalize(os.path.join(ctx.obj.code_dir, artifact), source_date_epoch=ctx.obj.source_date_epoch)


@cli.command()
@click.option('--bundle', metavar='<file>', help='Git bundle to use', type=click.Path(file_okay=True, dir_okay=False, readable=True, resolve_path=True))
@click.pass_context
def unbundle_worktrees(ctx, bundle):
    """
    Unbundle a git bundle and fast-forward all the configured worktrees that are included in it.
    """

    with git.Repo(ctx.obj.workspace) as repo:
        submit_commit = repo.head.commit
        section = 'ci-driver.{submit_commit}'.format(**locals())
        with repo.config_reader() as git_cfg:
            try:
                refspecs = list(shlex.split(git_cfg.get_value(section, 'refspecs')))
            except (NoOptionError, NoSectionError):
                refspecs = []

        head_path = 'refs/heads/'
        worktrees = dict((v,k) for k,v in ctx.obj.config['scm']['git']['worktrees'].items())
        for headline in repo.git.bundle('list-heads', bundle).splitlines():
            commit, ref = headline.split(' ', 1)
            if not ref.startswith(head_path):
                continue
            ref = ref[len(head_path):]
            if ref not in worktrees:
                continue

            subdir = worktrees[ref]
            log.debug("Checkout worktree '%s' to '%s' (proposed branch '%s')", subdir, commit, ref)
            checkout_tree(os.path.join(ctx.obj.workspace, subdir), bundle, ref, remote_name='bundle')
            refspecs.append('{commit}:{ref}'.format(**locals()))

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
            cfg.set_value(section, 'refspecs', ' '.join(shquote(refspec) for refspec in refspecs))

@cli.command()
@click.option('--target-remote', metavar='<url>', help='''The remote to push to, if not specified this will default to the checkout remote.''')
@click.pass_context
def submit(ctx, target_remote):
    """
    Submit the changes created by prepare-source-tree to the target remote.
    """

    with git.Repo(ctx.obj.workspace) as repo:
        section = 'ci-driver.{repo.head.commit}'.format(**locals())
        with repo.config_reader() as cfg:
            if target_remote is None:
                target_remote = cfg.get_value(section, 'remote')
            refspecs = shlex.split(cfg.get_value(section, 'refspecs'))

        repo.git.push(target_remote, refspecs, atomic=True)

        with repo.config_writer() as cfg:
            cfg.remove_section(section)


@cli.command()
@click.pass_context
def show_config(ctx):
    """
    Diagnostic helper command to display the configuration after processing.
    """

    click.echo(json.dumps(ctx.obj.config, indent=4, separators=(',', ': ')))


@cli.command()
@click.pass_context
def show_env(ctx):
    """
    Diagnostic helper command to display the execution environment.
    """

    click.echo(json.dumps(ctx.obj.volume_vars, indent=4, separators=(',', ': '), sort_keys=True))
