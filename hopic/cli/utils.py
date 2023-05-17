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

from pathlib import Path
from pkg_resources import parse_version
import re
import subprocess
import sys
from typing import (
    Optional,
    Tuple,
    Union,
)

import click

from ..build import (
    HopicGitInfo,
)

from ..compat import metadata


def is_publish_branch(ctx, hopic_git_info=None) -> bool:
    """
    Check if the branch name is allowed to publish, if publish-from-branch is not defined in the config file, all the branches should be allowed to publish
    """

    if hopic_git_info is None:
        hopic_git_info = HopicGitInfo.from_repo(ctx.obj.workspace)
    if hopic_git_info is None or hopic_git_info.submit_ref is None:
        return False

    try:
        publish_from_branch = ctx.obj.config['publish-from-branch']
    except KeyError:
        return True

    publish_branch_pattern = re.compile(f"(?:{publish_from_branch})$")
    return publish_branch_pattern.match(re.sub("^refs/heads/", "", hopic_git_info.submit_ref)) is not None


def determine_config_file_name(ctx, workspace: Optional[Path] = None):
    """
    Determines the location of the config file, possibly falling back to a default.
    """
    try:
        return ctx.obj.config_file
    except (click.BadParameter, AttributeError):
        for fname in (
                    Path("hopic-ci-config.yaml"),
                    Path(".ci/hopic-ci-config.yaml"),
                ):
            if workspace is None:
                workspace = ctx.obj.workspace
            fname = workspace / fname
            if fname.is_file():
                return fname
        raise


def installed_pkgs():
    try:
        return subprocess.check_output((sys.executable, '-m', 'pip', 'freeze')).decode('UTF-8')
    except subprocess.CalledProcessError:
        pass


def get_package_version(package):
    """
    Consults Python's `importlib.metadata` or `importlib_metadata` package and returns the target package version.
    """
    return metadata.version(package)


def check_minimum_package_version(package: str, min_version: str) -> Tuple[bool, Union[str, None]]:
    """
    Checks whether the indicated package name is installed and meets the supplied minimum version triplet.
    Returns the result as well as the installed package version (if any, None otherwise).
    """
    try:
        version = get_package_version(package)
    except metadata.PackageNotFoundError:
        return False, None

    result = parse_version(version) >= parse_version(min_version)

    return result, version
