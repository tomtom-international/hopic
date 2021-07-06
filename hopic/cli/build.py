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

from datetime import datetime
import logging
import os
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
from collections.abc import (
    Mapping,
    Sequence,
)

import click
from dateutil.tz import tzutc
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
    CredentialEncoding,
    CredentialType,
    RunOnChange,
    expand_docker_volume_spec,
    expand_vars,
)
from ..errors import (
    MissingCredentialVarError,
    MissingFileError,
    StepTimeoutExpiredError,
    UnknownPhaseError,
)
from ..execution import echo_cmd_click as echo_cmd
from ..git_time import (
    restore_mtime_from_git,
    to_git_time,
)
from .global_obj import initialize_global_variables_from_config

log = logging.getLogger(__name__)


@click.pass_context
def build_variant(ctx, variant, cmds, hopic_git_info):
    cfg = ctx.obj.config

    images = cfg['image']
    try:
        image = images[variant]
    except KeyError:
        image = images.get('default', None)

    docker_in_docker = False

    volume_vars = ctx.obj.volume_vars.copy()
    # Give commands executing inside a container image a different view than outside
    volume_vars['GIT_COMMIT'] = str(hopic_git_info.submit_commit)
    if hopic_git_info.submit_ref is not None:
        volume_vars['GIT_BRANCH'] = hopic_git_info.submit_ref
    git_commit_time = hopic_git_info.submit_commit.committed_datetime
    # RFC 3339 formatted (strict subset of ISO 8601)
    volume_vars["GIT_COMMIT_TIME"] = f"{git_commit_time:%Y-%m-%dT%H:%M:%S.%f%z}"

    volume_vars["BUILD_NAME"] = os.environ.get("BUILD_NAME", "unknown")
    volume_vars["BUILD_NUMBER"] = os.environ.get("BUILD_NUMBER", "NaN")
    volume_vars["BUILD_URL"] = os.environ.get("BUILD_URL", "")

    # Hack to represent an empty commit list
    volume_vars["SOURCE_COMMITS"] = volume_vars["AUTOSQUASHED_COMMITS"] = "HEAD..HEAD"
    if hopic_git_info.target_commit and hopic_git_info.source_commit:
        volume_vars["AUTOSQUASHED_COMMITS"] = volume_vars["SOURCE_COMMITS"] = f"{hopic_git_info.target_commit}..{hopic_git_info.source_commit}"
    if hopic_git_info.target_commit and hopic_git_info.autosquashed_commit:
        volume_vars["AUTOSQUASHED_COMMITS"] = f"{hopic_git_info.target_commit}..{hopic_git_info.autosquashed_commit}"

    mandatory_artifacts = []
    mandatory_junit = []
    optional_artifacts = []
    worktree_commits = {}
    variant_credentials = {}
    extra_docker_run_args = []
    with DockerContainers() as volumes_from:
        # If the branch is not allowed to publish, skip the publish phase. If run_on_change is set to 'always', phase will be run anyway regardless of
        # this condition. For build phase, run_on_change is set to 'always' by default, so build will always happen.
        is_publish_allowed = is_publish_branch(ctx, hopic_git_info)
        volumes = cfg['volumes'].copy()
        global_timeout = None
        global_timeout_expire_time = None
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
                    log.debug("No change detected for %r", hopic_git_info)
                    break
                if not is_publish_allowed:
                    log.debug("Not allowed to publish for %r", hopic_git_info)
                    break
                if run_on_change == RunOnChange.new_version_only and not hopic_git_info.version_bumped:
                    log.debug("No version change detected for %r", hopic_git_info)
                    break
            try:
                desc = cmd['description']
            except (KeyError, TypeError):
                pass
            else:
                log.info('Performing: %s', click.style(desc, fg='cyan'))

            monotonic_now = time.monotonic()
            if "sh" not in cmd and "timeout" in cmd:
                assert global_timeout is None, "there should only be a single global per-variant timeout and config_reader should have enforced that"
                global_timeout = cmd["timeout"]
                global_timeout_expire_time = monotonic_now + global_timeout
                log.debug("restricting all commands combined to a maximum time of %f seconds", cmd["timeout"])

            timeout = None
            if "sh" in cmd:
                if "timeout" in cmd:
                    timeout = cmd["timeout"]
                global_timeout_remainder = None
                if global_timeout_expire_time is not None:
                    global_timeout_remainder = global_timeout_expire_time - monotonic_now
                    if global_timeout_remainder <= 0:
                        raise StepTimeoutExpiredError(global_timeout, cmd=" ".join(cmd["sh"]), before=True)

                if global_timeout_remainder is not None and timeout is None:
                    timeout = global_timeout_remainder
                    log.debug("restricting current command to a maximum of %f seconds remaining from global timeout", timeout)
                elif global_timeout_remainder is not None and timeout > global_timeout_remainder:
                    log.debug(
                        "restricting current command to a maximum of %f seconds remaining from global timeout (clamped from %f)",
                        global_timeout_remainder,
                        timeout,
                    )
                elif timeout is not None:
                    log.debug("restricting current command to a maximum of %f seconds", timeout)

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
                    opt = cmd[artifact_key]
                except (KeyError, TypeError):
                    pass
                else:
                    if artifact_key == "junit":
                        new_artifacts = opt["test-results"]
                    else:
                        new_artifacts = (artifact["pattern"].replace("(*)", "*") for artifact in opt["artifacts"])
                    if opt["allow-missing"]:
                        optional_artifacts.extend(new_artifacts)
                    else:
                        mandatory_artifacts.extend(new_artifacts)
            try:
                junit = cmd["junit"]
            except (KeyError, TypeError):
                pass
            else:
                if not junit["allow-missing"]:
                    mandatory_junit.extend(junit["test-results"])

            if not ctx.obj.dry_run:
                try:
                    worktrees = cmd['worktrees']
                except KeyError:
                    pass
                else:
                    # Force clean builds when we don't know how to discover changed files
                    for subdir, worktree in worktrees.items():
                        if worktree["changed-files"] is None:
                            with git.Repo(ctx.obj.workspace / subdir) as repo:
                                clean_output = repo.git.clean(subdir, x=True, d=True, force=True)
                                if clean_output:
                                    log.info('%s', clean_output)

            try:
                foreach = cmd['foreach']
            except KeyError:
                pass

            try:
                scoped_volumes = expand_docker_volume_spec(ctx.obj.config_dir,
                                                           ctx.obj.volume_vars, cmd['volumes'],
                                                           add_defaults=False)
                volumes.update(scoped_volumes)
            except KeyError:
                pass

            try:
                image = cmd['image']
            except KeyError:
                pass

            cmd_extra_docker_run_args = cmd.get('extra-docker-args', '')
            if cmd_extra_docker_run_args:
                if not image:
                    log.warning('`extra-docker-args` has no effect if no Docker image is configured')
                else:
                    for arg in cmd_extra_docker_run_args:
                        value = cmd_extra_docker_run_args[arg]
                        if isinstance(value, bool):
                            if not value:
                                log.warning('A "False" value for an `extra-docker-args` argument has no meaning and will be ignored')
                            else:
                                extra_docker_run_args.append(f'--{arg}')
                        else:
                            if (isinstance(value, Sequence) and not isinstance(value, str)):
                                extra_docker_run_args.extend((f'--{arg}={v}' for v in value))
                            else:
                                extra_docker_run_args.append(f'--{arg}={value}')

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
                    if 'project-name' in cfg and creds['type'] == CredentialType.username_password and not (
                            creds['username-variable'] in ctx.obj.volume_vars
                            and creds['password-variable'] in ctx.obj.volume_vars):
                        if ctx.obj.dry_run:
                            # Use dummy values for dry runs instead of (potentially) asking the user
                            kcred = (f'user_{id}', f'pass_{id}')
                        else:
                            kcred = credentials.get_credential_by_id(cfg['project-name'], creds['id'])

                        if kcred is not None:
                            username, password = kcred
                            if creds['encoding'] == CredentialEncoding.url:
                                username = urllib.parse.quote(username, safe='')
                                password = urllib.parse.quote(password, safe='')
                            volume_vars.update({
                                creds['username-variable']: username,
                                creds['password-variable']: password,
                            })

                    cred_vars = {name for key, name in creds.items() if key.endswith('-variable')}
                    for cred_var in cred_vars:
                        if cred_var in volume_vars:
                            if not isinstance(volume_vars[cred_var], MissingCredentialVarError):
                                variant_credentials[cred_var] = volume_vars[cred_var]
                        else:
                            volume_vars[cred_var] = MissingCredentialVarError(creds['id'], cred_var)

            try:
                cmd_env = cmd['environment']
                cmd = cmd['sh']
            except KeyError:
                continue

            volume_vars['WORKSPACE'] = '/code' if image is not None else str(ctx.obj.code_dir)

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
                        'PUBLISH_VERSION'
                    ):
                if varname in ctx.obj.volume_vars and not isinstance(ctx.obj.volume_vars[varname], BaseException):
                    env[varname] = ctx.obj.volume_vars[varname]

            env.update(variant_credentials)
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
                try:
                    # Be compatible with reproducible-builds.org
                    now = datetime.utcfromtimestamp(float(os.environ["SOURCE_DATE_EPOCH"])).replace(tzinfo=tzutc())
                except (KeyError, TypeError):
                    now = datetime.utcnow().replace(tzinfo=tzutc())
                duration = now - git_commit_time
                cfg_vars["BUILD_DURATION"] = f"{duration.total_seconds():.6f}"

                final_env = env.copy()
                for k, v in cmd_env.items():
                    if v is None and k in final_env:
                        del final_env[k]
                    else:
                        final_env[k] = expand_vars(cfg_vars, v)
                final_cmd = [expand_vars(cfg_vars, arg) for arg in cmd]

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
                                      '--cap-add=SYS_PTRACE',
                                      f"--tmpfs={final_env['HOME']}:exec,uid={uid},gid={gid}",
                                      f"--user={uid}:{gid}",
                                      '--workdir=/code',
                                      ] + [
                                          f"--env={k}={v}" for k, v in final_env.items()
                                      ]

                        if all(hasattr(fd, 'isatty') and fd.isatty() for fd in [sys.stderr, sys.stdout, sys.stdin]):
                            docker_run += ['--tty']

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

                        docker_run += extra_docker_run_args
                        docker_run.append(str(image))
                        final_cmd = docker_run + final_cmd
                    new_env = os.environ.copy()
                    if image is None:
                        new_env.update(final_env)
                        for k, v in cmd_env.items():
                            if v is None and k in new_env:
                                del new_env[k]

                    def signal_handler(signum, frame):
                        log.warning('Received fatal signal %d', signum)
                        raise FatalSignal(signum)

                    old_handlers = dict((num, signal.signal(num, signal_handler)) for num in (signal.SIGINT, signal.SIGTERM))
                    try:
                        echo_cmd(subprocess.check_call, final_cmd, env=new_env, cwd=ctx.obj.code_dir, obfuscate=variant_credentials, timeout=timeout)
                    except subprocess.CalledProcessError as e:
                        log.error("Command fatally terminated with exit code %d", e.returncode)
                        ctx.exit(e.returncode)
                    except (FatalSignal, subprocess.TimeoutExpired) as exc:
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
                            except FatalSignal:
                                # Ignore INT and TERM while sending KILL to the Docker container
                                signal.signal(signal.SIGINT, signal.SIG_IGN)
                                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                                log.warning('Interrupted while stopping Docker container; killing..')
                                echo_cmd(subprocess.check_call, ('docker', 'kill', cid))
                        if isinstance(exc, FatalSignal):
                            ctx.exit(128 + exc.signal)
                        else:
                            assert isinstance(exc, subprocess.TimeoutExpired)
                            raise StepTimeoutExpiredError(timeout, cmd=" ".join(cmd))
                    for num, old_handler in old_handlers.items():
                        signal.signal(num, old_handler)
                finally:
                    if cidfile:
                        try:
                            os.unlink(cidfile)
                        except FileNotFoundError:
                            pass

            for subdir, worktree in worktrees.items():
                with git.Repo(ctx.obj.workspace / subdir) as repo:
                    worktree_commits.setdefault(subdir, [
                        str(repo.head.commit),
                        str(repo.head.commit),
                    ])

                    changed_files = worktree["changed-files"]
                    if changed_files is not None:
                        repo.index.add(expand_vars(volume_vars, f) for f in changed_files)
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
                    submit_commit = repo.index.commit(
                            message     = commit_message,                                                # noqa: E251 "unexpected spaces around '='"
                            author      = hopic_git_info.submit_commit.author,                           # noqa: E251 "unexpected spaces around '='"
                            committer   = hopic_git_info.submit_commit.committer,                        # noqa: E251 "unexpected spaces around '='"
                            author_date = to_git_time(hopic_git_info.submit_commit.authored_datetime),   # noqa: E251 "unexpected spaces around '='"
                            commit_date = to_git_time(hopic_git_info.submit_commit.committed_datetime),  # noqa: E251 "unexpected spaces around '='"
                        )
                    restore_mtime_from_git(repo)
                    worktree_commits[subdir][1] = str(submit_commit)
                    log.info('%s', repo.git.show(submit_commit, format='fuller', stat=True))

        if worktree_commits:
            with git.Repo(ctx.obj.workspace) as repo, repo.config_writer() as git_cfg:
                submit_commit = repo.head.commit
                section = f"hopic.{submit_commit}"
                if git_cfg.has_option(section, 'refspecs'):
                    refspecs = list(shlex.split(git_cfg.get_value(section, 'refspecs')))
                else:
                    refspecs = []

                bundle_commits = []
                for subdir, (base_commit, submit_commit) in worktree_commits.items():
                    worktree_ref = ctx.obj.config['scm']['git']['worktrees'][subdir]
                    if worktree_ref in repo.heads:
                        repo.heads[worktree_ref].set_commit(submit_commit, logmsg='Prepare for git-bundle')
                    else:
                        repo.create_head(worktree_ref, submit_commit)
                    bundle_commits.append(f"{base_commit}..{worktree_ref}")
                    refspecs.append(f"{submit_commit}:{worktree_ref}")
                repo.git.bundle('create', ctx.obj.workspace / 'worktree-transfer.bundle', *bundle_commits)

                git_cfg.set_value(section, 'refspecs', ' '.join(shlex.quote(refspec) for refspec in refspecs))

        # Post-processing to make these artifacts as reproducible as possible
        for artifact_pattern in optional_artifacts:
            for artifact in ctx.obj.code_dir.glob(artifact_pattern):
                binary_normalize.normalize(artifact, source_date_epoch=ctx.obj.source_date_epoch)

        pattern_matched = False
        mandatory_artifacts = [expand_vars(volume_vars, exp) for exp in mandatory_artifacts]
        for pattern in mandatory_artifacts:
            for artifact in ctx.obj.code_dir.glob(pattern):
                pattern_matched = True
                binary_normalize.normalize(artifact, source_date_epoch=ctx.obj.source_date_epoch)
        if mandatory_artifacts and not pattern_matched:
            raise MissingFileError(f"none of these mandatory artifact patterns matched a file: {mandatory_artifacts}")

        pattern_matched = False
        mandatory_junit = [expand_vars(volume_vars, exp) for exp in mandatory_junit]
        for pattern in mandatory_junit:
            for _ in ctx.obj.code_dir.glob(pattern):
                pattern_matched = True
                break
        if mandatory_junit and not pattern_matched:
            raise MissingFileError(f"none of these mandatory junit patterns matched a file: {mandatory_junit}")


