# Copyright (c) 2021 - 2021 TomTom N.V.
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

import sys

# Two things to note about this Python version-dependent import:
#  - importlib.metadata was introduced in 3.8
#  - importlib.metadata.entry_points() accepts the group keyword since 3.10

if sys.version_info[:2] >= (3, 10):
    from importlib import metadata as metadata  # noqa: F401
else:
    import importlib_metadata as metadata  # noqa: F401
