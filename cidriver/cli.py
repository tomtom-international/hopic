import click

from .config_reader import read as read_config
from .config_reader import expand_vars
from .execution import echo_cmd
from .versioning import (bump_version, stringify_semver)
from datetime import datetime
from dateutil.parser import parse as date_parse
from dateutil.tz import (tzoffset, tzlocal)
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
        output = echo_cmd(subprocess.check_output, ('git', 'rev-parse', '--is-inside-work-tree'), cwd=workspace, env={'LANG': 'C'})
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

@click.group(context_settings=dict(help_option_names=('-h', '--help')))
@click.option('--color', type=click.Choice(('always', 'auto', 'never')), default='auto')
@click.option('--config', type=click.Path(exists=True, readable=True, resolve_path=True))
@click.option('--workspace', type=click.Path(exists=True, file_okay=False, dir_okay=True))
@click.pass_context
def cli(ctx, color, config, workspace):
    if color == 'always':
        ctx.color = True
    elif color == 'never':
        ctx.color = False
    else:
        # leave as is: 'auto' is the default for Click
        pass

    if ctx.obj is None:
        ctx.obj = {}
    ctx.obj['workspace'] = workspace

    volume_vars = {}
    if workspace is not None:
        volume_vars['WORKSPACE'] = workspace
    for whitelisted_var in (
            'CT_DEVENV_HOME',
        ):
        try:
            volume_vars[whitelisted_var] = os.environ[whitelisted_var]
        except KeyError:
            pass
    ctx.obj['volume-vars'] = volume_vars

    if config is not None:
        ctx.obj['cfg'] = read_config(config, volume_vars)

@cli.command()
@click.option('--target-remote'     , metavar='<url>')
@click.option('--target-ref'        , metavar='<ref>')
@click.option('--clean/--no-clean'  , default=False, help='''Clean workspace of non-tracked files''')
@click.pass_context
def checkout_source_tree(ctx, target_remote, target_ref, clean):
    workspace = ctx.obj['workspace']
    if not git_has_work_tree(workspace):
        echo_cmd(subprocess.check_call, ('git', 'clone', '-c' 'color.ui=always', target_remote, workspace))
    echo_cmd(subprocess.check_call, ('git', 'config', 'color.ui', 'always'), cwd=workspace)
    echo_cmd(subprocess.check_call, ('git', 'fetch', target_remote, target_ref), cwd=workspace)
    echo_cmd(subprocess.check_call, ('git', 'checkout', '--force', 'FETCH_HEAD'), cwd=workspace)
    if clean:
      echo_cmd(subprocess.check_call, ('git', 'clean', '--force', '-xd'), cwd=workspace, stdout=sys.stderr)
    echo_cmd(subprocess.check_call, ('git', 'rev-parse', 'HEAD'), cwd=workspace)

@cli.group()
# git
@click.option('--target-remote'             , metavar='<url>')
@click.option('--target-ref'                , metavar='<ref>')
@click.option('--author-name'               , metavar='<name>'                 , help='''Name of change-request's author''')
@click.option('--author-email'              , metavar='<email>'                , help='''E-mail address of change-request's author''')
@click.option('--author-date'               , metavar='<date>', type=DateTime(), help='''Time of last update to the change-request''')
@click.option('--commit-date'               , metavar='<date>', type=DateTime(), help='''Time of starting to build this change-request''')
def prepare_source_tree(*args, **kwargs):
    pass

