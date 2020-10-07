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

try:
    # Python >= 3.8
    from importlib import metadata
except ImportError:
    import importlib_metadata as metadata

import click

from ..config_reader import read as read_config
from ..execution import echo_cmd_click as echo_cmd
from .utils import determine_config_file_name


PACKAGE : str = __package__.split('.')[0]


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
            cf.write(f"{PACKAGE}=={metadata.distribution(PACKAGE).version}\n")

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

            echo_cmd(subprocess.check_call, cmd, stdout=sys.__stderr__)

    # Ensure newly installed packages can be imported
    importlib.invalidate_caches()
