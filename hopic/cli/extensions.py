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
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from textwrap import dedent
from typing import (
    Optional,
)

from ..compat import metadata

import click

from ..config_reader import (
    get_entry_points,
    read as read_config,
)
from ..execution import echo_cmd_click as echo_cmd
from .utils import (
    determine_config_file_name,
    get_package_version,
    check_minimum_package_version,
)

PACKAGE : str = __package__.split('.')[0]
log = logging.getLogger(__name__)


def check_minimum_pip_version():
    """Check the installed version of pip and issue a warning if it's lower than recommended."""
    MINIMUM_PIP_VERSION = "21.1.0"
    success, version = check_minimum_package_version("pip", MINIMUM_PIP_VERSION)
    if success:
        return

    if not version:
        log.warning("Package 'pip' is not installed")
    else:
        log.warning(
            dedent(
                """
            The installed 'pip' version (%s) does not meet the recommended minimum of %s.
            You may encounter fatal pip errors related to URL constraints.
            It is strongly recommended to upgrade your pip package:

              pip install --upgrade pip>=21.0
            """
            ),
            version,
            MINIMUM_PIP_VERSION,
        )


@click.command()
# fmt: off
@click.option("--constraints", type=click.Path(exists=True, dir_okay=False, readable=True), help="""Apply the provided constraints file to pip operations""")
@click.option("--upgrade", is_flag=True, help="""Request for already installed packages to be upgraded to newer versions, can't be combined with "constraints" option""")
# fmt: on
@click.pass_context
def install_extensions(ctx, constraints: Optional[str] = None, *, upgrade: bool = False):
    if constraints and upgrade:
        raise click.BadOptionUsage("upgrade", 'options "constraints" and "upgrade" are mutually exclusive')

    def installer(pip_cfg):
        return install_extensions_with_config(pip_cfg, constraints, upgrade=upgrade)

    # Read the config file and install all templates available
    return read_config(
        determine_config_file_name(ctx),
        ctx.obj.volume_vars,
        installer,
    )


def install_extensions_with_config(pip_cfg, input_constraints_file: Optional[str], *, upgrade: bool):
    if not pip_cfg:
        return

    is_venv = (hasattr(sys, 'real_prefix') or getattr(sys, 'base_prefix', None) != sys.prefix)

    with tempfile.TemporaryDirectory() as td:
        # Prevent changing the Hopic version and add constraints that were passed in, if any
        constraints_text = f"{PACKAGE}=={get_package_version(PACKAGE)}\n"
        if input_constraints_file:
            # Issue a warning if a constraints file is provided and the pip version is not prepared
            # to handle URL constraints.
            check_minimum_pip_version()

            input_constraints = Path(input_constraints_file).read_text()
            # Remove any existing references to the Hopic package itself from the input
            constraints_text += re.sub(f"(?m)^{PACKAGE}[^A-Za-z0-9._-].*?\n", "", input_constraints)

        constraints_file = Path(td) / "constraints.txt"
        constraints_file.write_text(constraints_text)
        log.debug("pip constraints used:\n%s", constraints_text)

        base_cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-c",
            str(constraints_file.resolve()),
        ]

        plog = logging.getLogger(PACKAGE)
        if plog.isEnabledFor(logging.DEBUG):
            base_cmd.append('--verbose')

        if not is_venv:
            base_cmd.append('--user')

        if upgrade:
            base_cmd.append("--upgrade")

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
    get_entry_points.cache_clear()
