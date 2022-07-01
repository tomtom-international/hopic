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

import click
import logging
import functools
import locale
import os
import re
import shlex

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


def no_exec(*args, **kwargs):
    return 0


# Caching is not just an optimization: manipulating the locale multiple times is risky on some platforms
@functools.cache
def determine_locale_envvars():
    # Prefer the current LC_CTYPE (character encoding) locale when it's already UTF-8
    if re.match(".*utf[-_]?8$", os.environ.get("LC_CTYPE", ""), re.IGNORECASE):
        return {"LANG": "C", "LC_CTYPE": os.environ["LC_CTYPE"]}

    # FreeBSD's preferred way of setting the encoding is to specify only the codeset name in LC_CTYPE without any language (and MacOS inherits this).
    # This doesn't work on glibc based operating systems though (most Linux distros) so make it optional.
    try:
        locale.setlocale(locale.LC_CTYPE, "UTF-8")
    except locale.Error:
        pass
    else:
        return {"LANG": "C", "LC_CTYPE": "UTF-8"}

    # C.UTF-8 works on all Linux distros (glibc really)
    # Also LC_ALL has precedence over all other LC_* environment variables, so overrides the rest.
    # So we can use that only if we're not setting any other LC_* vars.
    return {"LC_ALL": "C.UTF-8"}


def echo_cmd(fun, cmd, *args, dry_run=False, obfuscate=None, **kwargs):
    command_list = []
    for word in cmd:
        if obfuscate is not None:
            for secret_name, secret in obfuscate.items():
                if secret and isinstance(secret, str):
                    word = word.replace(secret, f'${{{secret_name}}}')
        command_list.append(shlex.quote(word))
    log.info('%s%s', '' if dry_run else 'Executing: ',
             click.style(' '.join(command_list), fg='yellow'))

    # Set our locale for machine readability with UTF-8
    kwargs = kwargs.copy()
    try:
        env = kwargs['env'].copy()
    except KeyError:
        env = os.environ.copy()
    for key in list(env):
        if key.startswith("LC_") or key in ("LANG", "LANGUAGE"):
            del env[key]
    env.update(determine_locale_envvars())
    kwargs['env'] = env

    try:
        exec_fun = no_exec if dry_run else fun
        output = exec_fun(cmd, *args, **kwargs)

        return (output.decode('UTF-8') if isinstance(output, bytes) else output)
    except Exception as e:
        if hasattr(e, 'child_traceback'):
            log.exception('Child traceback: %s', e.child_traceback)
        raise


def echo_cmd_click(fun, cmd, *args, obfuscate=None, **kwargs):
    ctx = click.get_current_context()
    return echo_cmd(fun, cmd, *args, **kwargs, obfuscate=obfuscate, dry_run=getattr(ctx.obj, 'dry_run', False))
