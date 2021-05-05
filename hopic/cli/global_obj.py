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
from pathlib import Path

import click
from dateutil.tz import tzutc
import git

from configparser import (
        NoOptionError,
        NoSectionError,
    )
from . import utils
from ..errors import VersioningError
from ..git_time import (
    determine_source_date,
    determine_version,
)
from ..versioning import SemVer

log = logging.getLogger(__name__)


@click.pass_context
def initialize_global_variables_from_config(ctx, config):
    assert ctx.obj.workspace
    ctx.obj.config = config
    config_file = set_path_variables(ctx.obj.workspace)
    set_version_variables(config_file)


@click.pass_context
def set_path_variables(ctx, workspace):
    try:
        with git.Repo(workspace) as repo, repo.config_reader() as cfg:
            code_dir = workspace / cfg.get_value('hopic.code', 'dir')
    except (git.InvalidGitRepositoryError, git.NoSuchPathError, NoOptionError, NoSectionError):
        code_dir = workspace

    ctx.obj.code_dir = code_dir
    ctx.obj.volume_vars['WORKSPACE'] = str(code_dir)
    source_date = determine_source_date(code_dir)
    if source_date is not None:
        ctx.obj.source_date = source_date
        ctx.obj.source_date_epoch = int((
            source_date - datetime.utcfromtimestamp(0).replace(tzinfo=tzutc())
        ).total_seconds())
        ctx.obj.volume_vars['SOURCE_DATE_EPOCH'] = str(ctx.obj.source_date_epoch)
    try:
        config_file = utils.determine_config_file_name(ctx, workspace)
    except click.BadParameter:
        return None

    if config_file is not None:
        if not config_file.is_absolute() and not config_file.is_reserved():
            config_file = Path.cwd() / config_file
        ctx.obj.config_dir = config_file.parent
        ctx.obj.volume_vars['CFGDIR'] = str(ctx.obj.config_dir)

    return config_file


@click.pass_context
def set_version_variables(ctx, config_file, *, config=None):
    version_info = config.get('version', {}) if config is not None else ctx.obj.config.get('version', {})

    ctx.obj.version, commit_hash = determine_version(
            version_info,
            config_dir=(config_file and ctx.obj.config_dir),
            code_dir=ctx.obj.code_dir,
        )

    if ctx.obj.version is None:
        if version_info.get('tag'):
            error_msg = (
                "Failed to determine the current version from Git tag. "
                "If this is a new repository, please create a version tag. "
                "If this is a shallow clone, try a deepening fetch."
            )
        elif version_info.get('file'):
            error_msg = (
                "Failed to determine the current version from file. "
                "Make sure the file exists and contains the expected value."
            )
        else:
            error_msg = "Failed to determine the current version."

        ctx.obj.volume_vars['VERSION'] = ctx.obj.volume_vars['PURE_VERSION'] = ctx.obj.volume_vars['PUBLISH_VERSION'] = VersioningError(error_msg)
        return

    log.debug("read version: \x1B[34m%s\x1B[39m", ctx.obj.version)
    ctx.obj.volume_vars['VERSION'] = str(ctx.obj.version)
    ctx.obj.volume_vars['PURE_VERSION'] = ctx.obj.volume_vars['VERSION'].split('+')[0]
    # FIXME: make this conversion work even when not using SemVer as versioning policy
    # Convert SemVer to Debian version: '~' for pre-release instead of '-'
    ctx.obj.volume_vars['DEBVERSION'] = ctx.obj.volume_vars['VERSION'].replace('-', '~', 1).replace('.dirty.', '+dirty', 1)
    if ctx.obj.publishable_version:
        ctx.obj.volume_vars['PUBLISH_VERSION'] = ctx.obj.volume_vars['PURE_VERSION']
        if 'build' in version_info:
            ctx.obj.volume_vars['PUBLISH_VERSION'] += f"+{version_info['build']}"
    else:
        assert commit_hash
        ver = SemVer.parse(ctx.obj.volume_vars['VERSION'])
        ver.build = ()  # discard duplicate commit_hash
        ver.prerelease += (commit_hash, )
        if 'build' in version_info:
            ver.build = (version_info['build'],)
        ctx.obj.volume_vars['PUBLISH_VERSION'] = str(ver)
