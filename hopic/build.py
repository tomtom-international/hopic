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
import subprocess
import sys
from configparser import (
    NoSectionError,
)
from pathlib import (
    PurePath,
)
from typing import (
    NamedTuple,
    Optional,
    Sequence,
    Tuple,
    Union,
)

import git

from .execution import echo_cmd_click as echo_cmd


log = logging.getLogger(__name__)


class FatalSignal(Exception):
    def __init__(self, signum):
        self.signal = signum


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


class HopicGitInfo(NamedTuple):
    submit_commit        : git.Commit
    submit_ref           : Optional[str] = None
    submit_remote        : Optional[str] = None
    refspecs             : Sequence[str] = ()
    target_commit        : Optional[git.Commit] = None
    source_commit        : Optional[git.Commit] = None
    autosquashed_commit  : Optional[git.Commit] = None
    source_commits       : Sequence[git.Commit] = ()
    autosquashed_commits : Sequence[git.Commit] = ()
    version_bumped       : Optional[bool] = None

    @classmethod
    def from_repo(cls, repo_ctx: Union[git.Repo, str, PurePath]) -> 'HopicGitInfo':
        if isinstance(repo_ctx, git.Repo):
            repo = repo_ctx
        else:
            repo = git.Repo(repo_ctx)

        try:
            submit_ref, version_bumped, target_commit, source_commit, autosquashed_commit = None, None, None, None, None
            submit_remote = None
            refspecs: Tuple[str, ...] = ()
            source_commits: Tuple[git.Commit, ...] = ()
            autosquashed_commits: Tuple[git.Commit, ...] = ()

            submit_commit = repo.head.commit
            section = f"hopic.{submit_commit}"
            with repo.config_reader() as git_cfg:
                try:
                    # Determine remote ref for current commit
                    submit_ref = git_cfg.get(section, 'ref', fallback=None)
                    submit_remote = git_cfg.get(section, "remote", fallback=None)

                    version_bumped = git_cfg.getboolean(section, "version-bumped", fallback=None)

                    if git_cfg.has_option(section, 'refspecs'):
                        refspecs = tuple(shlex.split(git_cfg.get_value(section, 'refspecs')))

                    if git_cfg.has_option(section, 'target-commit'):
                        target_commit = repo.commit(git_cfg.get_value(section, 'target-commit'))
                    if git_cfg.has_option(section, 'source-commit'):
                        source_commit = repo.commit(git_cfg.get_value(section, 'source-commit'))
                    if git_cfg.has_option(section, 'autosquashed-commit'):
                        autosquashed_commit = repo.commit(git_cfg.get_value(section, 'autosquashed-commit'))

                    if target_commit and source_commit:
                        source_commits = tuple(git.Commit.list_items(
                            repo,
                            f"{target_commit}..{source_commit}",
                            first_parent=True,
                            no_merges=True,
                        ))
                        autosquashed_commits = source_commits
                        log.debug('Building for source commits: %s', source_commits)
                    if target_commit and autosquashed_commit:
                        autosquashed_commits = tuple(git.Commit.list_items(
                            repo,
                            f"{target_commit}..{autosquashed_commit}",
                            first_parent=True,
                            no_merges=True,
                        ))
                except NoSectionError:
                    pass
        finally:
            if not isinstance(repo_ctx, git.Repo):
                repo.close()

        return cls(
            submit_commit        = submit_commit,         # noqa: E251 "unexpected spaces around '='"
            submit_ref           = submit_ref,            # noqa: E251 "unexpected spaces around '='"
            submit_remote        = submit_remote,         # noqa: E251 "unexpected spaces around '='"
            refspecs             = refspecs,              # noqa: E251 "unexpected spaces around '='"
            source_commits       = source_commits,        # noqa: E251 "unexpected spaces around '='"
            autosquashed_commits = autosquashed_commits,  # noqa: E251 "unexpected spaces around '='"
            target_commit        = target_commit,         # noqa: E251 "unexpected spaces around '='"
            source_commit        = source_commit,         # noqa: E251 "unexpected spaces around '='"
            autosquashed_commit  = autosquashed_commit,   # noqa: E251 "unexpected spaces around '='"
            version_bumped       = version_bumped,        # noqa: E251 "unexpected spaces around '='"
        )

    @property
    def has_change(self) -> bool:
        return bool(self.refspecs)


def volume_spec_to_docker_param(volume):
    if not os.path.exists(volume['source']):
        os.makedirs(volume['source'])
    param = '{source}:{target}'.format(**volume)
    try:
        param = param + ':' + ('ro' if volume['read-only'] else 'rw')
    except KeyError:
        pass
    return param
