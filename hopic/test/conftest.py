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

import logging
import os
import os.path
import sys
from functools import (
    partial,
    wraps,
)
from pathlib import (
    Path,
    PurePath,
)
from typing import (
    AbstractSet,
    Any,
    Callable,
    List,
    Mapping,
    Optional,
    Tuple,
    Union,
)

try:
    # Python >= 3.8
    from importlib import metadata
except ImportError:
    import importlib_metadata as metadata

import click_log
import git
import pytest
from click import ClickException
from click.testing import CliRunner
from typeguard import typechecked

from ..cli import utils
from . import (
    hopic_cli,
    sgr_re,
    source_date_epoch,
)

try:
    # Only available from Python >= 3.8 onwards
    from typing import Protocol
except ImportError:
    FileLike = Any
else:
    class FileLike(Protocol):
        name: str

        def read(self) -> str:
            ...


PACKAGE: str = __package__.split('.')[0]
_hopic_version_str = f"{PACKAGE}=={metadata.version(PACKAGE)}"
_root_dir = Path(__file__).parent / '..' / '..'
_example_dir = _root_dir / 'examples'

_git_time = f"{source_date_epoch} +0000"
_author = git.Actor('Bob Tester', 'bob@example.net')
_commitargs = dict(
        author_date=_git_time,
        commit_date=_git_time,
        author=_author,
        committer=_author,
    )


@pytest.fixture
def run_hopic(caplog, monkeypatch, tmp_path):
    @typechecked
    def run_hopic(
        *args: Union[List, Tuple, Callable[[], Any]],
        config: Union[str, FileLike, None] = None,
        files: Optional[Mapping[Union[str, PurePath], Union[str, Tuple[str, Callable[[Union[str, PurePath]], Any]]]]] = None,
        env: Optional[Mapping[str, str]] = None,
        monkeypatch_injector: Callable[[Any], None] = lambda _: None,
        umask: int = 0o022,
        commit_count: int = 0,
        dirty: Union[bool, str] = False,
        tag: Optional[str] = None,
    ):
        result = None
        commit = None
        runner = CliRunner(mix_stderr=False, env=env)
        umask = os.umask(umask)
        try:
            with monkeypatch.context() as dir_ctx:
                rundir = tmp_path / "rundir"
                if not rundir.exists():
                    rundir.mkdir(parents=True)
                dir_ctx.chdir(rundir)

                files = ({} if files is None else files.copy())
                if config is not None:
                    try:
                        fn = config.read
                    except AttributeError:
                        fn = lambda: config  # noqa: E731
                    files[getattr(config, "name", ".ci/hopic-ci-config.yaml")] = fn()

                if files:
                    with git.Repo.init() as repo:
                        for fname, content in files.items():
                            on_file_created_callback = lambda path: None  # noqa: E731
                            if not isinstance(content, str):
                                (content, on_file_created_callback) = content
                            if '/' in fname and not os.path.exists(os.path.dirname(fname)):
                                os.makedirs(os.path.dirname(fname))
                            with open(fname, 'w') as f:
                                f.write(content)
                            on_file_created_callback(fname)
                        repo.index.add(files.keys())
                        commit = repo.index.commit(message='Initial commit', **_commitargs)
                        if tag:
                            repo.create_tag(tag)
                        for i in range(commit_count):
                            commit = repo.index.commit(message=f"Some commit {i}", **_commitargs)
                        if dirty:
                            if dirty is True:
                                dirty = "dirty_file"
                            (rundir / dirty).write_text("dirty")
                            repo.index.add((dirty,))  # do not commit to create the dirty state
                else:
                    assert config is None and commit_count == 0 and dirty is False and tag is None

                for arg in args:
                    if callable(arg):
                        arg()
                        continue

                    orig_main = hopic_cli.main
                    rv = []

                    @wraps(orig_main)
                    def mock_main(*args, **kwargs):
                        with monkeypatch.context() as m:
                            # Ensure pytest can capture our logging
                            m.setattr(click_log, "basic_config", lambda: None)
                            m.setattr(logging.getLogger("hopic"), "setLevel", lambda _: None)
                            m.setattr(logging.getLogger("git"), "setLevel", lambda _: None)

                            monkeypatch_injector(m)
                            rv.append(orig_main(*args, **kwargs))
                            return rv[-1]

                    with monkeypatch.context() as call_ctx:
                        call_ctx.setattr(hopic_cli, "main", mock_main)
                        result = runner.invoke(hopic_cli, [str(a) for a in arg], standalone_mode=False)

                    if result.stdout_bytes:
                        print(result.stdout, end='')
                    if result.stderr_bytes:
                        print(result.stderr, end='', file=sys.stderr)

                    if result.exception is not None and not isinstance(result.exception, (SystemExit, ClickException)):
                        raise result.exception
                    if isinstance(result.exception, ClickException):
                        result.exit_code = result.exception.exit_code
                    if result.exit_code == 0 and isinstance(rv[-1], int):
                        result.exit_code = rv[-1]

                    result.commit = commit
                    result.logs = tuple(
                        (rec.levelno, sgr_re.sub("", rec.getMessage())) for rec in caplog.records if rec.name == "hopic" or rec.name.startswith("hopic.")
                    )
                    yield result

                    if result.exit_code != 0:
                        return
        finally:
            os.umask(umask)

    run_hopic.toprepo = tmp_path / "repo"
    # Make _all_ logging levels available for capture by pytest
    caplog.set_level("DEBUG", logger="git")
    caplog.set_level("DEBUG", logger="hopic")
    return run_hopic


@pytest.fixture(autouse=True)
def pip_freeze_constant(monkeypatch):
    """Prevent invoking 'pip freeze' to improve execution speed"""
    with monkeypatch.context() as m:
        m.setattr(utils, 'installed_pkgs', lambda: _hopic_version_str)
        yield m


def _data_file_paths(
    datadir: Union[str, PurePath],
    *,
    recurse: bool = False,
    suffices: Optional[AbstractSet[str]] = None,
):
    for entry in datadir.iterdir():
        if recurse and entry.is_dir():
            yield from _data_file_paths(entry, suffices=suffices)
            continue

        if suffices is not None and entry.suffix not in suffices:
            continue

        if entry.is_dir() or entry.is_socket() or entry.is_block_device():
            continue

        yield entry

    if recurse and datadir == _example_dir:
        from ..cli.utils import determine_config_file_name

        yield determine_config_file_name(None, _root_dir)


def _data_file_path_id(
    datadir: Union[str, PurePath],
    name,
):
    try:
        return os.path.relpath(name, datadir)
    except TypeError:
        return str(name)


def pytest_generate_tests(metafunc):
    for fixture in metafunc.fixturenames:
        if fixture.endswith('example_file'):
            datadir = _example_dir
            dir_prefix = fixture[: -len('_example_file')]
            if dir_prefix:
                datadir = datadir.joinpath(*dir_prefix.split('_'))

            metafunc.parametrize(
                fixture,
                _data_file_paths(datadir, recurse=not dir_prefix, suffices={'.yml', '.yaml'}),
                ids=partial(_data_file_path_id, _example_dir),
            )
