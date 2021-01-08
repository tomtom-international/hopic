# Copyright (c) 2019 - 2020 TomTom N.V.
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
