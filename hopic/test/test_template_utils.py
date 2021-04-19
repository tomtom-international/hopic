# Copyright (c) 2021 - 2021 TomTom N.V. (https://tomtom.com)
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

import keyword
from pathlib import Path
import sys

import pytest

from hopic.template.utils import (
    command,
    module_command,
)


def test_args_only():
    assert command("cmd") == ("cmd",)
    assert command("cmd", 1) == ("cmd", "1")
    assert command("cmd", "a") == ("cmd", "a")
    assert command("cmd", 1, 2, 3, "a", "b", "c") == ("cmd", "1", "2", "3", "a", "b", "c")


def test_bool_kwargs_only():
    assert command("cmd", first=True, second=True) == ("cmd", "--first", "--second")
    assert command("cmd", second=True, first=True) == ("cmd", "--second", "--first")
    assert command("cmd", first=False, second=True) == ("cmd", "--second")
    assert command("cmd", first=True, second=False) == ("cmd", "--first")


def test_list_kwargs():
    assert command("cmd", puppet=("Woody", "Buzz")) == ("cmd", "--puppet", "Woody", "--puppet", "Buzz")
    assert command("cmd", puppet=["Buzz", "Woody"]) == ("cmd", "--puppet", "Buzz", "--puppet", "Woody")


def test_short_kwargs():
    assert command("cmd", x=True, v=True, f=Path("some/archive.tar")) == ("cmd", "-x", "-v", "-f", "some/archive.tar")
    assert command("cmd", f=(True, True), v=True) == ("cmd", "-f", "-f", "-v")


def test_str_coercion():
    assert command("cmd", Path("over/here.txt"), include=(Path("somewhere/else.txt"), Path("some/other/place.txt"))) == (
        "cmd",
        *("--include", "somewhere/else.txt"),
        *("--include", "some/other/place.txt"),
        "over/here.txt",
    )

    assert command("cmd", 0, arg=1, args=(2, True)) == ("cmd", "--arg", "1", "--args", "2", "--args", "0")


@pytest.mark.parametrize("keyword", keyword.kwlist)
def test_keyword_opts(keyword):
    try:
        call = compile(f"command('cmd', {keyword}=True)", filename="", mode="eval")
    except SyntaxError:
        call = compile(f"command('cmd', _{keyword}=True)", filename="", mode="eval")

    assert eval(call) == ("cmd", f"--{keyword}")


def test_opt_like_args():
    assert command("cmd", Path("-cons.txt"), Path("+pros.txt"), evaluate=True) == ("cmd", "--evaluate", "--", "-cons.txt", "+pros.txt")


def test_list_command():
    assert command(("git", "add"), Path("a.txt"), Path("b.txt"), u=True) == ("git", "add", "-u", "a.txt", "b.txt")


def test_module_command():
    assert module_command(__name__, 1, 2, verbose=True) == (sys.executable, "-m", "hopic.test.test_template_utils", "--verbose", "1", "2")
    assert module_command((__name__, "sub"), 1, verbose=True) == (sys.executable, "-m", "hopic.test.test_template_utils", "sub", "--verbose", "1")
