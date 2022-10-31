# Copyright (c) 2018 - 2021 TomTom N.V. (https://tomtom.com)
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
from pathlib import Path

import click
import click_log
import git

from . import autocomplete
from .utils import (
        get_package_version,
    )
from ..config_reader import (
        read as read_config,
    )

from .global_obj import (
    set_path_variables,
    set_version_variables
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
            workspace = Path(workspace)
            if ctx.invoked_subcommand != 'checkout-source-tree':
                # Require the workspace directory to exist for anything but the checkout command
                if not workspace.is_dir():
                    raise click.BadParameter(
                        f"Directory '{workspace}' does not exist.",
                        ctx=ctx, param=param
                    )
        elif param.human_readable_name == 'config' and config is not None:
            config = Path(config)
            # Require the config file to exist everywhere that it's used
            try:
                # Try to open the file instead of config.is_file() because we want to be able to use /dev/null too
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
        if config is not None and config != Path(os.devnull):
            try:
                with git.Repo(config.parent, search_parent_directories=True) as repo:
                    # Default to containing repository of config file, ...
                    workspace = Path(repo.working_dir)
            except (git.InvalidGitRepositoryError, git.NoSuchPathError):
                # ... but fall back to containing directory of config file.
                workspace = config.parent
        else:
            workspace = Path.cwd()
    workspace = Path.cwd() / workspace
    ctx.obj.workspace = workspace
    ctx.obj.publishable_version = publishable_version
    ctx.obj.volume_vars = {}

    ctx.obj.register_dependent_attribute('code_dir', 'workspace')
    ctx.obj.register_dependent_attribute('source_date', 'workspace')
    ctx.obj.register_dependent_attribute('source_date_epoch', 'workspace')

    if config is not None:
        ctx.obj.config_file = config
    ctx.obj.register_dependent_attribute('config_file', 'config')
    config = set_path_variables(workspace)

    for var in whitelisted_var:
        try:
            ctx.obj.volume_vars[var] = os.environ[var]
        except KeyError:
            pass

    cfg = {}
    if config is not None:
        if not config.is_absolute() and not config.is_reserved():
            config = Path.cwd() / config
        ctx.obj.config_dir = config.parent
        ctx.obj.volume_vars['CFGDIR'] = str(ctx.obj.config_dir)
        # Prevent reading the config file _before_ performing a checkout. This prevents a pre-existing file at the same
        # location from being read as the config file. This may cause problems if that pre-checkout file has syntax
        # errors for example.
        if ctx.invoked_subcommand != 'checkout-source-tree':
            try:
                # Try to open the file instead of config.is_file() because we want to be able to use /dev/null too
                with open(config, 'rb'):
                    pass
            except IOError:
                pass
            else:
                cfg = ctx.obj.config = read_config(config, ctx.obj.volume_vars)
    set_version_variables(config, config=cfg)
