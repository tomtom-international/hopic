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
from datetime import (datetime, timedelta)
from dateutil.parser import parse as date_parse
from dateutil.tz import (tzoffset, tzlocal, tzutc)
import git
from itertools import chain
import json
import logging
import os
import re
import shlex
from six import string_types
import subprocess
import sys

try:
    from ConfigParser import NoSectionError
except ImportError:
    # PY3
    from configparser import NoSectionError

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
                tzmin  = int_or_none(stamp.group('tzmin' ))

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

def git_has_work_tree(workspace):
    try:
        with git.Repo(workspace):
            return True
    except (git.InvalidGitRepositoryError, git.NoSuchPathError):
        return False

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
            raise click.MissingParameter(**self._missing_parameters[name])
        except KeyError:
            raise AttributeError("'{}' object has no attribute '{}'.".format(self.__class__.__name__, name))

    def __setattr__(self, name, value):
        if name in frozenset({'_opts', '_missing_parameters'}):
            return super(OptionContext, self).__setattr__(name, value)

        self._opts[name] = value

    def __delattr__(self, name):
        del self._opts[name]

    def register_parameter(self, ctx, param, name=None):
        if name is None:
            name = param.human_readable_name

        self._missing_parameters[name] = dict(
                ctx=ctx,
                param=param,
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
        return cfg['phases'].keys()
    except Exception:
        return ()

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
        return cfg['modality-source-preparation'].keys()
    except Exception:
        return ()

def cli_autocomplete_click_log_verbosity(ctx, args, incomplete):
    return (
            'DEBUG',
            'INFO',
            'WARNING',
            'ERROR',
            'CRITICAL',
        )

@click.group(context_settings=dict(help_option_names=('-h', '--help')))
@click.option('--color', type=click.Choice(('always', 'auto', 'never')), default='auto')
@click.option('--config', type=click.Path(exists=False, file_okay=True, dir_okay=False, readable=True, resolve_path=True))
@click.option('--workspace', type=click.Path(exists=False, file_okay=False, dir_okay=True))
@click_log.simple_verbosity_option(__package__, autocompletion=cli_autocomplete_click_log_verbosity)
@click_log.simple_verbosity_option('git', '--git-verbosity', autocompletion=cli_autocomplete_click_log_verbosity)
@click.pass_context
def cli(ctx, color, config, workspace):
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
                try:
                    os.stat(workspace)
                except OSError:
                    raise click.BadParameter(
                            'Directory "{workspace}" does not exist.'.format(**locals()),
                            ctx=ctx, param=param
                        )
            ctx.obj.workspace = workspace
        elif param.human_readable_name == 'config' and config is not None:
            if ctx.invoked_subcommand != 'checkout-source-tree':
                # Require the config file to exist for anything but the checkout command
                try:
                    os.stat(config)
                except OSError:
                    raise click.BadParameter(
                            'File "{config}" does not exist.'.format(**locals()),
                            ctx=ctx, param=param
                        )

    ctx.obj.volume_vars = {}
    if workspace is not None:
        code_dir = workspace
        try:
            with git.Repo(workspace) as repo, repo.config_reader() as cfg:
                code_dir = os.path.join(workspace, cfg.get_value('ci-driver.code', 'dir'))
        except (git.InvalidGitRepositoryError, git.NoSuchPathError, NoSectionError):
            pass

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

    for whitelisted_var in (
            'CT_DEVENV_HOME',
        ):
        try:
            ctx.obj.volume_vars[whitelisted_var] = os.environ[whitelisted_var]
        except KeyError:
            pass

    cfg = {}
    if config is not None:
        ctx.obj.config_file = config
        ctx.obj.volume_vars['CFGDIR'] = os.path.dirname(config)
        if os.path.isfile(config):
            cfg = ctx.obj.config = read_config(config, ctx.obj.volume_vars)
    ctx.obj.register_dependent_attribute('config_file', 'config')

    ctx.obj.version = None
    version_info = cfg.get('version', {})
    if 'file' in version_info and ctx.obj.version is None:
        params = {}
        if 'format' in version_info:
            params['format'] = version_info['format']
        fname = version_info['file']
        if os.path.isfile(fname):
            ctx.obj.version = read_version(fname, **params)
    if 'tag' in version_info and ctx.obj.version is None and workspace is not None:
        try:
            with git.Repo(ctx.obj.code_dir) as repo:
                describe_out = repo.git.describe(tags=True, long=True, dirty=True, always=True)
        except (git.InvalidGitRepositoryError, git.NoSuchPathError):
            pass
        else:
            params = {}
            if 'format' in version_info:
                params['format'] = version_info['format']
            ctx.obj.version = parse_git_describe_version(describe_out, dirty_date=ctx.obj.source_date, **params)
    if ctx.obj.version is not None:
        log.debug("read version: \x1B[34m%s\x1B[39m", ctx.obj.version)
        ctx.obj.volume_vars['VERSION'] = str(ctx.obj.version)
        # FIXME: make this conversion work even when not using SemVer as versioning policy
        # Convert SemVer to Debian version: '~' for pre-release instead of '-'
        ctx.obj.volume_vars['DEBVERSION'] = ctx.obj.volume_vars['VERSION'].replace('-', '~', 1).replace('.dirty.', '+dirty', 1)

def checkout_tree(tree, remote, ref, clean):
    if not git_has_work_tree(tree):
        echo_cmd(subprocess.check_call, ('git', 'clone', '-c' 'color.ui=always', remote, tree))
    echo_cmd(subprocess.call, ('git', 'config', '--remove-section', 'ci-driver.code'), cwd=tree)
    echo_cmd(subprocess.check_call, ('git', 'config', 'color.ui', 'always'), cwd=tree)
    tags = tuple(tag.strip() for tag in echo_cmd(subprocess.check_output, ('git', 'tag'), cwd=tree).split('\n') if tag.strip())
    if tags:
        echo_cmd(subprocess.check_call, ('git', 'tag', '-d') + tags, cwd=tree, stdout=sys.stderr)
    echo_cmd(subprocess.check_call, ('git', 'fetch', '--tags', remote, ref), cwd=tree)
    commit = echo_cmd(subprocess.check_output, ('git', 'rev-parse', 'FETCH_HEAD'), cwd=tree).strip()
    echo_cmd(subprocess.check_call, ('git', 'checkout', '--force', commit), cwd=tree)
    if clean:
      echo_cmd(subprocess.check_call, ('git', 'clean', '--force', '-xd'), cwd=tree, stdout=sys.stderr)

    echo_cmd(subprocess.check_call, ('git', 'config', 'ci-driver.{commit}.ref'.format(**locals()), ref), cwd=tree)
    echo_cmd(subprocess.check_call, ('git', 'config', 'ci-driver.{commit}.remote'.format(**locals()), remote), cwd=tree)

    files = subprocess.check_output(('git', 'ls-files', '-z'), cwd=tree).split(b'\0')
    if files and not files[-1]:
        del files[-1]
    files = set(files)

    # Set all files' modification times to their last commit's time
    encoding = sys.getfilesystemencoding() or sys.getdefaultencoding()
    with open(os.devnull) as devnull:
        whatchanged = subprocess.Popen(('git', 'whatchanged', '--pretty=format:%ct'), cwd=tree, stdout=subprocess.PIPE, stderr=devnull)
        mtime = 0
        for line in whatchanged.stdout:
            if not files:
                break

            line = line.strip()
            if not line:
                continue
            if line.startswith(b':'):
                filename = line.split(b'\t')[-1]
                if filename in files:
                    files.remove(filename)
                    os.utime(os.path.join(tree.encode(encoding), filename), (mtime, mtime))
            else:
                mtime = int(line)
        try:
            whatchanged.terminate()
        except OSError:
            pass
    return commit

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
    except:
        return

    if 'remote' not in git_cfg and 'ref' not in git_cfg:
        return

    code_dir_re = re.compile(r'^code(?:-\d+)$')
    code_dirs = sorted(dir for dir in os.listdir(workspace) if code_dir_re.match(dir))
    for dir in code_dirs:
        if git_has_work_tree(os.path.join(workspace, dir)):
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
    echo_cmd(subprocess.check_call, ('git', 'config', 'ci-driver.code.dir', code_dir), cwd=workspace)
    echo_cmd(subprocess.check_call, ('git', 'config', 'ci-driver.code.cfg-remote', target_remote), cwd=workspace)
    echo_cmd(subprocess.check_call, ('git', 'config', 'ci-driver.code.cfg-ref', target_ref), cwd=workspace)
    echo_cmd(subprocess.check_call, ('git', 'config', 'ci-driver.code.cfg-clean', str(clean)), cwd=workspace)

    checkout_tree(ctx.obj.code_dir, git_cfg.get('remote', target_remote), git_cfg.get('ref', target_ref), clean)

@cli.group()
# git
@click.option('--author-name'               , metavar='<name>'                 , help='''Name of change-request's author''')
@click.option('--author-email'              , metavar='<email>'                , help='''E-mail address of change-request's author''')
@click.option('--author-date'               , metavar='<date>', type=DateTime(), help='''Time of last update to the change-request''')
@click.option('--commit-date'               , metavar='<date>', type=DateTime(), help='''Time of starting to build this change-request''')
def prepare_source_tree(*args, **kwargs):
    """
    Prepares the source tree for building with some change.
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
    workspace = ctx.obj.workspace
    assert git_has_work_tree(workspace)

    target_commit = echo_cmd(subprocess.check_output, ('git', 'rev-parse', 'HEAD'), cwd=workspace).strip()
    target_ref    = echo_cmd(subprocess.check_output, ('git', 'config', '--get', 'ci-driver.{target_commit}.ref'.format(**locals())), cwd=workspace).strip()
    target_remote = echo_cmd(subprocess.check_output, ('git', 'config', '--get', 'ci-driver.{target_commit}.remote'.format(**locals())), cwd=workspace).strip()
    echo_cmd(subprocess.check_call, ('git', 'config', '--remove-section', 'ci-driver.{target_commit}'.format(**locals())), cwd=workspace)

    env = os.environ.copy()
    if author_name is not None:
        env['GIT_AUTHOR_NAME'] = author_name
    if author_email is not None:
        env['GIT_AUTHOR_EMAIL'] = author_email
    if author_date is not None:
        env['GIT_AUTHOR_DATE'] = author_date.strftime('%Y-%m-%d %H:%M:%S.%f %z')
    if commit_date is not None:
        env['GIT_COMMITTER_DATE'] = commit_date.strftime('%Y-%m-%d %H:%M:%S.%f %z')

    msg = change_applicator(workspace)
    if msg is None:
        return

    # Ensure that, when we're dealing with a separated config and code repository, that the code repository is checked out again to the newer version
    if ctx.obj.code_dir != ctx.obj.workspace:
        # Re-read config
        ctx.obj.config = read_config(ctx.obj.config_file, ctx.obj.volume_vars)

        try:
            code_remote = ctx.obj.config['scm']['git']['remote']
        except (KeyError, TypeError):
            code_remote = echo_cmd(subprocess.check_output, ('git', 'config', '--get', 'ci-driver.code.cfg-remote'), cwd=workspace).strip()
        try:
            code_commit = ctx.obj.config['scm']['git']['ref']
        except (KeyError, TypeError):
            code_commit = echo_cmd(subprocess.check_output, ('git', 'config', '--get', 'ci-driver.code.cfg-ref'), cwd=workspace).strip()
        code_clean = {'true': True,
                'false': False,
            }[echo_cmd(subprocess.check_output, ('git', 'config', '--get', '--bool', 'ci-driver.code.cfg-clean'), cwd=workspace).strip()]

        checkout_tree(ctx.obj.code_dir, code_remote, code_commit, code_clean)

    version_info = ctx.obj.config.get('version', {})
    version_tag  = version_info.get('tag', False)
    if version_tag and not isinstance(version_tag, string_types):
        version_tag = '{version.major}.{version.minor}.{version.patch}'

    if ctx.obj.version is not None and version_info.get('bump', True):
        params = {}
        if 'bump' in version_info:
            params['bump'] = version_info['bump']
        ctx.obj.version = ctx.obj.version.next_version(**params)
        log.debug("bumped version to: \x1B[34m%s\x1B[39m", ctx.obj.version)

        if 'file' in version_info:
            replace_version(version_info['file'], ctx.obj.version)
            echo_cmd(subprocess.check_call, ('git', 'add', version_info['file']), cwd=workspace)

    echo_cmd(subprocess.check_call, (
            'git',
            'commit',
            '-m', msg,
        ),
        cwd=workspace,
        env=env,
        stdout=sys.stderr,
    )

    submit_commit = echo_cmd(subprocess.check_output, ('git', 'rev-parse', 'HEAD'), cwd=workspace).strip()
    click.echo(submit_commit)

    tagname = None
    if ctx.obj.version is not None and not ctx.obj.version.prerelease and version_tag:
        tagname = version_tag.format(
                version        = ctx.obj.version,
                build_sep      = ('+' if ctx.obj.version.build else ''),
            )
        echo_cmd(subprocess.check_call, ('git', 'tag', '-f', tagname, submit_commit), cwd=workspace, stdout=sys.stderr)

    echo_cmd(subprocess.check_call, ('git', 'show', '--format=fuller', '--stat', submit_commit), cwd=workspace, stdout=sys.stderr)

    echo_cmd(subprocess.call, ('git', 'config', '--unset-all', 'ci-driver.{submit_commit}.refspec'.format(**locals())), cwd=workspace)
    echo_cmd(subprocess.check_call, ('git', 'config', 'ci-driver.{submit_commit}.remote'.format(**locals()), target_remote), cwd=workspace)
    echo_cmd(subprocess.check_call, (
            'git', 'config', '--add',
            'ci-driver.{submit_commit}.refspec'.format(**locals()),
            '{submit_commit}:{target_ref}'.format(**locals()),
        ),
        cwd=workspace)
    if ctx.obj.version is not None:
        click.echo(ctx.obj.version)
    if tagname is not None:
        echo_cmd(subprocess.check_call, (
                'git', 'config', '--add',
                'ci-driver.{submit_commit}.refspec'.format(**locals()),
                'refs/tags/{tagname}:refs/tags/{tagname}'.format(**locals()),
            ),
            cwd=workspace)

@prepare_source_tree.command()
# git
@click.option('--source-remote' , metavar='<url>', help='<source> remote to merge into <target>')
@click.option('--source-ref'    , metavar='<ref>', help='ref of <source> remote to merge into <target>')
@click.option('--change-request', metavar='<identifier>'           , help='Identifier of change-request to use in merge commit message')
@click.option('--title'         , metavar='<title>'                , help='''Change request title to incorporate in merge commit's subject line''')
@click.option('--description'   , metavar='<description>'          , help='''Change request description to incorporate in merge commit message's body''')
def merge_change_request(
        source_remote,
        source_ref,
        change_request,
        title,
        description,
    ):
    """
    Merges the change request from the specified branch.
    """

    def change_applicator(workspace):
        echo_cmd(subprocess.check_call, ('git', 'fetch', source_remote, source_ref), cwd=workspace)
        echo_cmd(subprocess.check_call, (
                'git',
                'merge',
                '--no-ff',
                '--no-commit',
                'FETCH_HEAD',
            ),
            cwd=workspace,
            stdout=sys.stderr,
        )

        msg = "Merge #{}".format(change_request)
        if title is not None:
            msg = "{msg}: {title}".format(msg=msg, title=title)
        if description is not None:
            msg = "{msg}\n\n{description}".format(msg=msg, description=description)
        return msg
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
    def change_applicator(workspace):
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
            echo_cmd(subprocess.check_call, ('git', 'clean', '--force', '-xd'), cwd=workspace, stdout=sys.stderr)

        volume_vars = ctx.obj.volume_vars

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
                echo_cmd(subprocess.check_call, args, cwd=workspace, env=env, stdout=sys.stderr)

            if 'changed-files' in cmd:
                changed_files = cmd["changed-files"]
                if isinstance(changed_files, string_types):
                    changed_files = [changed_files]
                changed_files = [expand_vars(volume_vars, f) for f in changed_files]
                echo_cmd(subprocess.check_call, ['git', 'add'] + changed_files, cwd=workspace, stdout=sys.stderr)

        if not has_changed_files:
            # Force clean builds when we don't know how to discover changed files
            echo_cmd(subprocess.check_call, ('git', 'add', '--all'), cwd=workspace, stdout=sys.stderr)

        changed = echo_cmd(subprocess.call, (
                    'git', 'diff', '--exit-code', '--ignore-all-space', '--quiet', '--cached',
                ),
                cwd=workspace,
                stdout=sys.stderr,
            )
        if changed is None or changed == 0:
            log.info("No changes introduced by '%s'", commit_message)
            return None
        return commit_message
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
                info[key] = expand_vars(ctx.obj.volume_vars, val)
            except KeyError:
                pass
    click.echo(json.dumps(info))

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

    submit_commit = echo_cmd(subprocess.check_output, ('git', 'rev-parse', 'HEAD'), cwd=ctx.obj.workspace).strip()
    try:
        refspecs = tuple(refspec for refspec in
            echo_cmd(subprocess.check_output, (
                'git', 'config', '--get-all', '--null',
                'ci-driver.{submit_commit}.refspec'.format(**locals())
            )
            , cwd=ctx.obj.workspace).split('\0') if refspec)
    except subprocess.CalledProcessError as e:
        refspecs = ()
    has_change = bool(refspecs)

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

            for cmd in cmds:
                if not isinstance(cmd, string_types):
                    try:
                        run_on_change = cmd['run-on-change']
                    except (KeyError, TypeError):
                        pass
                    else:
                        if run_on_change == 'always':
                            pass
                        elif run_on_change == 'never' and has_change:
                            break
                        elif run_on_change == 'only' and not has_change:
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
                        cmd = cmd['sh']
                    except (KeyError, TypeError):
                        continue

                cmd = shlex.split(cmd)
                env = (dict(
                        HOME            = '/home/sandbox',
                        _JAVA_OPTIONS   = '-Duser.home=/home/sandbox',
                    ) if image is not None else {})
                try:
                    env['SOURCE_DATE_EPOCH'] = str(ctx.obj.source_date_epoch)
                except:
                    pass

                # Strip off prefixed environment variables from this command-line and apply them
                while cmd:
                    m = _env_var_re.match(cmd[0])
                    if not m:
                        break
                    env[m.group('var')] = expand_vars(volume_vars, m.group('val'))
                    cmd.pop(0)
                cmd = [expand_vars(volume_vars, arg) for arg in cmd]

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
                            '-v', '{WORKSPACE}:/code:rw'.format(**ctx.obj.volume_vars)
                        ] + list(chain(*[
                            ['-e', '{}={}'.format(k, v)] for k, v in env.items()
                        ]))
                    for volume in cfg['volumes']:
                        docker_run += ['-v', volume_spec_to_docker_param(volume)]
                    docker_run.append(image)
                    cmd = docker_run + cmd
                new_env = os.environ.copy()
                if image is None:
                    new_env.update(env)
                try:
                    echo_cmd(subprocess.check_call, cmd, env=new_env, cwd=ctx.obj.code_dir)
                except subprocess.CalledProcessError as e:
                    log.exception("Command fatally terminated with exit code %d", e.returncode)
                    sys.exit(e.returncode)

            # Post-processing to make these artifacts as reproducible as possible
            for artifact in artifacts:
                binary_normalize.normalize(os.path.join(ctx.obj.code_dir, artifact), source_date_epoch=ctx.obj.source_date_epoch)

@cli.command()
@click.option('--target-remote', metavar='<url>', help='''The remote to push to, if not specified this will default to the checkout remote.''')
@click.pass_context
def submit(ctx, target_remote):
    """
    Submit the changes created by prepare-source-tree to the target remote.
    """

    workspace = ctx.obj.workspace
    assert git_has_work_tree(workspace)

    submit_commit = echo_cmd(subprocess.check_output, ('git', 'rev-parse', 'HEAD'), cwd=workspace).strip()
    if target_remote is None:
        target_remote = echo_cmd(subprocess.check_output, ('git', 'config', '--get', 'ci-driver.{submit_commit}.remote'.format(**locals())), cwd=workspace).strip()

    refspecs = tuple(refspec for refspec in
        echo_cmd(subprocess.check_output, (
            'git', 'config', '--get-all', '--null',
            'ci-driver.{submit_commit}.refspec'.format(**locals())
        )
        , cwd=workspace).split('\0') if refspec)
    echo_cmd(subprocess.check_call, ('git', 'config', '--remove-section', 'ci-driver.{submit_commit}'.format(**locals())), cwd=workspace)

    echo_cmd(subprocess.check_call, ('git', 'push', '--atomic', target_remote) + refspecs, cwd=workspace)

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
