# Copyright (c) 2018 - 2020 TomTom N.V. (https://tomtom.com)
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
from commisery.commit import parse_commit_message
from .config_reader import (
        JSONEncoder,
        expand_docker_volume_spec,
        expand_vars,
        read as read_config,
    )
from .execution import echo_cmd
from .git_time import restore_mtime_from_git
from .versioning import *
from collections import OrderedDict
from collections.abc import (
        Mapping,
        MutableSequence,
    )
from configparser import (
        NoOptionError,
        NoSectionError,
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
try:
    # Python >= 3.8
    from importlib import metadata
except ImportError:
    import importlib_metadata as metadata
import re
import signal
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class VersioningError(click.ClickException):
    exit_code = 33


class FatalSignal(Exception):
    def __init__(self, signum):
        self.signal = signum


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
                target_ref = cfg.get_value(f"hopic.{target_commit}", 'ref')
    except (NoOptionError, NoSectionError):
        return False

    try:
        publish_from_branch = ctx.obj.config['publish-from-branch']
    except KeyError:
        return True

    publish_branch_pattern = re.compile(f"(?:{publish_from_branch})$")
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


class DockerContainers(object):
    """
    This context manager class manages a set of Docker containers, handling their creation and deletion.
    """
    def __init__(self):
        self.containers = set()

    def __enter__(self):
        return self

    def __exit__(self, ex_type, ex_value, tb):
        if self.containers:
            log.info('Cleaning up Docker containers: %s', ' '.join(self.containers))
            try:
                echo_cmd(subprocess.check_call, ['docker', 'rm', '-v'] + list(self.containers))
            except subprocess.CalledProcessError as e:
                log.error('Could not remove all Docker volumes, command failed with exit code %d', e.returncode)
            self.containers.clear()

    def __iter__(self):
        return iter(self.containers)

    def add(self, volume_image):
        log.info('Creating new Docker container for image %s', volume_image)
        try:
            container_id = echo_cmd(subprocess.check_output, ['docker', 'create', volume_image]).strip()
        except subprocess.CalledProcessError as e:
            log.exception('Command fatally terminated with exit code %d', e.returncode)
            sys.exit(e.returncode)

        # Container ID's consist of 64 hex characters
        if not re.match('^[0-9a-fA-F]{64}$', container_id):
            log.error('Unable to create Docker container for %s', volume_image)
            sys.exit(1)

        self.containers.add(container_id)

class OptionContext(object):
    def __init__(self):
        super().__init__()
        self._opts = {}
        self._missing_parameters = {}

    def __getattr__(self, name):
        if name in frozenset({'_opts', '_missing_parameters'}):
            return super().__getattr__(name)

        try:
            return self._opts[name]
        except KeyError:
            pass
        try:
            missing_param = self._missing_parameters[name].copy()
        except KeyError:
            raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'.")
        else:
            exception_raiser = missing_param.pop('exception_raiser')
            exception_raiser(**missing_param)

    def __setattr__(self, name, value):
        if name in frozenset({'_opts', '_missing_parameters'}):
            return super().__setattr__(name, value)

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


def determine_config_file_name(ctx):
    """
    Determines the location of the config file, possibly falling back to a default.
    """
    try:
        return ctx.obj.config_file
    except (click.BadParameter, AttributeError):
        for fname in (
                'hopic-ci-config.yaml',
            ):
            fname = os.path.join(ctx.obj.workspace, fname)
            if os.path.isfile(fname):
                return fname
        raise


@click.group(context_settings=dict(help_option_names=('-h', '--help')))
@click.option('--color', type=click.Choice(('always', 'auto', 'never')), default='auto', show_default=True)
@click.option('--config', type=click.Path(exists=False, file_okay=True, dir_okay=False, readable=True, resolve_path=True), default=lambda: None, show_default='${WORKSPACE}/hopic-ci-config.yaml')
@click.option('--workspace', type=click.Path(exists=False, file_okay=False, dir_okay=True), default=lambda: None, show_default='git work tree of config file or current working directory')
@click.option('--whitelisted-var', multiple=True, default=['CT_DEVENV_HOME'], show_default=True)
@click_log.simple_verbosity_option(__package__,              envvar='HOPIC_VERBOSITY', autocompletion=cli_autocomplete_click_log_verbosity)
@click_log.simple_verbosity_option('git', '--git-verbosity', envvar='GIT_VERBOSITY'  , autocompletion=cli_autocomplete_click_log_verbosity)
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
                        f"Directory '{workspace}' does not exist.",
                        ctx=ctx, param=param
                    )
        elif param.human_readable_name == 'config' and config is not None:
            # Require the config file to exist everywhere that it's used
            if not os.path.isfile(config):
                def exception_raiser(ctx, param):
                    raise click.BadParameter(
                        f"File '{config}' does not exist.",
                        ctx=ctx, param=param
                    )
                ctx.obj.register_parameter(ctx=ctx, param=param, exception_raiser=exception_raiser)

    if workspace is None:
        # workspace default
        if config is not None:
            try:
                with git.Repo(os.path.dirname(config), search_parent_directories=True) as repo:
                    # Default to containing repository of config file, ...
                    workspace = repo.working_dir
            except (git.InvalidGitRepositoryError, git.NoSuchPathError):
                # ... but fall back to containing directory of config file.
                workspace = os.path.dirname(config)
        else:
            workspace = os.getcwd()
    workspace = os.path.join(os.getcwd(), workspace)
    ctx.obj.workspace = workspace

    ctx.obj.volume_vars = {}
    try:
        with git.Repo(workspace) as repo, repo.config_reader() as cfg:
            code_dir = os.path.join(workspace, cfg.get_value('hopic.code', 'dir'))
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

    if config is not None:
        ctx.obj.config_file = config
    ctx.obj.register_dependent_attribute('config_file', 'config')
    try:
        config = determine_config_file_name(ctx)
    except click.BadParameter:
        config = None

    cfg = {}
    if config is not None:
        if not os.path.isabs(config):
            config = os.path.join(os.getcwd(), config)
        ctx.obj.volume_vars['CFGDIR'] = ctx.obj.config_dir = os.path.dirname(config)
        # Prevent reading the config file _before_ performing a checkout. This prevents a pre-existing file at the same
        # location from being read as the config file. This may cause problems if that pre-checkout file has syntax
        # errors for example.
        if ctx.invoked_subcommand != 'checkout-source-tree' and os.path.isfile(config):
            cfg = ctx.obj.config = read_config(config, ctx.obj.volume_vars)
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


