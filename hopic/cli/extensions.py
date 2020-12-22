# Copyright (c) 2020 - 2020 TomTom N.V.
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

import importlib
import logging
import os
import subprocess
import sys
import tempfile
from textwrap import dedent

try:
    from importlib import metadata
except ImportError:
    import importlib_metadata as metadata

import click

from ..config_reader import read as read_config
from ..execution import echo_cmd_click as echo_cmd
from .utils import (
        determine_config_file_name,
        get_package_version,
        )

PACKAGE : str = __package__.split('.')[0]
log = logging.getLogger(__name__)


@click.command()
@click.pass_context
def install_extensions(ctx):
    # Read the config file and install all templates available
    ctx.obj.config = read_config(determine_config_file_name(ctx), ctx.obj.volume_vars, install_extensions_with_config)


def install_extensions_with_config(pip_cfg):
    if not pip_cfg:
        return

    is_venv = (hasattr(sys, 'real_prefix') or getattr(sys, 'base_prefix', None) != sys.prefix)

    with tempfile.TemporaryDirectory() as td:
        # Prevent changing the Hopic version
        constraints_file = os.path.join(td, 'constraints.txt')
        with open(constraints_file, 'w', encoding='UTF-8') as cf:
            cf.write(f"{PACKAGE}=={get_package_version(PACKAGE)}\n")

        base_cmd = [
                sys.executable, '-m', 'pip', 'install',
                '-c', constraints_file,
            ]

        plog = logging.getLogger(PACKAGE)
        if plog.isEnabledFor(logging.DEBUG):
            base_cmd.append('--verbose')

        if not is_venv:
            base_cmd.append('--user')

        for spec in pip_cfg:
            cmd = base_cmd.copy()

            from_index = spec.get('from-index')
            if from_index is not None:
                cmd.extend(['--index-url', spec['from-index']])
            for index in spec['with-extra-index']:
                cmd.extend(['--extra-index-url', index])

            cmd.extend(spec['packages'])

            try:
                echo_cmd(subprocess.check_call, cmd, stdout=sys.__stderr__)
            except subprocess.CalledProcessError as exc:
                if not spec['with-extra-index']:
                    raise

                # This is the first version that fixes https://github.com/pypa/pip/issues/4195
                required_versionstr = "19.1"
                versionstr = metadata.version("pip")

                def try_int(s):
                    try:
                        return int(s)
                    except ValueError:
                        return s

                version = tuple(try_int(x) for x in versionstr.split("."))
                required_version = tuple(try_int(x) for x in required_versionstr.split("."))
                if version < required_version:
                    log.error(
                        dedent(
                            """\
                            pip failed to install with error code %i while using an extra-index.

                            The pip version available (%s) is older than %s and may contain a bug related to usage of --extra-index-url.

                            Consider updating pip to a more recent version.
                            For example: %s -m pip install --upgrade pip
                            """
                        ),
                        exc.returncode,
                        versionstr,
                        required_versionstr,
                        sys.executable,
                    )
                raise

    # Ensure newly installed packages can be imported
    importlib.invalidate_caches()
