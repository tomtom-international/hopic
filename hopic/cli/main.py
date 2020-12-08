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

from configparser import (
        NoOptionError,
        NoSectionError,
    )
from datetime import datetime
import logging
import os

import click
import click_log
from dateutil.tz import (
        tzutc,
    )
import git

from . import autocomplete
from .utils import (
        determine_config_file_name,
        get_package_version,
    )
from ..config_reader import (
        read as read_config,
    )
from ..errors import (
    VersioningError,
)
from ..git_time import (
        determine_source_date,
        determine_version,
    )
from ..versioning import (
        SemVer
)

PACKAGE : str = __package__.split('.')[0]

log = logging.getLogger(__name__)


class OptionContext(object):
    def __init__(self):
        super().__init__()
        self._opts = {
            'dry_run': False,
        }
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


@click.group(context_settings=dict(help_option_names=('-h', '--help')))
@click.option('--color'          , type=click.Choice(('always', 'auto', 'never'))                                                  , default='auto'      , show_default=True)  # noqa: E501
@click.option('--config'         , type=click.Path(exists=False, file_okay=True , dir_okay=False, readable=True, resolve_path=True), default=lambda: None, show_default='${WORKSPACE}/hopic-ci-config.yaml')  # noqa: E501
@click.option('--workspace'      , type=click.Path(exists=False, file_okay=False, dir_okay=True)                                   , default=lambda: None, show_default='git work tree of config file or current working directory')  # noqa: E501
@click.option('--whitelisted-var', multiple=True                                                                                   , default=['CT_DEVENV_HOME'], hidden=True)  # noqa: E501
@click.option('--publishable-version', is_flag=True                                                                                , default=False, hidden=True, help='''Indicate if change is publishable or not''')  # noqa: E501
@click.version_option(get_package_version(PACKAGE))
@click_log.simple_verbosity_option(PACKAGE                 , envvar='HOPIC_VERBOSITY', autocompletion=autocomplete.click_log_verbosity)
@click_log.simple_verbosity_option('git', '--git-verbosity', envvar='GIT_VERBOSITY'  , autocompletion=autocomplete.click_log_verbosity)
@click.pass_context
def main(ctx, color, config, workspace, whitelisted_var, publishable_version):
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
            try:
                # Try to open the file instead of os.path.isfile because we want to be able to use /dev/null too
                with open(config, 'rb'):
                    pass
            except IOError:
                def exception_raiser(ctx, param):
                    raise click.BadParameter(
                        f"File '{config}' does not exist.",
                        ctx=ctx, param=param
                    )
                ctx.obj.register_parameter(ctx=ctx, param=param, exception_raiser=exception_raiser)

    if workspace is None:
        # workspace default
        if config is not None and config != os.devnull:
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
        if not os.path.isabs(config) and config != os.devnull:
            config = os.path.join(os.getcwd(), config)
        ctx.obj.volume_vars['CFGDIR'] = ctx.obj.config_dir = os.path.dirname(config)
        # Prevent reading the config file _before_ performing a checkout. This prevents a pre-existing file at the same
        # location from being read as the config file. This may cause problems if that pre-checkout file has syntax
        # errors for example.
        if ctx.invoked_subcommand != 'checkout-source-tree':
            try:
                # Try to open the file instead of os.path.isfile because we want to be able to use /dev/null too
                with open(config, 'rb'):
                    pass
            except IOError:
                pass
            else:
                cfg = ctx.obj.config = read_config(config, ctx.obj.volume_vars)
    ctx.obj.register_dependent_attribute('config_dir', 'config')

    version_info = cfg.get('version', {})
    ctx.obj.version, commit_hash = determine_version(
            version_info,
            config_dir=(config and ctx.obj.config_dir),
            code_dir=ctx.obj.code_dir,
        )
    if ctx.obj.version is not None:
        log.debug("read version: \x1B[34m%s\x1B[39m", ctx.obj.version)
        ctx.obj.volume_vars['VERSION'] = str(ctx.obj.version)
        ctx.obj.volume_vars['PURE_VERSION'] = ctx.obj.volume_vars['VERSION'].split('+')[0]
        # FIXME: make this conversion work even when not using SemVer as versioning policy
        # Convert SemVer to Debian version: '~' for pre-release instead of '-'
        ctx.obj.volume_vars['DEBVERSION'] = ctx.obj.volume_vars['VERSION'].replace('-', '~', 1).replace('.dirty.', '+dirty', 1)
        if publishable_version:
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
    else:
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