def checkout_tree(tree, remote, ref, clean=False, remote_name='origin', allow_submodule_checkout_failure=False, clean_config=[]):
    try:
        repo = git.Repo(tree)
        # Cleanup potential existing submodules to avoid conflicts in PR's where submodules are added
        # Cannot use config file here to determine if feature is enabled since config is not parsed during checkout-source-tree
        repo.git.submodule(["deinit", "--all", "--force"])
        modules_dir = "%s/modules" % repo.git_dir
        if os.path.isdir(modules_dir):
            shutil.rmtree(modules_dir) # Hacky way to restore git repo to clean state
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
        repo.git.submodule(["deinit", "--all", "--force"]) # Remove potential moved submodules

        try:
            update_submodules(repo, clean)
        except git.GitCommandError as e:
            log.error('Failed to checkout submodule for ref \'%s\'\n'
                        'error:\n%s' % (ref, e))
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

    clean_output = repo.git.clean('-xd', force=True)
    if clean_output:
        log.info('%s', clean_output)

    # Only restore mtimes when doing a clean build. This prevents problems with timestamp-based build sytems.
    # I.e. make and ninja and probably half the world.
    restore_mtime_from_git(repo)


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
    return f"{utctime} {date:%z}"


@cli.command()
@click.option('--target-remote'     , metavar='<url>')
@click.option('--target-ref'        , metavar='<ref>')
@click.option('--clean/--no-clean'  , default=False, help='''Clean workspace of non-tracked files''')
@click.option('--ignore-initial-submodule-checkout-failure/--no-ignore-initial-submodule-checkout-failure',
              default=False, help='''Ignore git submodule errors during initial checkout''')
