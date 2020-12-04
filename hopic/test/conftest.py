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

import os.path
from pathlib import (
    Path,
    PurePath,
)
import typing

try:
    # Python >= 3.8
    from importlib import metadata
except ImportError:
    import importlib_metadata as metadata

import pytest

from ..cli import utils

PACKAGE: str = __package__.split('.')[0]
_hopic_version_str = f"{PACKAGE}=={metadata.version(PACKAGE)}"
_root_dir = Path(__file__).parent / '..' / '..'
_example_dir = _root_dir / 'examples'


@pytest.fixture(autouse=True)
def pip_freeze_constant(monkeypatch):
    """Prevent invoking 'pip freeze' to improve execution speed"""
    with monkeypatch.context() as m:
        m.setattr(utils, 'installed_pkgs', lambda: _hopic_version_str)
        yield m


def _data_file_paths(
    datadir: typing.Union[str, PurePath],
    *,
    suffices: typing.Optional[typing.AbstractSet[str]] = None,
):
    for entry in datadir.iterdir():
        if suffices is not None and entry.suffix not in suffices:
            continue

        if entry.is_dir() or entry.is_socket() or entry.is_block_device():
            continue

        yield entry


def _data_file_path_id(
    datadir: typing.Union[str, PurePath],
    name,
):
    try:
        return os.path.relpath(name, datadir)
    except TypeError:
        return str(name)


@pytest.fixture(
    params=_data_file_paths(_example_dir, suffices={'.yml', '.yaml'}),
    ids=lambda entry: _data_file_path_id(_example_dir, entry),
)
def example_file(request):
    with open(request.param, 'r') as f:
        yield f


@pytest.fixture(
    params=_data_file_paths(_example_dir / 'simple', suffices={'.yml', '.yaml'}),
    ids=lambda entry: _data_file_path_id(_example_dir, entry),
)
def simple_example_file(request):
    with open(request.param, 'r') as f:
        yield f


@pytest.fixture(
    params=_data_file_paths(_example_dir / 'embed', suffices={'.yml', '.yaml'}),
    ids=lambda entry: _data_file_path_id(_example_dir, entry),
)
def embed_example_file(request):
    with open(request.param, 'r') as f:
        yield f
