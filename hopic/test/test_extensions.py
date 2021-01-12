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

from . import hopic_cli

from click.testing import CliRunner

try:
    # Python >= 3.8
    from importlib import metadata
except ImportError:
    import importlib_metadata as metadata
import git
import json
import pytest
import subprocess
import sys
from textwrap import dedent

_git_time = f"{42 * 365 * 24 * 3600} +0000"
_author = git.Actor('Bob Tester', 'bob@example.net')
_commitargs = dict(
    author_date=_git_time,
    commit_date=_git_time,
    author=_author,
    committer=_author,
)


def run_with_config(config, *args, env=None):
    runner = CliRunner(mix_stderr=False, env=env)
    with runner.isolated_filesystem():
        with git.Repo.init() as repo:
            with open('hopic-ci-config.yaml', 'w') as f:
                f.write(config)
            repo.index.add(('hopic-ci-config.yaml',))
            repo.index.commit(message='Initial commit', **_commitargs)
            repo.create_tag('0.0.0')
        for arg in args:
            result = runner.invoke(hopic_cli, arg)

            if result.stdout_bytes:
                print(result.stdout, end='')
            if result.stderr_bytes:
                print(result.stderr, end='', file=sys.stderr)

            if result.exception is not None and not isinstance(result.exception, SystemExit):
                raise result.exception

            yield result

            if result.exit_code != 0:
                return


@pytest.mark.parametrize('expected_args', (
        ('--extra-index-url', 'https://test.pypi.org/simple/', 'hopic>=1.19<2',),
        ('--index-url', 'https://test.pypi.org/simple/', 'commisery>=0.2,<1',),
        ('flake8',),  # noqa: E201
))
def test_install_extensions_from_multiple_indices(monkeypatch, expected_args):
    def mock_check_call(args, *popenargs, **kwargs):
        if '--user' in args:
            args.remove('--user')
        if '--verbose' in args:
            args.remove('--verbose')
        # del ['-c', constraints_file]
        del args[4:6]

        assert [*args] == [sys.executable, '-m', 'pip', 'install', *expected_args]

    monkeypatch.setattr(subprocess, 'check_call', mock_check_call)

    if expected_args[0] == '--extra-index-url':
        config = dedent(f"""\
                pip:
                  - with-extra-index:
                      - {expected_args[1]}
                    packages:
                      - {expected_args[-1]}
                """)
    elif expected_args[0] == '--index-url':
        config = dedent(f"""\
                pip:
                  - from-index: {expected_args[1]}
                    packages:
                      - {expected_args[-1]}
                """)
    else:
        assert not expected_args[0].startswith('-')
        config = dedent(f"""\
                pip: {json.dumps(expected_args)}
                """)
    result, = run_with_config(
        config,
        ('install-extensions',))

    assert result.exit_code == 0


def test_with_single_extra_index(monkeypatch):
    extra_index = 'https://test.pypi.org/simple/'
    pkg = 'hopic>=1.19<2'

    def mock_check_call(args, *popenargs, **kwargs):
        if '--user' in args:
            args.remove('--user')
        if '--verbose' in args:
            args.remove('--verbose')
        # del ['-c', constraints_file]
        del args[4:6]

        assert [*args] == [sys.executable, '-m', 'pip', 'install', '--extra-index-url', extra_index, pkg]

    monkeypatch.setattr(subprocess, 'check_call', mock_check_call)

    result, = run_with_config(
        dedent(f"""\
                pip:
                  - with-extra-index: {extra_index}
                    packages:
                      - {pkg}
                """),
        ('install-extensions',))

    assert result.exit_code == 0


def test_recursive_extension_installation(monkeypatch):
    extra_index = 'https://test.pypi.org/simple/'
    pkg = 'pipeline-template'
    template_pkg = 'template-in-template'
    expected_pkg_install_order = [pkg, template_pkg]
    inner_template_called = []

    def mock_check_call(args, *popenargs, **kwargs):
        if '--user' in args:
            args.remove('--user')
        if '--verbose' in args:
            args.remove('--verbose')
        del args[4:6]

        assert [*args] == [sys.executable, '-m', 'pip', 'install', '--extra-index-url', extra_index,
                           expected_pkg_install_order.pop(0)]

    monkeypatch.setattr(subprocess, 'check_call', mock_check_call)

    def template_template(volume_vars):
        inner_template_called.append(True)
        return dedent("""\
                        phases:
                          test:
                            variant:
                              - echo 'bob'
                        """)

    class TestTemplatePackage:
        name = template_pkg

        def load(self):
            return template_template

    def pipeline_template(volume_vars):
        return dedent(f"""\
                        pip:
                          - with-extra-index: {extra_index}
                            packages:
                              - {template_pkg}

                        config: !template {template_pkg}
                        """)

    class TestPipelinePackage:
        name = pkg

        def load(self):
            return pipeline_template

    def mock_entry_points():
        return {
            'hopic.plugins.yaml': (TestPipelinePackage(), TestTemplatePackage())
        }

    monkeypatch.setattr(metadata, 'entry_points', mock_entry_points)

    result, = run_with_config(
        dedent(f"""\
                pip:
                  - with-extra-index: {extra_index}
                    packages:
                      - {pkg}

                config: !template {pkg}
                """),
        ('install-extensions',))

    assert result.exit_code == 0
    assert len(expected_pkg_install_order) == 0
    assert inner_template_called.pop()


