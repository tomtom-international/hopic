import click

from .config_reader import read as read_config
from .config_reader import expand_vars
from .execution import echo_cmd
from .versioning import *
from datetime import datetime
from dateutil.parser import parse as date_parse
from dateutil.tz import (tzoffset, tzlocal)
from itertools import chain
import json
import os
import re
import shlex
from six import string_types
import subprocess
import sys

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
    if not workspace or not os.path.isdir(os.path.join(workspace, '.git')):
        return False
    try:
        output = echo_cmd(subprocess.check_output, ('git', 'rev-parse', '--is-inside-work-tree'), cwd=workspace)
    except subprocess.CalledProcessError:
        return False
    return output.strip().lower() == 'true'

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

@click.group(context_settings=dict(help_option_names=('-h', '--help')))
@click.option('--color', type=click.Choice(('always', 'auto', 'never')), default='auto')
@click.option('--config', type=click.Path(exists=True, readable=True, resolve_path=True))
@click.option('--workspace', type=click.Path(exists=False, file_okay=False, dir_okay=True))
@click.pass_context
def cli(ctx, color, config, workspace):
    if color == 'always':
        ctx.color = True
    elif color == 'never':
        ctx.color = False
    else:
        # leave as is: 'auto' is the default for Click
        pass

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

    ctx.obj.volume_vars = {}
    if workspace is not None:
        ctx.obj.volume_vars['WORKSPACE'] = workspace
    for whitelisted_var in (
            'CT_DEVENV_HOME',
        ):
        try:
            ctx.obj.volume_vars[whitelisted_var] = os.environ[whitelisted_var]
        except KeyError:
            pass

    if config is not None:
        cfg = ctx.obj.config = read_config(config, ctx.obj.volume_vars)
    else:
        cfg = {}

    ctx.obj.version = None
    version_info = cfg.get('version', {})
    if 'file' in version_info and ctx.obj.version is None:
        params = {}
        if 'format' in version_info:
            params['format'] = version_info['format']
        fname = version_info['file']
        if os.path.isfile(fname):
            ctx.obj.version = read_version(fname, **params)
    if 'tag' in version_info and ctx.obj.version is None:
        try:
            describe_out = echo_cmd(subprocess.check_output, (
                    'git', 'describe', '--tags', '--long', '--dirty', '--always'
                ),
                cwd=workspace,
            ).strip()

            params = {}
            if 'format' in version_info:
                params['format'] = version_info['format']
            ctx.obj.version = parse_git_describe_version(describe_out, **params)
        except subprocess.CalledProcessError:
            pass
    if ctx.obj.version is not None:
        click.echo("[DEBUG]: read version: \x1B[34m{ctx.obj.version}\x1B[39m".format(**locals()), err=True)
        ctx.obj.volume_vars['VERSION'] = str(ctx.obj.version)

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
    if not git_has_work_tree(workspace):
        echo_cmd(subprocess.check_call, ('git', 'clone', '-c' 'color.ui=always', target_remote, workspace))
    echo_cmd(subprocess.check_call, ('git', 'config', 'color.ui', 'always'), cwd=workspace)
    tags = tuple(tag.strip() for tag in echo_cmd(subprocess.check_output, ('git', 'tag'), cwd=workspace).split('\n') if tag.strip())
    if tags:
        echo_cmd(subprocess.check_call, ('git', 'tag', '-d') + tags, cwd=workspace, stdout=sys.stderr)
    echo_cmd(subprocess.check_call, ('git', 'fetch', '--tags', target_remote, target_ref), cwd=workspace)
    commit = echo_cmd(subprocess.check_output, ('git', 'rev-parse', 'FETCH_HEAD'), cwd=workspace).strip()
    echo_cmd(subprocess.check_call, ('git', 'checkout', '--force', commit), cwd=workspace)
    if clean:
      echo_cmd(subprocess.check_call, ('git', 'clean', '--force', '-xd'), cwd=workspace, stdout=sys.stderr)
    click.echo(commit)

    echo_cmd(subprocess.check_call, ('git', 'config', 'ci-driver.{commit}.ref'.format(**locals()), target_ref), cwd=workspace)
    echo_cmd(subprocess.check_call, ('git', 'config', 'ci-driver.{commit}.remote'.format(**locals()), target_remote), cwd=workspace)

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
    cfg = getattr(ctx.obj, 'config', {})
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

    version_info = cfg.get('version', {})
    version_tag  = version_info.get('tag', False)
    if version_tag and not isinstance(version_tag, string_types):
        version_tag = '{version.major}.{version.minor}.{version.patch}'

    if ctx.obj.version is not None and version_info.get('bump', True):
        params = {}
        if 'bump' in version_info:
            params['bump'] = version_info['bump']
        ctx.obj.version = ctx.obj.version.next_version(**params)
        click.echo("[DEBUG]: bumped version to: \x1B[34m{ctx.obj.version}\x1B[39m".format(**locals()), err=True)

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
@click.option('--description'   , metavar='<description>'          , help='''Change request title to incorporate in merge commit's subject line''')
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
@click.argument('modality')
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
        message = modality
        for cmd in modality_cmds:
            try:
                cmd["changed-files"]
            except (KeyError, TypeError):
                pass
            else:
                has_changed_files = True
            try:
                message = cmd["message"]
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
                click.echo('Performing: ' + click.style(desc, fg='cyan'), err=True)

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
            click.echo("No changes introduced by '{message}'".format(**locals()), err=True)
            return None
        return message
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
@click.option('--phase'             , metavar='<phase>'  , help='''Build phase to show variants for''')
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
@click.option('--phase'             , metavar='<phase>'  , required=True, help='''Build phase''')
@click.option('--variant'           , metavar='<variant>', required=True, help='''Configuration variant''')
@click.pass_context
def getinfo(ctx, phase, variant):
    """
    Display meta-data associated with the specified variant in the given phase as JSON.
    """

    variants = []
    info = {}
    for var in ctx.obj.config['phases'][phase][variant]:
        if isinstance(var, string_types):
            continue
        for key, val in var.items():
            info[key] = expand_vars(ctx.obj.volume_vars, val)
    click.echo(json.dumps(info))

