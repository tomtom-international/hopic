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
    autosquash_re = re.compile(r'^(fixup|squash)!\s+')

    def __init__(self, message):
        self.message = _strip_message(message)

        # Discover starts of lines
        self._line_index = [m.end() for m in re.finditer(self.line_separator, self.message)]
        self._line_index.insert(0, 0)
        if len(self._line_index) < 2 or self._line_index[-1] < len(self.message):
            self._line_index.append(len(self.message) + 1)

        merge = re.match(r'^Merge.*?:[ \t]*', self.message)
        self._subject_start = merge.end() if merge is not None else 0

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
        return self.message[self._subject_start:self._line_index[1] - 1]

    def needs_autosquash(self):
        return self.autosquash_re.match(self.subject) is not None

    @property
    def autosquashed_subject(self):
        return self.autosquash_re.sub('', self.subject)

    @property
    def body(self):
        return self.message[self._paragraph_index[0]:]

    @property
    def paragraphs(self):
        return _IndexedList(self.message, self._paragraph_index, self.paragraph_separator)

    def paragraph_line(self, idx):
        if idx < 0:
            idx += len(self._paragraph_index) - 1
        idx = self._paragraph_index[idx]
        return self.message[:idx].count(self.line_separator)


class ConventionalCommit(CommitMessage):
    strict_subject_re = re.compile(r'''
    ^
    # 1. Commits MUST be prefixed with a type, which consists of a noun, feat, fix, etc., ...
    (?P<type_tag>\w+)

    # 4. A scope MAY be provided after a type. A scope MUST consist of a noun describing a section of the codebase
    #    surrounded by parenthesis, e.g., `fix(parser):`
    (?: \( (?P<scope> \S+? ) \) )?

    # 1. Commits MUST be prefixed with a type, ..., followed by ..., OPTIONAL `!`, ...
    (?P<breaking>!)?

    # 1. Commits MUST be prefixed with a type, ..., and REQUIRED terminal colon and space.
    :[ ]

    # 5. A description MUST immediately follow the colon and space after the type/scope prefix. The description is a
    #    short description of the code changes, e.g., fix: array parsing issue when multiple spaces were contained in
    #    string.
    (?P<description>.+)
    $
    ''', re.VERBOSE)

    def __init__(self, message):
        super().__init__(message)
        m = self.strict_subject_re.match(self.autosquashed_subject)
        if not m:
            raise RuntimeError("commit message's subject ({self.subject!r}) not formatted according to Conventional Commits ({self.strict_subject_re.pattern})".format(self=self))
        self.type_tag     = m.group('type_tag')
        self.scope        = m.group('scope')
        self._is_breaking = m.group('breaking')
        self.description  = m.group('description')

    def has_breaking_change(self):
        if self._is_breaking:
            return True

        for paragraph in self.paragraphs:
            # 16. `BREAKING-CHANGE` MUST be synonymous with `BREAKING CHANGE`, when used as a token in a footer.
            if re.match(r'^BREAKING[- ]CHANGE: ', paragraph):
                return True

        return False

    def has_new_feature(self):
        return self.type_tag.lower() == 'feat'

    def has_fix(self):
        return self.type_tag.lower() == 'fix'


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