def test_recursive_extension_installation_invalid_template_name(monkeypatch):
    extra_index = 'https://test.pypi.org/simple/'
    pkg = 'pipeline-template'
    template_pkg = 'template-in-template'
    expected_pkg_install_order = [pkg, template_pkg]

    def mock_check_call(args, *popenargs, **kwargs):
        if '--user' in args:
            args.remove('--user')
        if '--verbose' in args:
            args.remove('--verbose')
        del args[4:6]

        assert [*args] == [sys.executable, '-m', 'pip', 'install', '--extra-index-url', extra_index,
                           expected_pkg_install_order.pop(0)]

    monkeypatch.setattr(subprocess, 'check_call', mock_check_call)

    def pipeline_template(volume_vars):
        return dedent(f"""\
                        pip:
                          - with-extra-index: {extra_index}
                            packages:
                              - {template_pkg}

                        config: !template {template_pkg}-not-available
                        """)

    class TestPipelinePackage:
        def __init__(self):
            self.name = f'{pkg}'

        def load(self):
            return pipeline_template

    def mock_entry_points():
        return {
            'hopic.plugins.yaml': (TestPipelinePackage(),)
        }

    monkeypatch.setattr(metadata, 'entry_points', mock_entry_points)

    result, = run_with_config(
        dedent(f"""\
                pip:
                  - with-extra-index: {extra_index}
                    packages:
                      - {pkg}

                config: !template {pkg}
                """),
        ('install-extensions',))

    assert result.exit_code == 0
    assert len(expected_pkg_install_order) == 0


def test_invalid_template_name(capfd):
    '''
    `hopic build` should return a clear error message when a template is specified
    that can't be found.
    '''

    result, = run_with_config(dedent('''
                config: !template xyzzy
                '''), ('build',))

    assert result.exit_code != 0

    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)
    assert "No YAML template named 'xyzzy' available (props={})" in err.strip()


@pytest.mark.xfail
def test_recursive_extension_installation_version_functionality(monkeypatch):
    first_pkg = 'firstorder'
    second_pkg = 'secondorder'

    class FirstOrderTemplateFirst:
        def __init__(self):
            self.name = first_pkg

        @staticmethod
        def first_order_template_first_call(volume_vars):
            return dedent(
                f"""
                pip:
                  - packages:
                    - {second_pkg}

                phases:
                  yaml-error:
                    unknown:
                      - echo Unknown template
                """
            )

        def load(self):
            return self.first_order_template_first_call

    class FirstOrderTemplate:
        def __init__(self):
            self.name = first_pkg

        @staticmethod
        def first_order_template(volume_vars):
            return dedent(
                f"""
                pip:
                  - packages:
                    - {second_pkg}

                version:
                  tag: true
                  format: semver
                  bump:
                    policy: conventional-commits
                    strict: yes

                phases:
                  phase-one:
                    variant-one: !template {second_pkg}
                """
            )

        def load(self):
            return self.first_order_template

    class SecondOrderTemplate:
        name = second_pkg

        @staticmethod
        def second_order_template(volume_vars):
            return "- echo $PUBLISH_VERSION"

        def load(self):
            return self.second_order_template

    def mock_entry_points():
        monkeypatch.setattr(metadata, 'entry_points', lambda: {'hopic.plugins.yaml': (FirstOrderTemplate(), SecondOrderTemplate())})
        return {
            'hopic.plugins.yaml': (FirstOrderTemplateFirst(),)
        }

    monkeypatch.setattr(metadata, 'entry_points', mock_entry_points)
    monkeypatch.setattr(subprocess, 'check_call', lambda *args, **kwargs: None)

    result, = run_with_config(
        dedent(
            f"""
            pip:
              - packages:
                - {first_pkg}

            config: !template {first_pkg}
            """
        ),
        ('build', '--dry-run')
    )

    assert result.exit_code == 0