@prepare_source_tree.resultcallback()
@click.pass_context
def process_prepare_source_tree(
        ctx,
        change_applicator,
        target_remote,
        target_ref,
        author_name,
        author_email,
        author_date,
        commit_date,
    ):
    cfg = ctx.obj.get('cfg', {})
    workspace = ctx.obj['workspace']
    assert git_has_work_tree(workspace)

    echo_cmd(subprocess.check_call, ('git', 'fetch', target_remote, target_ref), cwd=workspace)
    echo_cmd(subprocess.check_call, ('git', 'checkout', '--force', 'FETCH_HEAD'), cwd=workspace)

    version = None

    version_info = cfg.get('change-request', {}).get('version', {})
    version_tag  = version_info.get('tag', False)
    if version_tag and not isinstance(version_tag, string_types):
        version_tag = '{version}'

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

    if'file' in version_info:
        version = bump_version(workspace, **version_info)

    echo_cmd(subprocess.check_call, (
            'git',
            'commit',
            '-m', msg,
        ),
        cwd=workspace,
        env=env,
        stdout=sys.stderr,
    )

    commit = echo_cmd(subprocess.check_output, ('git', 'rev-parse', 'HEAD'), cwd=workspace).strip()
    click.echo(commit)

    tagname = None
    if version is not None and not version.prerelease and version_tag:
        tagname = version_tag.format(
                version        = stringify_semver(*version),
                major          = version.major,
                minor          = version.minor,
                patch          = version.patch,
                prerelease     = '.'.join(version.prerelease),
                build          = '.'.join(version.build),
                prerelease_sep = ('-' if version.prerelease else ''),
                build_sep      = ('+' if version.build else ''),
            )
        echo_cmd(subprocess.check_call, ('git', 'tag', '-f', tagname, commit), cwd=workspace, stdout=sys.stderr)

    echo_cmd(subprocess.check_call, ('git', 'show', '--format=fuller', '--stat', commit), cwd=workspace, stdout=sys.stderr)
    click.echo('{commit}:{target_ref}'.format(commit=commit, target_ref=target_ref))
    if tagname is not None:
        click.echo('refs/tags/{tagname}:refs/tags/{tagname}'.format(**locals()))

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
    cfg = ctx.obj['cfg']
    modality_cmds = cfg.get('modality-source-preparation', {}).get(modality, ())
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

        volume_vars = ctx.obj.get('volume-vars', {})

        for cmd in modality_cmds:
            if isinstance(cmd, string_types):
                cmd = {"sh": cmd}

            try:
                desc = cmd['description']
            except KeyError:
                pass
            else:
                click.echo('Performing: ' + click.style(desc, fg='cyan'), err=True)

            try:
                args = shlex.split(cmd['sh'])
            except KeyError:
                pass
            else:
                env = os.environ.copy()
                while args:
                    m = _env_var_re.match(args[0])
                    if not m:
                        break
                    env[m.group('var')] = expand_vars(volume_vars, m.group('val'))
                    args.pop(0)

                args = [expand_vars(volume_vars, arg) for arg in args]
                echo_cmd(subprocess.check_call, args, cwd=workspace, env=env, stdout=sys.stderr)

            try:
                changed_files = cmd["changed-files"]
            except KeyError:
                pass
            else:
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
    cfg = ctx.obj['cfg']
    for phase in cfg['phases']:
        click.echo(phase)

@cli.command()
@click.option('--phase'             , metavar='<phase>'  , help='''Build phase to show variants for''')
@click.pass_context
def variants(ctx, phase):
    variants = []
    cfg = ctx.obj['cfg']
    for phasename, curphase in cfg['phases'].items():
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
    variants = []
    cfg = ctx.obj['cfg']
    info = {}
    for var in cfg['phases'][phase][variant]:
        if isinstance(var, string_types):
            continue
        for key, val in var.items():
            info[key] = expand_vars(ctx.obj.get('volume-vars', {}), val)
    click.echo(json.dumps(info))

@cli.command()
@click.option('--ref'               , metavar='<ref>'    , help='''Commit-ish that's checked out and to be built''')
@click.option('--phase'             , metavar='<phase>'  , help='''Build phase to execute''')
@click.option('--variant'           , metavar='<variant>', help='''Configuration variant to build''')
@click.pass_context
def build(ctx, ref, phase, variant):
    cfg = ctx.obj['cfg']
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
                            '-e', 'HOME=/home/sandbox',
                            '--tmpfs', '/home/sandbox:uid={},gid={}'.format(uid, gid),
                            '-u', '{}:{}'.format(uid, gid),
                            '-v', '/etc/passwd:/etc/passwd:ro',
                            '-v', '/etc/group:/etc/group:ro',
                            '-w', '/code',
                            '-v', '{WORKSPACE}:/code:rw'.format(**ctx.obj['volume-vars'])
                        ]
                    for volume in cfg['volumes']:
                        docker_run += ['-v', volume_spec_to_docker_param(volume)]
                    docker_run.append(image)
                    cmd = docker_run + cmd
                echo_cmd(subprocess.check_call, cmd)

@cli.command()
@click.option('--target-remote'     , metavar='<url>', required=True)
@click.option('--refspec'           , metavar='<ref>', required=True, multiple=True, help='''Refspecs that are to be submitted''')
@click.pass_context
def submit(ctx, target_remote, refspec):
    workspace = ctx.obj['workspace']
    assert git_has_work_tree(workspace)
    echo_cmd(subprocess.check_call, ('git', 'push', '--atomic', target_remote) + tuple(refspec), cwd=workspace)
