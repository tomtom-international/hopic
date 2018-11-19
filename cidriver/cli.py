import click

from .config_reader import read as read_config
from datetime import datetime
from dateutil.parser import parse as date_parse
from dateutil.tz import (tzoffset, tzlocal, tzutc)
import json
import os
import re
import shlex
from six import string_types
import subprocess

try:
    from shlex import quote as shquote
except ImportError:
    from pipes import quote as shquote

try:
    from cStringIO import StringIO
except ImportError:
    from StringIO import StringIO

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
                    tz = tzutc()
                return datetime.fromtimestamp(float(stamp.group('utcstamp')), tz)

            dt = date_parse(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tzlocal())
            return dt
        except ValueError as e:
            self.fail('Could not parse datetime string "{value}": {e}'.format(value=value, e=' '.join(e.args)), param, ctx)

def echo_cmd(fun, cmd, *args, **kwargs):
  click.echo('Executing: ' + click.style(' '.join(shquote(word) for word in cmd), fg='yellow'), err=True)
  try:
    return fun(cmd, *args, **kwargs)
  except Exception as e:
    if hasattr(e, 'child_traceback'):
      click.echo("Child traceback: {}".format(e.child_traceback), err=True)
    raise

def git_has_work_tree(workspace):
  if not os.path.isdir(os.path.join(workspace, '.git')):
    return False
  try:
    output = echo_cmd(subprocess.check_output, ('git', 'rev-parse', '--is-inside-work-tree'), cwd=workspace, env={'LANG': 'C'})
  except subprocess.CalledProcessError:
    return False
  return output.strip().lower() == 'true'

_semver_re = re.compile(r'^(?:version=)?(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)\.(?P<patch>0|[1-9][0-9]*)(?:-(?P<prerelease>[-0-9a-zA-Z]+(?:\.[-0-9a-zA-Z])*))?(?:\+(?P<build>[-0-9a-zA-Z]+(?:\.[-0-9a-zA-Z])*))?$')
def parse_semver(s):
    m = _semver_re.match(s)
    if not m:
        return None

    major, minor, patch, prerelease, build = m.groups()

    major, minor, patch = int(major), int(minor), int(patch)

    if prerelease is None:
        prerelease = ()
    else:
        prerelease = tuple(prerelease.split('.'))

    if build is None:
        build = ()
    else:
        build = tuple(build.split('.'))

    return (major, minor, patch, prerelease, build)

def stringify_semver(major, minor, patch, prerelease, build):
    ver = '.'.join(str(x) for x in (major, minor, patch))
    if prerelease:
        ver += '-' + '.'.join(prerelease)
    if build:
        ver += '+' + '.'.join(build)
    return ver

def bump_version(workspace, file, format='semver', bump='patch', **_):
    version = None
    new_content = StringIO()
    with open(file, 'r') as f:
        for l in f:
            ver = None
            if format == 'semver':
                ver = parse_semver(l)
            if ver is None:
                new_content.write(l)
                continue

            assert version is None, "multiple versions are not supported"
            version = ver

            if bump:
                major, minor, patch, prerelease, build = version

                if bump == 'patch':
                    if not prerelease:
                        patch += 1
                elif bump == 'minor':
                    if not (prerelease and patch == 0):
                        minor += 1
                    patch = 0
                elif bump == 'major':
                    if not (prerelease and minor == 0 and patch == 0):
                        major += 1
                    major = 0
                    minor = 0
                else:
                    click.echo("Invalid version bumping target: {bump}".format(**locals()), err=True)
                    sys.exit(1)

                # When bumping the prerelease tags need to be dropped always
                prerelease, build = (), ()

                version = (major, minor, patch, prerelease, build)

                # Replace version in source line
                m = _semver_re.match(l)
                new_line = l[:m.start(1)] + stringify_semver(*version) + l[m.end(m.lastgroup)]
                new_content.write(new_line)

    if bump:
        assert version is not None, "no version found"
        with open(file, 'w') as f:
            f.write(new_content.getvalue())
        echo_cmd(subprocess.check_call, ('git', 'add', file), cwd=workspace)

    return (stringify_semver(*version) if version is not None else version)

_var_re = re.compile(r'\$(?:(\w+)|\{([^}]+)\})')
def expand_vars(vars, expr):
    if isinstance(expr, string_types):
        # Expand variables from our "virtual" environment
        last_idx = 0
        new_val = expr[:last_idx]
        for var in _var_re.finditer(expr):
            name = var.group(1) or var.group(2)
            value = vars[name]
            new_val = new_val + expr[last_idx:var.start()] + value
            last_idx = var.end()

        new_val = new_val + expr[last_idx:]
        return new_val
    if hasattr(expr, 'items'):
        expr = expr.copy()
        for key, val in expr.items():
            expr[key] = expand_vars(vars, expr[key])
        return expr
    return [expand_vars(vars, val) for val in expr]

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
@click.option('--config', type=click.Path(exists=True, readable=True, resolve_path=True))
@click.option('--workspace', type=click.Path(exists=True, file_okay=False, dir_okay=True))
@click.option('--dependency-manifest', type=click.File('r'))
@click.pass_context
def cli(ctx, config, workspace, dependency_manifest):
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

    # Fallback to 'dependency_manifest.xml' file in same directory as config
    manifest = (dependency_manifest if dependency_manifest
            else (os.path.join(workspace or config_dir, 'dependency_manifest.xml')))

    if config is not None:
        ctx.obj['cfg'] = read_config(config, manifest, volume_vars)

