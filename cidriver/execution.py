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
import os

try:
    from shlex import quote as shquote
except ImportError:
    from pipes import quote as shquote

def echo_cmd(fun, cmd, *args, **kwargs):
    click.echo('Executing: ' + click.style(' '.join(shquote(word) for word in cmd), fg='yellow'), err=True)

    # Set our locale for machine readability with UTF-8
    kwargs = kwargs.copy()
    try:
        env = kwargs['env'].copy()
    except KeyError:
        env = os.environ.copy()
    for key in list(env):
        if key.startswith('LC_') or key in ('LANG', 'LANGUAGE'):
            del env[key]
    env['LANG'] = 'C.UTF-8'
    kwargs['env'] = env

    try:
        output = fun(cmd, *args, **kwargs)
        return (output.decode('UTF-8') if isinstance(output, bytes) else output)
    except Exception as e:
        if hasattr(e, 'child_traceback'):
            click.echo("Child traceback: {}".format(e.child_traceback), err=True)
        raise
