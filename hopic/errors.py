# Copyright (c) 2019 - 2021 TomTom N.V.
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

from textwrap import dedent
from typing import Optional

from click import ClickException


class ConfigurationError(ClickException):
    exit_code = 32

    def __init__(self, message, file=None):
        super().__init__(message)
        self.file = file

    def format_message(self):
        if self.file is not None:
            return "configuration error in '%s': %s" % (self.file, self.message)
        else:
            return "configuration error: %s" % (self.message,)


class VersioningError(ClickException):
    exit_code = 33


class MissingCredentialVarError(ClickException):
    exit_code = 34

    def __init__(self, credential_id, var_name):
        super().__init__(f"credential '{credential_id}' not available when trying to expand variable '{var_name}'")
        self.credential_id = credential_id
        self.var_name      = var_name


class UnknownPhaseError(ClickException):
    exit_code = 35

    def __init__(self, phase):
        super().__init__(f"build does not contain phase(s): {', '.join(phase)}")
        self.phase = phase


class VersionBumpMismatchError(ClickException):
    exit_code = 36

    def __init__(self, commit_version, merge_version):
        super().__init__(f"Version bump for commit messages results in different version ({commit_version}) "
                         f"than the version based on the merge message ({merge_version}).")


class CommitAncestorMismatchError(ClickException):
    exit_code = 37

    def __init__(self, commit, ancestor_commit, ref):
        super().__init__(
            dedent(
                """\
                attempting to checkout commit '{self.commit}' which is not an ancestor of remote ref '{self.ref}' ('{self.ancestor_commit}')
                possibly remote ref '{self.ref}' was force pushed to
                """
            )
        )
        self.commit = commit
        self.ancestor_commit = ancestor_commit
        self.ref = ref

    def format_message(self):
        return self.message.format(self=self)

    def __str__(self):
        return self.format_message()


class MissingFileError(ClickException):
    exit_code = 38


class GitNotesMismatchError(ClickException):
    exit_code = 39

    def __init__(self, object, new_note, existing_note):
        super().__init__(
            dedent(
                """\
                attempting to add a different note to object '{self.object}' which already had a Hopic note
                new note:
                {self.new_note}

                existing note:
                {self.existing_note}
                """
            )
        )
        self.object = object
        self.new_note = new_note
        self.existing_note = existing_note

    def format_message(self):
        return self.message.format(self=self)

    def __str__(self):
        return self.format_message()


class StepTimeoutExpiredError(ClickException):
    exit_code = 40

    def __init__(self, timeout, *, cmd: Optional[str] = None, before: bool = False):
        msg = f"Timeout of {timeout} seconds expired {'before' if before else 'while'} executing build command"
        if cmd:
            msg += f": {cmd}"
        super().__init__(msg)
        self.timeout = timeout
