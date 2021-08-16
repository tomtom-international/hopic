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

import click
import json
import logging
import re
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path
from textwrap import dedent

import git
import pytest

from ..cli import utils
from ..compat import metadata
from ..errors import ConfigurationError

_git_time = f"{42 * 365 * 24 * 3600} +0000"
_author = git.Actor('Bob Tester', 'bob@example.net')
_commitargs = dict(
        author_date=_git_time,
        commit_date=_git_time,
        author=_author,
        committer=_author,
    )


@pytest.mark.parametrize('expected_args', (
        ('--extra-index-url', 'https://test.pypi.org/simple/', 'hopic>=1.19<2',),
        ('--index-url', 'https://test.pypi.org/simple/', 'commisery>=0.2,<1',),
        ('flake8',),  # noqa: E201
))
def test_install_extensions_from_multiple_indices(monkeypatch, run_hopic, expected_args):
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
    (result,) = run_hopic(
        ("install-extensions",),
        config=config,
    )

    assert result.exit_code == 0


def test_with_single_extra_index(monkeypatch, run_hopic):
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

    (result,) = run_hopic(
        ("install-extensions",),
        config=dedent(
            f"""\
                pip:
                  - with-extra-index: {extra_index}
                    packages:
                      - {pkg}
            """
        ),
    )

    assert result.exit_code == 0


def test_recursive_extension_installation(monkeypatch, run_hopic):
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

    def mock_entry_points(*, group: str):
        assert group == "hopic.plugins.yaml"
        return (TestPipelinePackage(), TestTemplatePackage())

    monkeypatch.setattr(metadata, 'entry_points', mock_entry_points)

    (result,) = run_hopic(
        ("install-extensions",),
        config=dedent(
            f"""\
                pip:
                  - with-extra-index: {extra_index}
                    packages:
                      - {pkg}

                config: !template {pkg}
            """
        ),
    )

    assert result.exit_code == 0
    assert len(expected_pkg_install_order) == 0
    assert inner_template_called.pop()


def test_recursive_extension_installation_invalid_template_name(monkeypatch, run_hopic):
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

    def mock_entry_points(*, group: str):
        assert group == "hopic.plugins.yaml"
        return (TestPipelinePackage(),)

    monkeypatch.setattr(metadata, 'entry_points', mock_entry_points)

    (result,) = run_hopic(
        ("install-extensions",),
        config=dedent(
            f"""\
                pip:
                  - with-extra-index: {extra_index}
                    packages:
                      - {pkg}

                config: !template {pkg}
            """
        ),
    )

    assert result.exit_code == 0
    assert len(expected_pkg_install_order) == 0


def test_config_as_extension(monkeypatch, run_hopic):
    '''
    'config' item in hopic config yaml file should extend the existing config, not completely replace it.
    '''
    extra_index = 'https://test.pypi.org/simple/'
    template_pkg = 'template-pkg-extension'
    test_image_name = 'fake_docker_image'
    inner_template_called = []

    def mock_check_call(args, *popenargs, **kwargs):
        if '--user' in args:
            args.remove('--user')
        if '--verbose' in args:
            args.remove('--verbose')
        del args[4:6]

        assert [*args] == [sys.executable, '-m', 'pip', 'install', '--extra-index-url', extra_index, template_pkg]

    monkeypatch.setattr(subprocess, 'check_call', mock_check_call)

    def template(volume_vars):
        inner_template_called.append(True)
        return dedent(
            """\
              phases:
                test:
                  variant:
                    - echo 'bob'
            """)

    class TestTemplatePackage:
        name = template_pkg

        def load(self):
            return template

    def mock_entry_points(*, group: str):
        assert group == "hopic.plugins.yaml"
        return (TestTemplatePackage(),)

    monkeypatch.setattr(metadata, 'entry_points', mock_entry_points)

    hopic_ci_config = dedent(
        f"""\
            pip:
              - with-extra-index: {extra_index}
                packages:
                  - {template_pkg}

            image: {test_image_name}

            config: !template {template_pkg}
        """)

    (install_ext_result,) = run_hopic(("install-extensions",), config=hopic_ci_config)
    assert install_ext_result.exit_code == 0
    assert inner_template_called.pop()

    (result,) = run_hopic(("show-config",), config=hopic_ci_config)
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)
    # validate that items from top-level configuration are not removed
    assert output['image']['default'] == test_image_name
    # validate that items from config are added
    assert 'variant' in output['phases']['test']
    # validate that 'config' is actually removed to not trigger the process again
    assert 'config' not in output


