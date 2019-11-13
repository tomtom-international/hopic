# Copyright (c) 2018 - 2019 TomTom N.V. (https://tomtom.com)
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

import re


class CommitMessage(object):
    line_separator = '\n'
    paragraph_separator = '\n\n'

    def __init__(self, message):
        self.message = _strip_message(message)

        # Discover starts of lines
        self._line_index = [m.end() for m in re.finditer(self.line_separator, self.message)]
        self._line_index.insert(0, 0)
        if len(self._line_index) < 2 or self._line_index[-1] < len(self.message):
            self._line_index.append(len(self.message) + 1)

        # Discover starts of paragraphs
        self._paragraph_index = [m.end() for m in re.finditer(self.paragraph_separator, self.message)]
        if not self.message[self._line_index[1] - len(self.line_separator):].startswith(self.paragraph_separator):
            self._paragraph_index.insert(0, self._line_index[1])
        self._paragraph_index.append(len(self.message) + len(self.paragraph_separator))

        # Strip last line terminator from the last paragraph.
        if self.message and self.message[-1] == self.line_separator:
            self._paragraph_index[-1] -= 1

    @property
    def lines(self):
        return _IndexedList(self.message, self._line_index, self.line_separator)

    @property
    def subject(self):
        return self.message[:self._line_index[1] - 1]

    @property
    def body(self):
        return self.message[self._paragraph_index[0]:]

    @property
    def paragraphs(self):
        return _IndexedList(self.message, self._paragraph_index, self.paragraph_separator)


class _IndexedList(object):
    def __init__(self, message, index, separator):
        self._message = message
        self._index = index
        self.separator = separator

    def __len__(self):
        return len(self._index) - 1

    def __getitem__(self, idx):
        if idx < 0:
            idx += len(self)
        return self._message[self._index[idx] : self._index[idx+1] - len(self.separator)]

def _strip_message(message):
    cut_line = message.find('# ------------------------ >8 ------------------------\n')
    if cut_line >= 0 and (cut_line == 0 or message[cut_line - 1] == '\n'):
        message = message[:cut_line]

    # Strip comments
    message = re.sub(r'^#[^\n]*\n?', '', message, flags=re.MULTILINE)
    # Strip trailing whitespace from lines
    message = re.sub(r'[ \t]+$', '', message, flags=re.MULTILINE)
    # Merge consecutive empty lines into a single empty line
    message = re.sub(r'(?<=\n\n)\n+', '', message)
    # Remove empty lines from the beginning and end
    while message[:1] == '\n':
        message = message[1:]
    while message[-2:] == '\n\n':
        message = message[:-1]

    return message