@cli.command()
@click.option('--phase'             , metavar='<phase>'  , help='''Build phase to execute''')
@click.option('--variant'           , metavar='<variant>', help='''Configuration variant to build''')
@click.pass_context
def build(ctx, phase, variant):
    """
    Build for the specified commit.

    This defaults to building all variants for all phases.
    It's possible to limit building to either all variants for a single phase, all phases for a single variant or a
    single variant for a single phase.
    """

    cfg = ctx.obj.config
    for phasename, curphase in cfg['phases'].items():
        if phase is not None and phasename != phase:
            continue
        for curvariant, cmds in curphase.items():
            if variant is not None and curvariant != variant:
                continue
            for cmd in cmds:
                if not isinstance(cmd, string_types):
                    try:
                        desc = cmd['description']
                    except (KeyError, TypeError):
                        pass
                    else:
                        click.echo('Performing: ' + click.style(desc, fg='cyan'), err=True)
                    try:
                        cmd = cmd['sh']
                    except (KeyError, TypeError):
                        continue

                cmd = shlex.split(cmd)
                env = (dict(
                        HOME            = '/home/sandbox',
                        _JAVA_OPTIONS   = '-Duser.home=/home/sandbox',
                    ) if 'image' in cfg else {})
                # Strip of prefixed environment variables from this command-line and apply them
                while cmd:
                    m = _env_var_re.match(cmd[0])
                    if not m:
                        break
                    env[m.group('var')] = expand_vars(volume_vars, m.group('val'))
                    cmd.pop(0)

                # Handle execution inside docker
                if 'image' in cfg:
                    image = cfg['image']
                    if not isinstance(image, string_types):
                        try:
                            image = image[curvariant]
                        except KeyError:
                            image = image['default']
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
                if 'image' not in cfg:
                    new_env.update(env)
                try:
                    echo_cmd(subprocess.check_call, cmd, env=new_env)
                except subprocess.CalledProcessError as e:
                    click.secho("Command fatally terminated with exit code {}".format(e.returncode), fg='red', err=True)
                    sys.exit(e.returncode)

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
