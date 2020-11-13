# Copyright (c) 2018 - 2020 TomTom N.V.
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

import logging
import os
import re
import shlex
import signal
import stat
import subprocess
import tempfile
import urllib.parse
from collections.abc import (
    Mapping,
)
from copy import copy

import click
import git

from . import (
    autocomplete,
    extensions,
)
from .utils import (
    is_publish_branch,
)

from .. import (
    credentials,
    binary_normalize,
)
from ..build import (
    FatalSignal,
    DockerContainers,
    volume_spec_to_docker_param,
    HopicGitInfo,
)
from ..config_reader import (
    RunOnChange,
    expand_docker_volume_spec,
    expand_vars,
)
from ..errors import (
    MissingCredentialVarError,
)
from ..execution import echo_cmd_click as echo_cmd
from ..git_time import (
    restore_mtime_from_git,
    to_git_time,
)


log = logging.getLogger(__name__)
_env_var_re = re.compile(r'^(?P<var>[A-Za-z_][0-9A-Za-z_]*)=(?P<val>.*)$')


@click.command()
@click.option('--phase'  , metavar='<phase>'  , multiple=True, help='''Build phase to execute''', autocompletion=autocomplete.phase_from_config)
@click.option('--variant', metavar='<variant>', multiple=True, help='''Configuration variant to build''', autocompletion=autocomplete.variant_from_config)
@click.option('--dry-run', '-n',  is_flag=True, default=False, help='''Print commands from the configured phases and variants, but do not execute them''')
@click.pass_context
def build(ctx, phase, variant, dry_run):
    """
    Build for the specified commit.

    This defaults to building all variants for all phases.
    It's possible to limit building to either all variants for a single phase, all phases for a single variant or a
    single variant for a single phase.
    """
    # Ensure any required extensions are available
    extensions.install_extensions.callback()

    ctx.obj.dry_run = dry_run
    if dry_run:
        log.info('[dry-run] would execute:')
    cfg = ctx.obj.config

    hopic_git_info = HopicGitInfo.from_repo(ctx.obj.workspace)
    refspecs = list(hopic_git_info.refspecs)

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
            volume_vars['GIT_COMMIT'] = str(hopic_git_info.submit_commit)
            if hopic_git_info.submit_ref is not None:
                volume_vars['GIT_BRANCH'] = hopic_git_info.submit_ref

            artifacts = []
            with DockerContainers() as volumes_from:
                # If the branch is not allowed to publish, skip the publish phase. If run_on_change is set to 'always', phase will be run anyway regardless of
                # this condition. For build phase, run_on_change is set to 'always' by default, so build will always happen.
                is_publish_allowed = is_publish_branch(ctx)
                volumes = cfg['volumes'].copy()
                for cmd in cmds:
                    worktrees = {}
                    foreach = None

                    assert isinstance(cmd, Mapping)

                    run_on_change = cmd.get('run-on-change', RunOnChange.default)
                    if run_on_change == RunOnChange.always:
                        pass
                    elif run_on_change == RunOnChange.never:
                        if hopic_git_info.has_change:
                            break
                    elif run_on_change in (RunOnChange.only, RunOnChange.new_version_only):
                        if not hopic_git_info.has_change:
                            break
                        if not is_publish_allowed:
                            break
                        if run_on_change == RunOnChange.new_version_only and ctx.obj.version.prerelease:
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

                    if not dry_run:
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
                                                                   ctx.obj.volume_vars, cmd['volumes'],
                                                                   add_defaults=False)
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
                        with_credentials = cmd['with-credentials']
                    except (KeyError, TypeError):
                        pass
                    else:
                        for creds in with_credentials:
                            if 'project-name' in cfg and creds['type'] == 'username-password' and not (
                                    creds['username-variable'] in ctx.obj.volume_vars
                                    and creds['password-variable'] in ctx.obj.volume_vars):
                                kcred = credentials.get_credential_by_id(cfg['project-name'], creds['id'])
                                if kcred is not None:
                                    username, password = kcred
                                    if creds['encoding'] == 'url':
                                        username = urllib.parse.quote_plus(username)
                                        password = urllib.parse.quote_plus(password)
                                    volume_vars.update({
                                        creds['username-variable']: username,
                                        creds['password-variable']: password,
                                    })

                            cred_vars = {name for key, name in creds.items() if key.endswith('-variable')}
                            for cred_var in cred_vars:
                                if cred_var in volume_vars:
                                    continue
                                volume_vars[cred_var] = MissingCredentialVarError(creds['id'], cred_var)

                    try:
                        cmd = cmd['sh']
                    except KeyError:
                        continue

                    volume_vars['WORKSPACE'] = '/code' if image is not None else ctx.obj.code_dir

                    env = (dict(
                        HOME            = '/home/sandbox',              # noqa: E251 "unexpected spaces around '='"
                        _JAVA_OPTIONS   = '-Duser.home=/home/sandbox',  # noqa: E251 "unexpected spaces around '='"
                    ) if image is not None else {})

                    for varname in cfg['pass-through-environment-vars']:
                        if varname in os.environ:
                            env.setdefault(varname, os.environ[varname])

                    for varname in (
                                'SOURCE_DATE_EPOCH',
                                'VERSION',
                                'PURE_VERSION',
                                'DEBVERSION',
                            ):
                        if varname in ctx.obj.volume_vars:
                            env[varname] = ctx.obj.volume_vars[varname]

                    foreach_items = (None,)
                    if foreach == 'SOURCE_COMMIT':
                        foreach_items = hopic_git_info.source_commits
                    elif foreach == 'AUTOSQUASHED_COMMIT':
                        foreach_items = hopic_git_info.autosquashed_commits

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
                        cidfile = None
                        try:
                            if image is not None:
                                uid, gid = os.getuid(), os.getgid()
                                fd, cidfile = tempfile.mkstemp(prefix='hopic-docker-run-cid-', suffix='.txt')
                                os.close(fd)
                                # Docker wants this file to not exist (yet) when starting a container
                                os.unlink(cidfile)
                                docker_run = ['docker', 'run',
                                              '--rm',
                                              f"--cidfile={cidfile}",
                                              '--net=host',
                                              '--tty',
                                              '--cap-add=SYS_PTRACE',
                                              f"--tmpfs={env['HOME']}:exec,uid={uid},gid={gid}",
                                              f"--user={uid}:{gid}",
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
                            except FatalSignal as signal_exc:
                                if cidfile and os.path.isfile(cidfile):
                                    # If we're being signalled to shut down ensure the spawned docker container also gets cleaned up.
                                    with open(cidfile) as f:
                                        cid = f.read()
                                    try:
                                        # Will also remove the container due to the '--rm' it was started with.
                                        echo_cmd(subprocess.check_call, ('docker', 'stop', cid))
                                    except subprocess.CalledProcessError as e:
                                        log.error(
                                                'Could not stop Docker container (maybe it was stopped already?), command failed with exit code %d',
                                                e.returncode)
                                ctx.exit(128 + signal_exc.signal)
                            for num, old_handler in old_handlers.items():
                                signal.signal(num, old_handler)
                        finally:
                            if cidfile:
                                try:
                                    os.unlink(cidfile)
                                except FileNotFoundError:
                                    pass

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
                                    if not diff.deleted_file:
                                        add_files.add(diff.b_path)
                                    remove_files.add(diff.a_path)
                                remove_files -= add_files
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
                                        message     = commit_message,                          # noqa: E251 "unexpected spaces around '='"
                                        author      = parent.author,                           # noqa: E251 "unexpected spaces around '='"
                                        committer   = parent.committer,                        # noqa: E251 "unexpected spaces around '='"
                                        author_date = to_git_time(parent.authored_datetime),   # noqa: E251 "unexpected spaces around '='"
                                        commit_date = to_git_time(parent.committed_datetime),  # noqa: E251 "unexpected spaces around '='"
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