def test_config_has_duplicated_keys(monkeypatch, run_hopic):
    '''
    'config' item only adds new items to the hopic config, if an item is already present, hopic should throw.
    '''
    extra_index = 'https://test.pypi.org/simple/'
    template_pkg = 'template-pkg-duplicate-keys'
    test_image_name = 'fake_docker_image'
    inner_template_called = []

    def mock_check_call(args, *popenargs, **kwargs):
        if '--user' in args:
            args.remove('--user')
        if '--verbose' in args:
            args.remove('--verbose')
        del args[4:6]

        assert [*args] == [sys.executable, '-m', 'pip', 'install', '--extra-index-url', extra_index, template_pkg]

    monkeypatch.setattr(subprocess, 'check_call', mock_check_call)

    def template(volume_vars):
        inner_template_called.append(True)
        return dedent("""\
                        phases:
                          test:
                            variant:
                              - echo 'bob'
                        """)

    class TestTemplatePackage:
        def __init__(self):
            self.name = template_pkg

        def load(self):
            return template

    def mock_entry_points(*, group: str):
        assert group == "hopic.plugins.yaml"
        return (TestTemplatePackage(),)

    monkeypatch.setattr(metadata, 'entry_points', mock_entry_points)

    (result,) = run_hopic(
        ("install-extensions",),
        config=dedent(
            f"""\
                pip:
                  - with-extra-index: {extra_index}
                    packages:
                      - {template_pkg}

                image: {test_image_name}

                phases:
                  test_custom:
                    variant_custom:
                      - echo 'jax'

                config: !template {template_pkg}
            """)
    )
    assert isinstance(result.exception, ConfigurationError)
    assert inner_template_called.pop()
    err = result.exception.format_message()
    assert "top level configuration and 'config' item have duplicated keys: {'phases'}" in err


def test_invalid_template_name(run_hopic):
    '''
    `hopic build` should return a clear error message when a template is specified
    that can't be found.
    '''

    (result,) = run_hopic(
        ("build",),
        config=dedent(
            '''
                config: !template xyzzy
            '''
        ),
    )

    assert result.exit_code != 0
    assert any("No YAML template named 'xyzzy' available (props={})" in msg for _, msg in result.logs)


def test_recursive_extension_installation_version_functionality(monkeypatch, run_hopic):
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

    def mock_entry_points(*, group: str):
        assert group == "hopic.plugins.yaml"
        monkeypatch.setattr(metadata, 'entry_points', lambda group: {'hopic.plugins.yaml': (FirstOrderTemplate(), SecondOrderTemplate())}[group])
        return (FirstOrderTemplateFirst(),)

    monkeypatch.setattr(metadata, 'entry_points', mock_entry_points)
    monkeypatch.setattr(subprocess, 'check_call', lambda *args, **kwargs: None)

    (result,) = run_hopic(
        ("build", "--dry-run"),
        config=dedent(
            f"""
            pip:
              - packages:
                - {first_pkg}

            config: !template {first_pkg}
            """
        ),
        tag="0.0.0"
    )

    assert result.exit_code == 0


def add_template(monkeypatch, pkg, config):
    def template(volume_vars):
        return config

    class TestPipelinePackage:
        name = pkg

        def load(self):
            return template

    def mock_entry_points(*, group: str):
        assert group == "hopic.plugins.yaml"
        return (TestPipelinePackage(),)

    monkeypatch.setattr(metadata, 'entry_points', mock_entry_points)


