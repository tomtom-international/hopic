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

from configparser import (
        NoOptionError,
        NoSectionError,
    )
import os
import re
import subprocess
import sys

import click
import git

try:
    # Python >= 3.8
    from importlib import metadata
except ImportError:
    import importlib_metadata as metadata


def is_publish_branch(ctx):
    """
    Check if the branch name is allowed to publish, if publish-from-branch is not defined in the config file, all the branches should be allowed to publish
    """

    try:
        with git.Repo(ctx.obj.workspace) as repo:
            target_commit = repo.head.commit
            with repo.config_reader() as cfg:
                target_ref = cfg.get_value(f"hopic.{target_commit}", 'ref')
    except (NoOptionError, NoSectionError):
        return False

    try:
        publish_from_branch = ctx.obj.config['publish-from-branch']
    except KeyError:
        return True

    publish_branch_pattern = re.compile(f"(?:{publish_from_branch})$")
    return publish_branch_pattern.match(target_ref)


def determine_config_file_name(ctx):
    """
    Determines the location of the config file, possibly falling back to a default.
    """
    try:
        return ctx.obj.config_file
    except (click.BadParameter, AttributeError):
        for fname in (
                    'hopic-ci-config.yaml',
                ):
            fname = os.path.join(ctx.obj.workspace, fname)
            if os.path.isfile(fname):
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
