# Copyright (c) 2020 - 2021 TomTom N.V.
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

"""
Helper functionality for Hopic templates.
"""

import sys
from typing import (
    Any,
    List,
    Mapping,
    Tuple,
    Union,
)


def _kebabify(name: str):
    """Convert from snake_case to kebab-case"""
    return name.replace("_", "-")


def _name_to_arg(name):
    if len(name) == 1:
        return f"-{name}"
    else:
        return f"--{_kebabify(name)}"


def _kwarg_to_arg(name, value):
    if value is True:
        return [_name_to_arg(name)]
    elif value is not False and value is not None:
        return [_name_to_arg(name), str(value)]
    else:
        return []


def _kwargs_to_args(**kwargs: Mapping[str, Any]):
    for key, val in kwargs.items():
        if isinstance(val, (list, tuple)):
            for subval in val:
                yield from _kwarg_to_arg(key, subval)
        else:
            yield from _kwarg_to_arg(key, val)


def command(
    # NOTE: the_command_list param name is chosen to decrease probability of clashing with 'kwargs'
    the_command_list: Union[str, List[str], Tuple[str]],
    *args: Union[List[Any], Tuple[Any]],
    **kwargs: Mapping[str, Any],
):
    """
    Constructs an argument list from a Pythonic set of arguments and keyword arguments for executing
    some external program.
    """
    if isinstance(the_command_list, str):
        the_command_list = (the_command_list,)

    if args:
        return (*the_command_list, *_kwargs_to_args(**kwargs), "--", *(str(arg) for arg in args))
    else:
        return (*the_command_list, *_kwargs_to_args(**kwargs))


def module_command(
    # NOTE: the_module_and_command param name is chosen to decrease probability of clashing with 'kwargs'
    the_module_and_command: Union[str, List[str], Tuple[str]],
    *args: Union[List[Any], Tuple[Any]],
    **kwargs: Mapping[str, Any],
):
    """
    Constructs an argument list from a Pythonic set of arguments and keyword arguments for executing
    an executable Python module with.
    """
    if isinstance(the_module_and_command, str):
        the_module_and_command = (the_module_and_command,)

    return command((sys.executable, "-m", *the_module_and_command), *args, **kwargs)