def run_default_merge_flow(monkeypatch, run_hopic, config, pkg, message, target='master', merge_message=None):
    template_config = dedent("""\
                                version:
                                    format: semver
                                    tag: true
                                    bump:
                                        policy: conventional-commits
                                        strict: yes
                                    """)
    if merge_message is None:
        merge_message = message
    with git.Repo.init(run_hopic.toprepo, expand_vars=False) as repo:
        with (run_hopic.toprepo / 'hopic-ci-config.yaml').open('w') as f:
            f.write(config)
        repo.index.add(('hopic-ci-config.yaml',))
        repo.index.commit(message='Initial commit', **_commitargs)
        repo.git.branch(target, move=True)
        repo.create_tag('0.0.0')

        # PR branch
        repo.head.reference = repo.create_head('something-useful')
        assert not repo.head.is_detached

        # Some change
        with (run_hopic.toprepo / 'something.txt').open('w') as f:
            f.write('usable')
        repo.index.add(('something.txt',))
        repo.index.commit(message=message, **_commitargs)

    # Successful checkout and build
    (*_, result) = run_hopic(
            ('checkout-source-tree', '--target-remote', run_hopic.toprepo, '--target-ref', target),
            lambda: add_template(monkeypatch, pkg, template_config),
            ('prepare-source-tree',
                '--author-date', f"@{_git_time}",
                '--commit-date', f"@{_git_time}",
                '--author-name', _author.name,
                '--author-email', _author.email,
                'merge-change-request', '--source-remote', run_hopic.toprepo, '--source-ref', 'something-useful', '--title', merge_message),
        )
    return result


@pytest.mark.parametrize('merge_message, commit_message, expected_result_code', (
    ("feat: new feat", "fix: bump mismatch", 36),
    ("fix: a fix", "fix: bump mismatch", 0),
))
def test_extension_installation_version_config(monkeypatch, run_hopic, merge_message, commit_message, expected_result_code):
    extra_index = 'https://test.pypi.org/simple/'
    pkg = 'pipeline-template'
    expected_pkg_install_order = [pkg]

    def mock_check_call(args, *popenargs, **kwargs):
        if '--user' in args:
            args.remove('--user')
        if '--verbose' in args:
            args.remove('--verbose')
        del args[4:6]
        if len(expected_pkg_install_order):
            assert [*args] == [
                sys.executable,
                '-m', 'pip', 'install', '--extra-index-url', extra_index,
                expected_pkg_install_order.pop(0)
            ]

    monkeypatch.setattr(subprocess, 'check_call', mock_check_call)

    result = run_default_merge_flow(
        monkeypatch,
        run_hopic,
        dedent(
            f"""\
                pip:
                  - with-extra-index: {extra_index}
                    packages:
                      - {pkg}

                config: !template {pkg}
            """
        ),
        pkg=pkg,
        message=commit_message,
        merge_message=merge_message
    )

    assert result.exit_code == expected_result_code
    assert len(expected_pkg_install_order) == 0


def test_add_hopic_config_with_template_in_pr(capfd, monkeypatch, run_hopic):
    extra_index = 'https://test.pypi.org/simple/'
    pkg = 'pipeline-template'
    template_config = dedent("""\
                version:
                    format: semver
                    tag: true
                    bump:
                        policy: conventional-commits
                        strict: yes

                phases:
                    first-phase:
                        first-variant:
                            - echo 'Hello World!'
                """)
    subprocess_call = subprocess.check_call

    def mock_check_call(args, *popenargs, **kwargs):
        if '--user' in args:
            args.remove('--user')
        if '--verbose' in args:
            args.remove('--verbose')
        del args[4:6]
        if not all(arg in args for arg in ['pip', 'install']):
            subprocess_call(args)

    monkeypatch.setattr(subprocess, 'check_call', mock_check_call)

    merge_message = "ci: add hopic"
    with git.Repo.init(run_hopic.toprepo, expand_vars=False) as repo:
        with (run_hopic.toprepo / 'something.txt').open('w') as f:
            f.write('usable')
        repo.index.add(('something.txt',))
        repo.index.commit(message='ci: add hopic commit', **_commitargs)
        repo.create_tag('0.0.0')

        # PR branch
        repo.head.reference = repo.create_head('something-useful')
        assert not repo.head.is_detached

        # add hopic config
        with (run_hopic.toprepo / 'hopic-ci-config.yaml').open('w') as f:
            f.write(dedent(f"""\
                    pip:
                      - with-extra-index: {extra_index}
                        packages:
                          - {pkg}

                    config: !template {pkg}
                """))
        repo.index.add(('hopic-ci-config.yaml',))
        repo.index.commit(message='ci: add hopic commit', **_commitargs)

    # Successful checkout and build
    (*_, result) = run_hopic(
            ('checkout-source-tree', '--target-remote', run_hopic.toprepo, '--target-ref', 'master'),
            lambda: add_template(monkeypatch, pkg, template_config),
            ('prepare-source-tree',
                '--author-date', f"@{_git_time}",
                '--commit-date', f"@{_git_time}",
                '--author-name', _author.name,
                '--author-email', _author.email,
                'merge-change-request', '--source-remote', run_hopic.toprepo, '--source-ref', 'something-useful', '--title', merge_message),
            ('build',)
        )

    assert result.exit_code == 0
    out, err = capfd.readouterr()
    sys.stdout.write(out)
    sys.stderr.write(err)
    out.splitlines()[-1] = 'Hello World!'