@cli.command('checkout-source-tree')
@click.option('--target-remote'     , metavar='<url>')
@click.option('--target-ref'        , metavar='<ref>')
@click.option('--clean/--no-clean'  , default=False, help='''Clean workspace of non-tracked files''')
@click.pass_context
def checkout_source_tree(ctx, target_remote, target_ref, clean):
    workspace = ctx.obj['workspace']
    if not git_has_work_tree(workspace):
        echo_cmd(subprocess.check_call, ('git', 'clone', target_remote, workspace))
    echo_cmd(subprocess.check_call, ('git', 'fetch', target_remote, target_ref), cwd=workspace)
    echo_cmd(subprocess.check_call, ('git', 'checkout', '--force', 'FETCH_HEAD'), cwd=workspace)
    if clean:
      click.echo(echo_cmd(subprocess.check_output, ('git', '-c', 'color.ui=always', 'clean', '--force', '-xd'), cwd=workspace), err=True, nl=False)
    echo_cmd(subprocess.check_call, ('git', 'rev-parse', 'HEAD'), cwd=workspace)

@cli.command('prepare-source-tree')
# git
@click.option('--target-remote'             , metavar='<url>')
@click.option('--target-ref'                , metavar='<ref>')
@click.option('--source-remote'             , metavar='<url>', help='<source> remote to merge into <target>')
@click.option('--source-ref'                , metavar='<ref>', help='ref of <source> remote to merge into <target>')
@click.option('--change-request'            , metavar='<identifier>'           , help='Identifier of change-request to use in merge commit message')
@click.option('--change-request-title'      , metavar='<title>'                , help='''Change request title to incorporate in merge commit's subject line''')
@click.option('--change-request-description', metavar='<description>'          , help='''Change request title to incorporate in merge commit's subject line''')
@click.option('--author-name'               , metavar='<name>'                 , help='''Name of change-request's author''')
@click.option('--author-email'              , metavar='<email>'                , help='''E-mail address of change-request's author''')
@click.option('--author-date'               , metavar='<date>', type=DateTime(), help='''Time of last update to the change-request''')
@click.option('--commit-date'               , metavar='<date>', type=DateTime(), help='''Time of starting to build this change-request''')
@click.pass_context
def prepare_source_tree(
        ctx,
        target_remote,
        target_ref,
        source_remote,
        source_ref,
        change_request,
        change_request_title,
        change_request_description,
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
    echo_cmd(subprocess.check_call, ('git', 'fetch', source_remote, source_ref), cwd=workspace)

    click.echo(echo_cmd(subprocess.check_output, (
            'git', '-c', 'color.ui=always',
            'merge',
            '--no-ff',
            '--no-commit',
            'FETCH_HEAD',
        ),
        cwd=workspace), err=True, nl=False)

    version = None

    version_info = cfg.get('change-request', {}).get('version', {})
    version_tag  = version_info.get('tag', False)
    if 'file' in version_info:
        version = bump_version(workspace, **version_info)

    env = os.environ.copy()
    if author_name is not None:
        env['GIT_AUTHOR_NAME'] = author_name
    if author_email is not None:
        env['GIT_AUTHOR_EMAIL'] = author_email
    if author_date is not None:
        env['GIT_AUTHOR_DATE'] = author_date.strftime('%Y-%m-%d %H:%M:%S.%f %z')
    if commit_date is not None:
        env['GIT_COMMITTER_DATE'] = commit_date.strftime('%Y-%m-%d %H:%M:%S.%f %z')
    msg = "Merge #{}".format(change_request)
    if change_request_title is not None:
        msg = "{msg}: {title}".format(msg=msg, title=change_request_title)
    if change_request_description is not None:
        msg = "{msg}\n\n{description}".format(msg=msg, description=change_request_description)

    click.echo(echo_cmd(subprocess.check_output, (
            'git', '-c', 'color.ui=always',
            'commit',
            '-m', msg,
        ),
        cwd=workspace,
        env=env), err=True, nl=False)
    commit = echo_cmd(subprocess.check_output, ('git', 'rev-parse', 'HEAD'), cwd=workspace).strip()

    if version is not None and version_tag:
        click.echo(echo_cmd(subprocess.check_output, ('git', '-c', 'color.ui=always', 'tag', '-f', version, commit), cwd=workspace), err=True, nl=False)

    click.echo(echo_cmd(subprocess.check_output, ('git', '-c', 'color.ui=always', 'show', '--format=fuller', '--stat', commit), cwd=workspace), err=True, nl=False)
    click.echo('{commit}:{target_ref}'.format(commit=commit, target_ref=target_ref))
    if version is not None and version_tag:
        click.echo('refs/tags/{version}:refs/tags/{version}'.format(**locals()))

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
