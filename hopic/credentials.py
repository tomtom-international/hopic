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

import getpass
import sys

try:
    import keyring
    import keyring.backends.kwallet
    import keyring.backends.OS_X
    import keyring.backends.SecretService
    import keyring.credentials
    import netstruct
    from contextlib import closing

    class OSXKeyring(keyring.backends.OS_X.Keyring):

        pattern = b'b$b$'

        def get_credential(self, service, username):
            if username is None:
                username = service
            cred = super().get_credential(service, username)
            if cred is not None:
                u, p = netstruct.unpack(self.pattern, cred.password.encode('UTF-8'))
                cred = keyring.credentials.SimpleCredential(u.decode('UTF-8'), p.decode('UTF-8'))
            return cred

        def set_password(self, service, username, password):
            password = netstruct.pack(self.pattern, username.encode('UTF-8'), password.encode('UTF-8')).decode('UTF-8')
            username = service
            return super().set_password(service, username, password)

    class KeePassKeyring(keyring.backends.SecretService.Keyring):
        """
        A version of the SecretService keyring API that falls back to using the camel case
        'UserName'. KeePass uses that as its attribute name instead of 'username' like most other
        implementations.
        """

        appid = 'Hopic'

        def get_credential(self, service, username):
            for attr in ('username', 'UserName'):
                query = {'service': service}
                if username:
                    query[attr] = username

                collection = self.get_preferred_collection()

                with closing(collection.connection):
                    items = collection.search_items(query)
                    for item in items:
                        self.unlock(item)
                        for attr in ('username', 'UserName'):
                            username = item.get_attributes().get(attr)
                            if username is not None:
                                break
                        return keyring.credentials.SimpleCredential(username, item.get_secret().decode('UTF-8'))

        def get_password(self, service, username):
            cred = self.get_credential(service, username)
            if cred is not None:
                return cred.password
except ImportError:
    keyring = None  # type: ignore # mypy is buggy for this try-except import style: https://github.com/python/mypy/issues/1153

_keyring_backend = None


def _init_keyring():
    if keyring is None:
        return

    global _keyring_backend
    if _keyring_backend is not None:
        return _keyring_backend

    backends = [keyring.get_keyring()]
    try:
        backends = backends[0].backends
    except AttributeError:
        pass

    for i, backend in enumerate(backends):
        # Substitute our own KeePass compatible keyring
        if isinstance(backend, keyring.backends.SecretService.Keyring):
            backends[i] = KeePassKeyring()
            backend = backends

        if isinstance(backend, (
                keyring.backends.kwallet.DBusKeyring,
                keyring.backends.SecretService.Keyring,
                )):
            backend.appid = 'Hopic'

        if isinstance(backend, keyring.backends.OS_X.Keyring):
            backends[i] = OSXKeyring()
            backend = backends

    if len(backends) == 1:
        _keyring_backend = backends[0]
    else:
        _keyring_backend = keyring.get_keyring()
    return _keyring_backend


def get_credential_by_id(project_name, cred_id):
    backend = _init_keyring()
    if backend is None:
        return None

    cred_name = f"{project_name}-{cred_id}"
    kcred = backend.get_credential(cred_name, None)
    if kcred is not None:
        return kcred.username, kcred.password
    elif hasattr(sys.stdin, 'isatty') and sys.stdin.isatty():
        username =           input(f"Username for {cred_name}: ")  # noqa: E222
        password = getpass.getpass(f"Password for {cred_name}: ")
        backend.set_password(cred_name, username, password)
        return username, password
