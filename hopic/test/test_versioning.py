# Copyright (c) 2019 - 2020 TomTom N.V. (https://tomtom.com)
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

from ..versioning import CarusoVer


def test_carver_tag_formatting():
    version_str = '7.4.47+PI13.0'
    version = CarusoVer.parse(version_str)
    assert version == CarusoVer(7, 4, 47, (), 13, 0)
    assert str(version) == version_str
    assert version.default_tag_name.format(version=version) == version_str
