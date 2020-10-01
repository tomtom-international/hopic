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

from ..cli import utils
import pytest

try:
    # Python >= 3.8
    from importlib import metadata
except ImportError:
    import importlib_metadata as metadata

PACKAGE : str = __package__.split('.')[0]
_hopic_version_str = f"{PACKAGE}=={metadata.version(PACKAGE)}"


@pytest.fixture(autouse=True)
def pip_freeze_constant(monkeypatch):
    """Prevent invoking 'pip freeze' to improve execution speed"""
    with monkeypatch.context() as m:
        m.setattr(utils, 'installed_pkgs', lambda: _hopic_version_str)
        yield m
