#!/bin/sh

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

set -e

# Ensure that the current UID always appears to have a user associated with it.
# SSH _really_ wants that: https://github.com/openssh/openssh-portable/blob/ced327b9fb78c94d143879ef4b2a02cbc5d38690/ssh.c#L686
if ! getent passwd "$(id -u)" > /dev/null 2>&1; then
  NSS_WRAPPER_PASSWD="$(mktemp --tmpdir passwd.XXXXXXXXXX)"
  NSS_WRAPPER_GROUP=/etc/group
  cat < /etc/passwd >> "${NSS_WRAPPER_PASSWD}"
  echo "hopic:x:$(id -u):$(id -g)::${HOME:-/nonexistent}:/usr/sbin/nologin" >> "${NSS_WRAPPER_PASSWD}"

  export NSS_WRAPPER_PASSWD NSS_WRAPPER_GROUP

  LD_PRELOAD=libnss_wrapper.so
  export LD_PRELOAD
fi

if [ $$ -eq 1 ] && which tini > /dev/null 2>&1; then
  exec tini -- "$@"
else
  exec "$@"
fi