@click.command()
@click.option('--phase'  , '-p', metavar='<phase>'  , multiple=True, help='''Build phase to execute''', autocompletion=autocomplete.phase_from_config)
@click.option('--variant', '-v', metavar='<variant>', multiple=True, help='''Configuration variant to build''', autocompletion=autocomplete.variant_from_config)
@click.option('--dry-run', '-n', is_flag=True, default=False, help='''Print commands from the configured phases and variants, but do not execute them''')
@click.pass_context
def build(ctx, phase, variant, dry_run):
    """
    Build for the specified commit.

    This defaults to building all variants for all phases.
    It's possible to limit building to either all variants for a single phase, all phases for a single variant or a
    single variant for a single phase.
    """
    # Ensure any required extensions are available
    initialize_global_variables_from_config(extensions.install_extensions.callback())

    ctx.obj.dry_run = dry_run
    if dry_run:
        log.info('[dry-run] would execute:')

    hopic_git_info = HopicGitInfo.from_repo(ctx.obj.workspace)

    unknown_phases = [phasename for phasename in phase if phasename not in ctx.obj.config['phases']]
    if unknown_phases:
        raise UnknownPhaseError(phase=unknown_phases)

    for phasename, curphase in ctx.obj.config['phases'].items():
        if phase and phasename not in phase:
            continue

        for var in variant:
            if var not in curphase:
                log.warning(f"phase '{phasename}' does not contain variant '{var}'")

        for curvariant, cmds in curphase.items():
            if variant and curvariant not in variant:
                continue

            build_variant(variant=curvariant, cmds=cmds, hopic_git_info=hopic_git_info)