@pytest.mark.parametrize("constraints", (True, False))
@pytest.mark.parametrize("upgrade", (True, False))
def test_extension_constraints_upgrade_mutual_exclusivity(monkeypatch, run_hopic, constraints, upgrade):
    def mock_check_call(args, *popenargs, **kwargs):
        if args[2:4] == ["pip", "install"]:
            if upgrade:
                assert "--upgrade" in args and "--constraints" not in args
            elif constraints:
                assert "--constraints" in args and "--upgrade" not in args
            else:
                assert "--constraints" not in args and "--upgrade" not in args

    monkeypatch.setattr(subprocess, "check_call", mock_check_call)

    options = []
    if constraints:
        run_hopic.toprepo.mkdir()
        constraints_file = run_hopic.toprepo / "constraints.txt"
        constraints_file.write_text("test==1.2.3")
        options.extend(["--constraints", constraints_file.resolve()])
    if upgrade:
        options.append("--upgrade")

    (result,) = run_hopic(
        ("install-extensions", *options),
        config="",
    )
    if constraints and upgrade:
        assert isinstance(result.exception, click.BadOptionUsage)
        assert "mutually exclusive" in result.exception.format_message()
    else:
        assert result.exit_code == 0


def test_extension_constraints_functionality(monkeypatch, run_hopic, tmp_path):
    packages = (
        "commisery>=0.8.0",
        "somepackage==2.3.4",
        "someotherpackage==4.5.6",
    )
    constraint = "commisery==0.8.2"

    run_hopic.toprepo.mkdir()
    constraints_file = run_hopic.toprepo / "constraints.txt"
    constraints_file.write_text(constraint)

    options = ("--constraints", constraints_file.resolve())

    def mock_check_call(args, *popenargs, **kwargs):
        if args[2:4] == ["pip", "install"]:
            assert re.search(r"-c /[^ ]*/constraints.txt\b", " ".join(args)) is not None
            assert all(pkg in args for pkg in packages)

    monkeypatch.setattr(subprocess, "check_call", mock_check_call)

    with monkeypatch.context() as write_text_m:
        # The first `Path.write_text` we encounter will be from `install-extensions`,
        # so check that our input constraint is logged.

        def mock_write_text(f, text, *args, **kwargs):
            assert constraint in text
            write_text_m.undo()

        write_text_m.setattr(Path, "write_text", mock_write_text)

        config = dedent(
            f"""\
            pip:
              - packages: {json.dumps(packages)}
            """
        )

        (_, result) = run_hopic(
            (
                "install-extensions",
                *options,
            ),
            ("build",),
            config=config,
        )

    assert result.exit_code == 0


@pytest.mark.parametrize(
    "installed_pip_version, expected_result",
    (
        ("21.2.0", 0),
        ("0.0.0", 1),
        (None, 2),
    ),
)
def test_extension_constraints_pip_version(monkeypatch, run_hopic, tmp_path, installed_pip_version, expected_result):
    TEST_PACKAGE = "pip"

    def mock_check(package):
        if package == TEST_PACKAGE:
            if not installed_pip_version:
                raise metadata.PackageNotFoundError
            return installed_pip_version
        return original_pkg_version(package)

    monkeypatch.setattr(subprocess, "check_call", lambda *x, **_: None)

    original_pkg_version = monkeypatch.setattr(utils, "get_package_version", mock_check)

    run_hopic.toprepo.mkdir()
    constraints_file = run_hopic.toprepo / "constraints.txt"
    constraints_file.touch()

    options = ("--constraints", constraints_file.resolve())
    config = f"pip: [ packages: [ {TEST_PACKAGE} ] ]"

    (result,) = run_hopic(
        ("install-extensions", *options),
        config=config,
    )

    if expected_result == 1:
        assert any(level == logging.WARNING and "upgrade your pip package" in line for level, line in result.logs)
    elif expected_result == 2:
        assert any(level == logging.WARNING and "is not installed" in line for level, line in result.logs)

    assert result.exit_code == 0
