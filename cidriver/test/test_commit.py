# Copyright (c) 2019 - 2019 TomTom N.V. (https://tomtom.com)
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

from ..commit import CommitMessage


def test_basic_message_strip_and_splitup():
    """
    This tests these bits of functionality:

      * white space and comment stripping similar to how git-commit does it
      * line splitting
      * subject extraction
      * paragraph splitting
      * body extraction
    """
    m = CommitMessage('''\

# test stripping of comments and preceding empty lines

improvement(config): display config error messages without backtrace

In order to prevent users from thinking they're seeing a bug in Hopic.


This changes the type of ConfigurationError such that Click will display
its message without a backtrace. This ensures the displayed information
is more to the point.

# ------------------------ >8 ------------------------

This line and every other line after the 'cut' line above should not be present
in the output.

# test stripping of comments and succeeding empty lines

''')
    assert m.subject == '''improvement(config): display config error messages without backtrace'''
    assert m.lines[0] == m.subject

    assert m.paragraphs[0] == '''In order to prevent users from thinking they're seeing a bug in Hopic.'''
    assert m.paragraphs[0] == m.body.splitlines()[0]
    assert m.body.splitlines()[0] == m.message.splitlines()[2]

    assert m.paragraphs[1].splitlines(keepends=True)[0] == '''This changes the type of ConfigurationError such that Click will display\n'''

    assert m.paragraphs[-1].splitlines(keepends=True)[-1] == '''is more to the point.'''
    assert m.paragraphs[-1].splitlines()[-1] == m.body.splitlines()[-1]
    assert m.body.splitlines()[-1] == m.message.splitlines()[-1]
