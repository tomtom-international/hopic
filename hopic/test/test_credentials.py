# Copyright (c) 2020 - 2020 TomTom N.V. (https://tomtom.com)
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

from .. import credentials
import getpass
import sys
from typing import NamedTuple


class UserPassCredential(NamedTuple):
    username: str
    password: str


def test_obtain_named_credential(monkeypatch):
    class MockKeyring:
        scope = 'THE_AWESOME_SCOPE'
        cred_id = 'something-secret'
        username = 'username1234'
        password = 'password1234'

        def get_credential(self, service_name, username):
            if (service_name, username) == (f"{self.scope}-{self.cred_id}", None):
                return UserPassCredential(self.username, self.password)

    monkeypatch.setattr(credentials, '_init_keyring', lambda: MockKeyring())

    cred = credentials.get_credential_by_id(MockKeyring.scope, MockKeyring.cred_id)
    assert cred == (MockKeyring.username, MockKeyring.password)


def test_ask_for_missing_credential(monkeypatch):
    scope = 'THE_AWESOME_SCOPE'
    cred_id = 'something-secret'
    username = 'username1234'
    password = 'password1234'

    class EmptyKeyring:
        creds = {}

        def get_credential(self, service_name, username):
            return None

        @classmethod
        def set_password(cls, service_name, username, password):
            cls.creds[service_name] = UserPassCredential(username, password)

    monkeypatch.setattr(credentials, '_init_keyring', lambda: EmptyKeyring())
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr('builtins.input', lambda _: username)
    monkeypatch.setattr(getpass, 'getpass', lambda _: password)

    cred = credentials.get_credential_by_id(scope, cred_id)
    assert cred == (username, password)
    assert EmptyKeyring.creds == {f"{scope}-{cred_id}": (username, password)}