@click.pass_context
def checkout_source_tree(ctx, target_remote, target_ref, clean, ignore_initial_submodule_checkout_failure):
    """
    Checks out a source tree of the specified remote's ref to the workspace.
    """

    workspace = ctx.obj.workspace
    # Check out specified repository
    click.echo(checkout_tree(workspace, target_remote, target_ref, clean, allow_submodule_checkout_failure=ignore_initial_submodule_checkout_failure))

    try:
        ctx.obj.config = read_config(determine_config_file_name(ctx), ctx.obj.volume_vars)
        if clean:
            with git.Repo(workspace) as repo:
                clean_repo(repo, ctx.obj.config['clean'])
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
            dir = ('code' if seq == 0 else f"code-{seq:03}")
            seq += 1
            if dir not in code_dirs:
                code_dir = dir
                break

    # Check out configured repository and mark it as the code directory of this one
    ctx.obj.code_dir = os.path.join(workspace, code_dir)
    with git.Repo(workspace) as repo, repo.config_writer() as cfg:
        cfg.remove_section('hopic.code')
        cfg.set_value('hopic.code', 'dir', code_dir)
        cfg.set_value('hopic.code', 'cfg-remote', target_remote)
        cfg.set_value('hopic.code', 'cfg-ref', target_ref)
        cfg.set_value('hopic.code', 'cfg-clean', str(clean))

    checkout_tree(ctx.obj.code_dir, git_cfg.get('remote', target_remote), git_cfg.get('ref', target_ref),
                  clean, clean_config=ctx.obj.config['clean'])


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
        author = git.Actor.author(repo.config_reader())
        if author_name is not None:
            author.name = author_name
        if author_email is not None:
            author.email = author_email

        committer = git.Actor.author(repo.config_reader())
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

        # Re-read config to ensure any changes introduced by 'change_applicator' are taken into account
        try:
            config_file = determine_config_file_name(ctx)
            ctx.obj.config = read_config(config_file, ctx.obj.volume_vars)
            ctx.obj.volume_vars['CFGDIR'] = ctx.obj.config_dir = os.path.dirname(config_file)
        except (click.BadParameter, KeyError, TypeError, OSError, IOError):
            pass

        # Ensure that, when we're dealing with a separated config and code repository, that the code repository is checked out again to the newer version
        if ctx.obj.code_dir != ctx.obj.workspace:
            with repo.config_reader() as cfg:
                try:
                    code_remote = ctx.obj.config['scm']['git']['remote']
                except (KeyError, TypeError):
                    code_remote = cfg.get_value('hopic.code', 'cfg-remote')
                try:
                    code_commit = ctx.obj.config['scm']['git']['ref']
                except (KeyError, TypeError):
                    code_commit = cfg.get_value('hopic.code', 'cfg-ref')

            checkout_tree(ctx.obj.code_dir, code_remote, code_commit, code_clean, clean_config=ctx.obj.config['clean'])

        version_info = ctx.obj.config['version']

        # Re-read version to ensure that the version policy in the reloaded configuration is used for it
        ctx.obj.version = determine_version(version_info, ctx.obj.config_dir, ctx.obj.code_dir)
        
        # If the branch is not allowed to publish, skip version bump step
        is_publish_allowed = is_publish_branch(ctx)

        if 'file' in version_info:
            relative_version_file = os.path.relpath(os.path.join(os.path.relpath(ctx.obj.config_dir, repo.working_dir), version_info['file']))

        bump = version_info['bump']
        source_commits = (() if source_commit is None
                else [parse_commit_message(commit, policy=bump['policy'], strict=bump.get('strict', False))
                        for commit in git.Commit.list_items(
                        repo,
                        f"{target_commit}..{source_commit}",
                        first_parent=True,
                        no_merges=True,
                    )])

        if bump['policy'] == 'conventional-commits' and target_ref is not None:
            if bump['reject-breaking-changes-on'].match(target_ref):
                for commit in source_commits:
                    if commit.has_breaking_change():
                        raise VersioningError(f"Breaking changes are not allowed on '{target_ref}', but commit '{commit.hexsha}' contains one:\n{commit.message}")
            if bump['reject-new-features-on'].match(target_ref):
                for commit in source_commits:
                    if commit.has_new_feature():
                        raise VersioningError(f"New features are not allowed on '{target_ref}', but commit '{commit.hexsha}' contains one:\n{commit.message}")
        
        version_bumped = False
        if is_publish_allowed and bump['policy'] != 'disabled' and bump['on-every-change']:
            if ctx.obj.version is None:
                if 'file' in version_info:
                    raise VersioningError(f"Failed to read the current version (from {version[file]}) while attempting to bump the version")
                else:
                    msg = "Failed to determine the current version while attempting to bump the version"
                    log.error(msg)
                    # TODO: PIPE-309: provide an initial starting point instead
                    log.info("If this is a new repository you may wish to create a 0.0.0 tag for Hopic to start bumping from")
                    raise VersioningError(msg)

            if bump['policy'] == 'constant':
                params = {}
                if 'field' in bump:
                    params['bump'] = bump['field']
                new_version = ctx.obj.version.next_version(**params)
            elif bump['policy'] in ('conventional-commits',):
                if log.isEnabledFor(logging.DEBUG):
                    log.debug("bumping based on conventional commits:")
                    for commit in source_commits:
                        breaking = ('breaking' if commit.has_breaking_change() else '')
                        feat = ('feat' if commit.has_new_feature() else '')
                        fix = ('fix' if commit.has_fix() else '')
                        try:
                            hash_prefix = click.style(commit.hexsha, fg='yellow') + ': '
                        except AttributeError:
                            hash_prefix = ''
                        log.debug("%s[%-8s][%-4s][%-3s]: %s", hash_prefix, breaking, feat, fix, commit.full_subject)
                new_version = ctx.obj.version.next_version_for_commits(source_commits)
            else:
                raise NotImplementedError(f"unsupported version bumping policy {bump['policy']}")

            assert new_version >= ctx.obj.version, "the new version should be more recent than the old one"

            if new_version != ctx.obj.version:
                version_bumped = True
                ctx.obj.version = new_version
                log.debug("bumped version to: %s", click.style(str(ctx.obj.version), fg='blue'))

                if 'file' in version_info:
                    replace_version(os.path.join(ctx.obj.config_dir, version_info['file']), ctx.obj.version)
                    repo.index.add((relative_version_file,))
        else:
            log.info("Skip version bumping due to the configuration or the target branch is not allowed to publish")

        commit_params.setdefault('author', author)
        if author_date is not None:
            commit_params['author_date'] = to_git_time(author_date)
        if commit_date is not None:
            commit_params['commit_date'] = to_git_time(commit_date)

        submit_commit = repo.index.commit(**commit_params)
        click.echo(submit_commit)

        autosquash_commits = [commit
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
                    env = {'GIT_EDITOR': ':'}
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
        tagname = None
        version_tag = version_info.get('tag', False)
        if version_bumped and not ctx.obj.version.prerelease and version_tag and is_publish_allowed:
            if version_tag and not isinstance(version_tag, str):
                version_tag = ctx.obj.version.default_tag_name
            tagname = version_tag.format(
                    version        = ctx.obj.version,
                    build_sep      = ('+' if getattr(ctx.obj.version, 'build', None) else ''),
                )
            repo.create_tag(tagname, submit_commit, force=True)

        # Re-read version to ensure that the newly created tag is taken into account
        ctx.obj.version = determine_version(version_info, ctx.obj.config_dir, ctx.obj.code_dir)

        log.info('%s', repo.git.show(submit_commit, format='fuller', stat=True))

        push_commit = submit_commit
        if ctx.obj.version is not None and 'file' in version_info and 'bump' in version_info.get('after-submit', {}) and is_publish_allowed and bump['on-every-change']:
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

            old_version_blob = submit_commit.tree[relative_version_file]
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
            refspecs = []
            if target_ref is not None:
                cfg.set_value(section, 'ref', target_ref)
                refspecs.append(f"{push_commit}:{target_ref}")
            if tagname is not None:
                refspecs.append(f"refs/tags/{tagname}:refs/tags/{tagname}")
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
                log.warning("Approval for '%s' is not valid anymore due to content changes compared to last reviewed commit '%s'", approver, last_reviewed_commit_hash)
                continue

            # Source has a different hash, but no content diffs.
            # Now 'squash' and compare metadata (author, date, commit message).
            merge_base = repo.merge_base(repo.head.commit, source_commit)

            source_commits = [(commit.author, commit.authored_date, commit.message.rstrip()) for commit in
                git.Commit.list_items(repo, merge_base[0].hexsha + '..' + source_commit.hexsha, first_parent=True, no_merges=True)]

            autosquashed_reviewed_commits = [(commit.author, commit.authored_date, commit.message.rstrip()) for commit in
                git.Commit.list_items(repo, merge_base[0].hexsha + '..' + last_reviewed_commit.hexsha, first_parent=True, no_merges=True)
                if not autosquash_re.match(commit.message)]

            log.debug("For approver '%s', checking source commits:\n%s\n.. against squashed reviewed commits:\n%s",
                    approver, source_commits, autosquashed_reviewed_commits)

            if autosquashed_reviewed_commits == source_commits:
                log.debug("Approval for '%s' is still valid", approver)
                valid_approvers.append(approver)
            else:
                log.warning("Approval for '%s' is not valid anymore due to metadata changes compared to last reviewed commit '%s'", approver, last_reviewed_commit_hash)
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
        msg += u'\n'
        if approved_by:
            approvers = get_valid_approvers(repo, approved_by, source, source_commit)
            msg += '\n'.join(f"Acked-by: {approver}" for approver in approvers) + u'\n'
        msg += u'Merged-by: Hopic {pkg.version}\n'.format(pkg=metadata.distribution(__package__))
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
                add_files.add(diff.b_path)
                remove_files.add(diff.a_path)
            if remove_files:
                repo.index.remove(remove_files)
            if add_files:
                repo.index.add(add_files)

        if not repo.index.diff(repo.head.commit):
            log.info("No changes introduced by '%s'", commit_message)
            return None
        commit_message = (commit_message.rstrip()
                + u'\n\nMerged-by: Hopic {pkg.version}\n'.format(pkg=metadata.distribution(__package__)))

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


@cli.command()
@click.option('--phase'             , metavar='<phase>'  , multiple=True, help='''Build phase''', autocompletion=cli_autocomplete_phase_from_config)
@click.option('--variant'           , metavar='<variant>', multiple=True, help='''Configuration variant''', autocompletion=cli_autocomplete_variant_from_config)
@click.pass_context
def getinfo(ctx, phase, variant):
    """
    Display meta-data associated with each (or the specified) variant in each (or the specified) phase.

    The output is JSON encoded.

    If a phase or variant filter is specified the name of that will not be present in the output.
    Otherwise this is a nested dictionary of phases and variants.
    """

    info = OrderedDict()
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

            for var in curvariant:
                if isinstance(var, str):
                    continue
                for key, val in var.items():
                    try:
                        val = expand_vars(ctx.obj.volume_vars, val)
                    except KeyError:
                        pass
                    else:
                        if key in var_info and isinstance(var_info[key], Mapping):
                            var_info[key].update(val)
                        elif key in var_info and isinstance(var_info[key], MutableSequence):
                            var_info[key].extend(val)
                        else:
                            var_info[key] = val
    click.echo(json.dumps(info, indent=4, separators=(',', ': '), cls=JSONEncoder))


@cli.command()
@click.option('--phase'             , metavar='<phase>'  , multiple=True, help='''Build phase to execute''', autocompletion=cli_autocomplete_phase_from_config)
@click.option('--variant'           , metavar='<variant>', multiple=True, help='''Configuration variant to build''', autocompletion=cli_autocomplete_variant_from_config)
@click.pass_context
def build(ctx, phase, variant):
    """
    Build for the specified commit.

    This defaults to building all variants for all phases.
    It's possible to limit building to either all variants for a single phase, all phases for a single variant or a
    single variant for a single phase.
    """
    cfg = ctx.obj.config

    submit_ref = None
    refspecs = []
    source_commits = []
    autosquashed_commits = []
    try:
        with git.Repo(ctx.obj.workspace) as repo:
            submit_commit = repo.head.commit
            section = f"hopic.{submit_commit}"
            with repo.config_reader() as git_cfg:
                # Determine remote ref for current commit
                submit_ref = git_cfg.get_value(section, 'ref')

                if git_cfg.has_option(section, 'refspecs'):
                    refspecs = list(shlex.split(git_cfg.get_value(section, 'refspecs')))

                if git_cfg.has_option(section, 'target-commit') and git_cfg.has_option(section, 'source-commit'):
                    target_commit = repo.commit(git_cfg.get_value(section, 'target-commit'))
                    source_commit = repo.commit(git_cfg.get_value(section, 'source-commit'))
                    source_commits = git.Commit.list_items(repo, f"{target_commit}..{source_commit}", first_parent=True, no_merges=True)
                    autosquashed_commits = source_commits
                    log.debug('Building for source commits: %s', source_commits)
                if git_cfg.has_option(section, 'autosquashed-commit'):
                    autosquashed_commit = repo.commit(git_cfg.get_value(section, 'autosquashed-commit'))
                    autosquashed_commits = git.Commit.list_items(repo, f"{target_commit}..{autosquashed_commit}", first_parent=True, no_merges=True)
    except NoSectionError:
        pass
    has_change = bool(refspecs)

    worktree_commits = {}
    for phasename, curphase in cfg['phases'].items():
        if phase and phasename not in phase:
            continue
        for curvariant, cmds in curphase.items():
            if variant and curvariant not in variant:
                continue

            images = cfg['image']
            try:
                image = images[curvariant]
            except KeyError:
                image = images.get('default', None)

            docker_in_docker = False

            volume_vars = ctx.obj.volume_vars.copy()
            # Give commands executing inside a container image a different view than outside
            volume_vars['GIT_COMMIT'] = str(submit_commit)
            if submit_ref is not None:
                volume_vars['GIT_BRANCH'] = submit_ref

            artifacts = []
            with DockerContainers() as volumes_from:
                # If the branch is not allowed to publish, skip the publish phase. If run_on_change is set to 'always', phase will be run anyway regardless of this condition
                # For build phase, run_on_change is set to 'always' by default, so build will always happen
                is_publish_allowed = is_publish_branch(ctx)
                volumes = cfg['volumes'].copy()
                for cmd in cmds:
                    worktrees = {}
                    foreach = None
                    if not isinstance(cmd, str):
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

                        try:
                            cmd_volumes_from = cmd['volumes-from']
                        except (KeyError, TypeError):
                            pass
                        else:
                            if image:
                                for volume in cmd_volumes_from:
                                    volumes_from.add(volume['image'])
                            else:
                                log.warning('`volumes-from` has no effect if no Docker image is configured')

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
                            scoped_volumes = expand_docker_volume_spec(ctx.obj.volume_vars['CFGDIR'],
                                                                       ctx.obj.volume_vars, cmd['volumes'])
                            volumes.update(scoped_volumes)
                        except KeyError:
                            pass

                        try:
                            image = cmd['image']
                        except KeyError:
                            pass

                        try:
                            docker_in_docker = cmd['docker-in-docker']
                        except KeyError:
                            pass

                        try:
                            cmd = cmd['sh']
                        except (KeyError, TypeError):
                            continue

                    volume_vars['WORKSPACE'] = '/code' if image is not None else ctx.obj.code_dir

                    cmd = shlex.split(cmd)
                    env = (dict(
                        HOME            = '/home/sandbox',
                        _JAVA_OPTIONS   = '-Duser.home=/home/sandbox',
                    ) if image is not None else {})

                    for varname in cfg['pass-through-environment-vars']:
                        if varname in os.environ:
                            env.setdefault(varname, os.environ[varname])

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
                            cfg_vars[foreach] = str(foreach_item)

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
                        with tempfile.TemporaryDirectory(prefix='hopic-run-state') as tmpdir:
                            cidfile = None
                            if image is not None:
                                uid, gid = os.getuid(), os.getgid()
                                cidfile = os.path.join(tmpdir, 'cid')
                                docker_run = ['docker', 'run',
                                              '--rm',
                                              f"--cidfile={cidfile}",
                                              '--net=host',
                                              '--tty',
                                              '--cap-add=SYS_PTRACE',
                                              f"--tmpfs={env['HOME']}:uid={uid},gid={gid}",
                                              f"--user={uid}:{gid}",
                                              '--volume=/etc/passwd:/etc/passwd:ro',
                                              '--volume=/etc/group:/etc/group:ro',
                                              '--workdir=/code',
                                              ] + [
                                                  f"--env={k}={v}" for k, v in env.items()
                                              ]

                                if docker_in_docker:
                                    try:
                                        sock = '/var/run/docker.sock'
                                        st = os.stat(sock)
                                    except OSError as e:
                                        log.error("Docker in Docker access requested but cannot access Docker socket: %s", e)
                                    else:
                                        if stat.S_ISSOCK(st.st_mode):
                                            docker_run += [f"--volume={sock}:{sock}"]
                                            # Give group access to the socket if it's group accessible but not world accessible
                                            if st.st_mode & 0o0060 == 0o0060 and st.st_mode & 0o0006 != 0o0006:
                                                docker_run += [f"--group-add={st.st_gid}"]

                                for volume in volumes.values():
                                    docker_run += ['--volume={}'.format(volume_spec_to_docker_param(volume))]

                                for volume_from in volumes_from:
                                    docker_run += ['--volumes-from=' + volume_from]

                                docker_run.append(str(image))
                                final_cmd = docker_run + final_cmd
                            new_env = os.environ.copy()
                            if image is None:
                                new_env.update(env)
                            def signal_handler(signum, frame):
                                log.warning('Received fatal signal %d', signum)
                                raise FatalSignal(signum)
                            old_handlers = dict((num, signal.signal(num, signal_handler)) for num in (signal.SIGINT, signal.SIGTERM))
                            try:
                                echo_cmd(subprocess.check_call, final_cmd, env=new_env, cwd=ctx.obj.code_dir)
                            except subprocess.CalledProcessError as e:
                                log.error("Command fatally terminated with exit code %d", e.returncode)
                                ctx.exit(e.returncode)
                            except FatalSignal as e:
                                if cidfile and os.path.isfile(cidfile):
                                    # If we're being signalled to shut down ensure the spawned docker container also gets cleaned up.
                                    with open(cidfile) as f:
                                        cid = f.read()
                                    try:
                                        # Will also remove the container due to the '--rm' it was started with.
                                        echo_cmd(subprocess.check_call, ('docker', 'stop', cid))
                                    except subprocess.CalledProcessError as e:
                                        log.error('Could not stop Docker container (maybe it was stopped already?), command failed with exit code %d', e.returncode)
                                ctx.exit(128 + e.signal)
                            for num, old_handler in old_handlers.items():
                                signal.signal(num, old_handler)

                    for subdir, worktree in worktrees.items():
                        with git.Repo(os.path.join(ctx.obj.workspace, subdir)) as repo:
                            worktree_commits.setdefault(subdir, [
                                str(repo.head.commit),
                                str(repo.head.commit),
                            ])

                            if 'changed-files' in worktree:
                                changed_files = worktree["changed-files"]
                                if isinstance(changed_files, str):
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
                                if remove_files:
                                    repo.index.remove(remove_files)
                                if add_files:
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
                            worktree_commits[subdir][1] = str(submit_commit)
                            log.info('%s', repo.git.show(submit_commit, format='fuller', stat=True))

                if worktree_commits:
                    with git.Repo(ctx.obj.workspace) as repo, repo.config_writer() as cfg:
                        bundle_commits = []
                        for subdir, (base_commit, submit_commit) in worktree_commits.items():
                            worktree_ref = ctx.obj.config['scm']['git']['worktrees'][subdir]
                            if worktree_ref in repo.heads:
                                repo.heads[worktree_ref].set_commit(submit_commit, logmsg='Prepare for git-bundle')
                            else:
                                repo.create_head(worktree_ref, submit_commit)
                            bundle_commits.append(f"{base_commit}..{worktree_ref}")
                            refspecs.append(f"{submit_commit}:{worktree_ref}")
                        repo.git.bundle('create', os.path.join(ctx.obj.workspace, 'worktree-transfer.bundle'), *bundle_commits)

                        submit_commit = repo.head.commit
                        cfg.set_value(f"hopic.{submit_commit}", 'refspecs', ' '.join(shlex.quote(refspec) for refspec in refspecs))

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
        section = f"hopic.{submit_commit}"
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

@cli.command()
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

        with repo.config_writer() as cfg:
            cfg.remove_section(section)


@cli.command()
@click.pass_context
def show_config(ctx):
    """
    Diagnostic helper command to display the configuration after processing.
    """

    click.echo(json.dumps(ctx.obj.config, indent=4, separators=(',', ': '), cls=JSONEncoder))


@cli.command()
@click.pass_context
def show_env(ctx):
    """
    Diagnostic helper command to display the execution environment.
    """

    click.echo(json.dumps(ctx.obj.volume_vars, indent=4, separators=(',', ': '), sort_keys=True))
