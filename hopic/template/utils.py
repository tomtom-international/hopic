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

from collections.abc import Sequence
import sys
from typing import (
    Any,
    Iterable,
    List,
    Tuple,
    Union,
)


def _kebabify(name: str) -> str:
    """Convert from snake_case to kebab-case"""
    return name.replace("_", "-")


def _name_to_arg(name: str) -> str:
    if name.startswith("_") and len(name) > 1:
        name = name[1:]
    if len(name) == 1:
        assert name not in ("_", "-")
        return f"-{name}"
    else:
        return f"--{_kebabify(name)}"


def _kwarg_to_arg(name: str, value: Any) -> Iterable[str]:
    if value is True:
        yield _name_to_arg(name)
    elif value is not False and value is not None:
        yield _name_to_arg(name)
        yield str(value)


def _kwargs_to_args(**kwargs: Any) -> Iterable[str]:
    for key, val in kwargs.items():
        if not isinstance(val, str) and isinstance(val, Sequence):
            for subval in val:
                yield from _kwarg_to_arg(key, subval)
        else:
            yield from _kwarg_to_arg(key, val)


def command(
    # NOTE: the_command_list param name is chosen to decrease probability of clashing with 'kwargs'
    the_command_list: Union[str, List[str], Tuple[str, ...]],
    *args: Any,
    **kwargs: Any,
):
    """
    Constructs an argument list from a Pythonic set of arguments and keyword arguments for executing
    some external program.
    """
    if isinstance(the_command_list, str):
        the_command_list = (the_command_list,)

    args = tuple(str(arg) for arg in args)
    if any(arg.startswith("-") for arg in args):
        # Prevent interpretation as an option
        args = ("--", *args)

    return (*the_command_list, *_kwargs_to_args(**kwargs), *args)


def module_command(
    # NOTE: the_module_and_command param name is chosen to decrease probability of clashing with 'kwargs'
    the_module_and_command: Union[str, List[str], Tuple[str, ...]],
    *args: Any,
    **kwargs: Any,
):
    """
    Constructs an argument list from a Pythonic set of arguments and keyword arguments for executing
    an executable Python module with.
    """
    if isinstance(the_module_and_command, str):
        the_module_and_command = (the_module_and_command,)

    return command((sys.executable, "-m", *the_module_and_command), *args, **kwargs)
