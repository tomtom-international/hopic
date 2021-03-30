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

import json
import re
from textwrap import dedent
import typing

import pytest

from . import config_file
from .. import config_reader
from ..errors import ConfigurationError


@pytest.fixture
def mock_yaml_plugin(monkeypatch):
    class TestTemplate:
        name = 'example'

        def load(self):
            return self.example_template

        @staticmethod
        def example_template(
            volume_vars : typing.Mapping[str, str],
            *,
            required_param : str,
            optional_param: 'typing.Optional[str]' = None,
            defaulted_param : bool = False,
        ) -> typing.Sequence[typing.Mapping[str, typing.Any]]:
            val = {
                'required': required_param,
                'defaulted': defaulted_param,
            }
            if optional_param is not None:
                val['optional'] = optional_param
            return (val,)

    class TestKwargTemplate:
        name = 'kwarg'

        def load(self):
            return self.kwarg_template

        @staticmethod
        def kwarg_template(
            volume_vars : typing.Mapping[str, str],
            *,
            required_param : str,
            **kwargs,
        ) -> typing.Sequence[typing.Mapping[str, typing.Any]]:
            val = {
                'required': required_param,
                'defaulted': kwargs.get('defaulted-param', False),
            }
            optional = kwargs.pop('optional-param', None)
            if optional is not None:
                val['optional'] = optional
            val['kwargs'] = kwargs

            assert isinstance(val['required'], str)
            return (val,)

    class TestSimpleTemplate:
        name = 'simple'

        def load(self):
            return self.no_arg_template

        @staticmethod
        def no_arg_template(volume_vars):
            return ()

    class TestSequenceTemplate:
        name = 'sequence'

        def load(self):
            return self.sequence_template

        @staticmethod
        def sequence_template(
            volume_vars : typing.Mapping[str, str],
            *,
            sequence: typing.List[str] = [],
        ) -> typing.Sequence[typing.Mapping[str, typing.Any]]:
            assert isinstance(sequence, typing.List)
            for v in sequence:
                assert isinstance(v, str)
            return ({'sequence': sequence},)

    class TestWrongDefaultTemplate:
        name = 'wrong-default'

        def load(self):
            return self.wrong_default_template

        @staticmethod
        def wrong_default_template(
            volume_vars : typing.Mapping[str, str],
            *,
            defaulted_param : 'str' = None,
        ) -> typing.Sequence[typing.Mapping[str, typing.Any]]:
            return ({
                'defaulted': defaulted_param,
            },)

    class TestWrongReturnTemplate:
        name = "wrong-return"

        def load(self):
            return self.wrong_return_template

        @staticmethod
        def wrong_return_template(
            volume_vars : typing.Mapping[str, str],
        ) -> typing.List[typing.Dict[str, typing.Any]]:
            return [
                {43: None},
            ]

    class TestNonGeneratorTemplate:
        name = "non-generator"

        def load(self):
            return self.non_generator_template

        @staticmethod
        def non_generator_template(
            volume_vars : typing.Mapping[str, str],
        ) -> typing.Generator:
            return [
                {"sh": ["ls"]},
            ]

    class TestGeneratorTemplate:
        name = 'generator'

        def load(self):
            return self.generator_template

        @staticmethod
        def generator_template(
            volume_vars : typing.Mapping[str, str],
            *,
            cmds: typing.List[str] = [],
        ) -> typing.Generator:
            yield "echo setup"
            yield from cmds
            yield "echo cleanup"

    class TestBadGeneratorTemplate:
        name = "bad-generator"

        def load(self):
            unwrapped = self.bad_generator_template
            while (
                hasattr(unwrapped, '__wrapped__')
                and getattr(unwrapped.__wrapped__, '__annotations__', None) is not None
                and getattr(unwrapped, '__annotations__') is unwrapped.__wrapped__.__annotations__
            ):
                unwrapped = unwrapped.__wrapped__
            return unwrapped

        @staticmethod
        def bad_generator_template(
            volume_vars : typing.Mapping[str, str],
        ) -> typing.Generator[typing.Mapping[str, typing.Any], None, None]:
            yield {"sh": ["ls"]}
            yield False

    def mock_entry_points():
        return {
            ep.name: ep
            for ep in [
                TestTemplate(),
                TestKwargTemplate(),
                TestSimpleTemplate(),
                TestSequenceTemplate(),
                TestWrongDefaultTemplate(),
                TestWrongReturnTemplate(),
                TestNonGeneratorTemplate(),
                TestGeneratorTemplate(),
                TestBadGeneratorTemplate(),
            ]
        }

    monkeypatch.setattr(config_reader, 'get_entry_points', mock_entry_points)


@pytest.mark.parametrize('version_build', (
    '',
    123,
    '@invalidmetadata',
    [1, 2, 3],
    (1,),
    {'name': 'value'},
    '+invalid',
    '.invalid',
))
def test_version_build_handles_invalid_values(version_build):
    with pytest.raises(ConfigurationError, match=r'version.build'):
        config_reader.read_version_info({}, {'build': version_build})


@pytest.mark.parametrize('version_build', (
    '1.70.0',
    '-oddbutvalid',
))
def test_version_build(version_build):
    result = config_reader.read_version_info({}, {'build': version_build})
    assert result['build'] == version_build


def test_version_build_non_semver():
    with pytest.raises(ConfigurationError, match=r'version.build'):
        config_reader.read_version_info({}, {'format': 'carver', 'build': '1.0.0'})


def test_environment_without_cmd():
    with pytest.raises(ConfigurationError, match=r"set 'environment' member .* doesn't have 'sh'"):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
                    phases:
                      test:
                        example:
                          - environment: {}
                    '''
                )
            ),
            {'WORKSPACE': None},
        )


def test_environment_type_mismatch():
    with pytest.raises(ConfigurationError, match=r"`environment\['sheep'\]` is not a string"):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
                    phases:
                      test:
                        example:
                          - environment:
                              sheep: 1
                            sh:
                              - printenv
                    '''
                )
            ),
            {'WORKSPACE': None},
        )


def test_environment_from_prefix():
    cfg = config_reader.read(
        config_file(
            "test-hopic-config.yaml",
            dedent(
                '''\
                phases:
                  test:
                    example:
                      - SHEEP=1 EMPTY= ./command.sh
                '''
            )
        ),
        {'WORKSPACE': None},
    )
    (out,) = cfg['phases']['test']['example']
    assert out['sh'] == ['./command.sh']
    assert dict(out['environment']) == {'SHEEP': '1', 'EMPTY': ''}


def test_node_label_type_mismatch():
    with pytest.raises(ConfigurationError, match=r"`node-label` .*? string .*? bool"):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
                    phases:
                      test:
                        example:
                          - node-label: true
                    '''
                )
            ),
            {'WORKSPACE': None},
        )


def test_node_label_mismatch():
    with pytest.raises(ConfigurationError, match=r"`node-label` .*?\bdiffers from .*?\bprevious.*?\bdefined"):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
                    phases:
                      build:
                        example:
                          - node-label: first
                      test:
                        example:
                          - node-label: second
                    '''
                )
            ),
            {'WORKSPACE': None},
        )


def test_node_label_mismatch_single_phase():
    with pytest.raises(ConfigurationError, match=r"`node-label` .*?\bdiffers from .*?\bprevious.*?\bdefined"):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
                    phases:
                      build:
                        example:
                          - node-label: first
                          - node-label: second
                    '''
                )
            ),
            {'WORKSPACE': None},
        )


def test_node_label_default_override():
    with pytest.raises(ConfigurationError, match=r"`node-label` .*?\boverride default"):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
                    phases:
                      build:
                        example: []
                      test:
                        example:
                          - node-label: second
                    '''
                )
            ),
            {'WORKSPACE': None},
        )


def test_post_submit_node_label_mismatch():
    with pytest.raises(ConfigurationError, match=r"`node-label` .*?\bdiffers from .*?\bprevious.*?\bdefined"):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
                    post-submit:
                      build:
                        - node-label: first
                      test:
                        - node-label: second
                    '''
                )
            ),
            {'WORKSPACE': None},
        )


def test_node_label_match():
    config_reader.read(
        config_file(
            "test-hopic-config.yaml",
            dedent(
                '''\
                phases:
                  build:
                    example:
                      - node-label: first
                  test:
                    example:
                      - node-label: first
                '''
            )
        ),
        {'WORKSPACE': None},
    )


def test_post_submit_node_label_match():
    config_reader.read(
        config_file(
            "test-hopic-config.yaml",
            dedent(
                '''\
                post-submit:
                  build:
                    - node-label: first
                  test:
                    - node-label: first
                '''
            )
        ),
        {'WORKSPACE': None},
    )


def test_template_reserved_param(mock_yaml_plugin):
    with pytest.raises(ConfigurationError, match=r'(?i)trying to use reserved keyword `volume-vars` to instantiate template `.*?`'):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
            phases:
              test:
                example: !template
                  name: example
                  required-param: PIPE
                  volume-vars: {}
                    '''
                )
            ),
            {'WORKSPACE': None}
        )


def test_template_missing_param(mock_yaml_plugin):
    with pytest.raises(ConfigurationError, match=r'(?i)trying to instantiate template `.*?` without required parameter'):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
            phases:
              test:
                example: !template "example"
                    '''
                )
            ),
            {'WORKSPACE': None}
        )


def test_template_mismatched_param_simple_type(mock_yaml_plugin):
    with pytest.raises(ConfigurationError, match=r'(?i)trying to instantiate template `.*?`: type of required-param must be str; got bool instead'):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
            phases:
              test:
                example: !template
                  name: example
                  required-param: yes
                    '''
                )
            ),
            {'WORKSPACE': None}
        )


def test_template_mismatched_param_optional_type(mock_yaml_plugin):
    with pytest.raises(ConfigurationError, match=r'(?i)trying to instantiate template `.*?`: type of optional-param must be .*?; got bool instead'):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
            phases:
              test:
                example: !template
                  name: example
                  optional-param: yes
                    '''
                )
            ),
            {'WORKSPACE': None}
        )


def test_template_snake_param(mock_yaml_plugin):
    with pytest.raises(
        ConfigurationError,
        match=r'(?i)trying to instantiate template `.*?` with unexpected parameter `required_param`.*? mean `required-param`',
    ):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
            phases:
              test:
                example: !template
                  name: example
                  required_param: PIPE
                    '''
                )
            ),
            {'WORKSPACE': None}
        )


def test_template_unknown_param(mock_yaml_plugin):
    with pytest.raises(ConfigurationError, match=r'(?i)trying to instantiate template `.*?` with unexpected parameter `unknown-param`'):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
            phases:
              test:
                example: !template
                  name: example
                  unknown-param: 42
                    '''
                )
            ),
            {'WORKSPACE': None}
        )


def test_template_without_optional_param(mock_yaml_plugin):
    cfg = config_reader.read(
        config_file(
            "test-hopic-config.yaml",
            dedent(
                '''\
        phases:
          test:
            example: !template
              name: example
              required-param: PIPE
                '''
            )
        ),
        {'WORKSPACE': None}
    )
    out, = cfg['phases']['test']['example']
    assert 'optional' not in out


def test_template_with_explicitly_null_param(mock_yaml_plugin):
    cfg = config_reader.read(
        config_file(
            "test-hopic-config.yaml",
            dedent(
                '''\
        phases:
          test:
            example: !template
              name: example
              required-param: PIPE
              optional-param: null
                '''
            )
        ),
        {'WORKSPACE': None}
    )
    out, = cfg['phases']['test']['example']
    assert 'optional' not in out


def test_template_with_optional_param(mock_yaml_plugin):
    cfg = config_reader.read(
        config_file(
            "test-hopic-config.yaml",
            dedent(
                '''\
        phases:
          test:
            example: !template
              name: example
              required-param: PIPE
              optional-param: Have you ever seen the rain?
                '''
            )
        ),
        {'WORKSPACE': None}
    )
    out, = cfg['phases']['test']['example']
    assert 'optional' in out
    assert out['optional'] == 'Have you ever seen the rain?'


def test_template_kwargs_required_snake_param(mock_yaml_plugin):
    with pytest.raises(
        ConfigurationError,
        match=r'(?i)trying to instantiate template `.*?` with unexpected parameter `required_param`.*? mean `required-param`',
    ):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
            phases:
              test:
                example: !template
                  name: kwarg
                  required_param: PIPE
                    '''
                )
            ),
            {'WORKSPACE': None}
        )


def test_template_kwargs_snake_param_in_kwarg(mock_yaml_plugin):
    cfg = config_reader.read(
        config_file(
            "test-hopic-config.yaml",
            dedent(
                '''\
        phases:
          test:
            example: !template
              name: kwarg
              required-param: PIPE
              something_extra-that_is-ridiculous: yes
                '''
            )
        ),
        {'WORKSPACE': None}
    )
    out, = cfg['phases']['test']['example']
    assert out['kwargs'] == {'something_extra-that_is-ridiculous': True}


def test_template_kwargs_missing_param(mock_yaml_plugin):
    with pytest.raises(ConfigurationError, match=r'(?i)trying to instantiate template `.*?` without required parameter'):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
            phases:
              test:
                example: !template "kwarg"
                    '''
                )
            ),
            {'WORKSPACE': None}
        )


def test_template_kwargs_type_mismatch(mock_yaml_plugin):
    with pytest.raises(ConfigurationError, match=r'(?i)trying to instantiate template `.*?`: type of required-param must be str; got NoneType instead'):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
            phases:
              test:
                example: !template
                  name: kwarg
                  required-param: null
                    '''
                )
            ),
            {'WORKSPACE': None}
        )


def test_template_simple_unknown_param(mock_yaml_plugin):
    with pytest.raises(ConfigurationError, match=r'(?i)trying to instantiate template `.*?` with unexpected parameter `unknown-param`'):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
            phases:
              test:
                example: !template
                  name: simple
                  unknown-param: 42
                    '''
                )
            ),
            {'WORKSPACE': None}
        )


def test_template_simple_without_param(mock_yaml_plugin):
    cfg = config_reader.read(
        config_file(
            "test-hopic-config.yaml",
            dedent(
                '''\
        phases:
          test:
            example: !template "simple"
                '''
            )
        ),
        {'WORKSPACE': None}
    )
    out = cfg['phases']['test']['example']
    assert out == []


def test_template_sequence_without_param(mock_yaml_plugin):
    cfg = config_reader.read(
        config_file(
            "test-hopic-config.yaml",
            dedent(
                '''\
        phases:
          test:
            example: !template "sequence"
                '''
            )
        ),
        {'WORKSPACE': None}
    )
    out = cfg['phases']['test']['example'][0]['sequence']
    assert out == []


def test_template_with_wrong_default(mock_yaml_plugin):
    with pytest.raises(ConfigurationError, match=r'(?i)\bwrong default of parameter for template `.*?`: type of defaulted-param must be str; got .*? instead'):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
            phases:
              test:
                example: !template
                  name: wrong-default
                  defaulted-param: mooh
                    '''
                )
            ),
            {'WORKSPACE': None}
        )


def test_template_sequence_with_single_entry(mock_yaml_plugin):
    cfg = config_reader.read(
        config_file(
            "test-hopic-config.yaml",
            dedent(
                '''\
        phases:
          test:
            example: !template
              name: "sequence"
              sequence:
                - mooh
                '''
            )
        ),
        {'WORKSPACE': None}
    )
    out = cfg['phases']['test']['example'][0]['sequence']
    assert out == ['mooh']


def test_template_sequence_with_str_instead_of_list(mock_yaml_plugin):
    with pytest.raises(ConfigurationError, match=r'(?i)trying to instantiate template `.*?`: type of sequence must be\b.*?\blist; got str instead'):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    """\
                    phases:
                      test:
                        example: !template
                          name: "sequence"
                          sequence: mooh
                    """
                )
            ),
            {'WORKSPACE': None},
        )


def test_template_sequence_with_type_mismatched_entry(mock_yaml_plugin):
    with pytest.raises(ConfigurationError, match=r'(?i)trying to instantiate template `.*?`: type of sequence\[1\] must be str; got bool instead'):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
            phases:
              test:
                example: !template
                  name: "sequence"
                  sequence:
                    - mooh
                    - false
                    - sheep
                    '''
                )
            ),
            {'WORKSPACE': None}
        )


def test_template_wrong_return(mock_yaml_plugin):
    with pytest.raises((ConfigurationError, TypeError), match=r"(?i)return value(?:\[.*?\])? must be .*?; got .*? instead"):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
            phases:
              test:
                example: !template wrong-return
                    '''
                )
            ),
            {'WORKSPACE': None}
        )


def test_template_non_generator(mock_yaml_plugin):
    with pytest.raises((ConfigurationError, TypeError), match=r"(?i)return value must be \S*\bGenerator; got list instead"):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
            phases:
              test:
                example: !template non-generator
                    '''
                )
            ),
            {'WORKSPACE': None}
        )


def test_template_generator(mock_yaml_plugin):
    cfg = config_reader.read(
        config_file(
            "test-hopic-config.yaml",
            dedent(
                '''\
        phases:
          test:
            example: !template
              name: generator
              cmds:
                - echo "do something"
                '''
            )
        ),
        {'WORKSPACE': None}
    )
    cmds = [cmd["sh"] for cmd in cfg["phases"]["test"]["example"]]
    assert cmds == [
        ["echo", "setup"],
        ["echo", "do something"],
        ["echo", "cleanup"],
    ]


def test_bad_generator_template(mock_yaml_plugin):
    with pytest.raises((ConfigurationError, TypeError), match=r"(?i)value yielded from generator\b.*?\bmust be (?:dict|\S*\bMapping); got bool instead"):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
            phases:
              test:
                example: !template bad-generator
                    '''
                )
            ),
            {'WORKSPACE': None}
        )


def test_nested_command_list_flattening():
    cfg = config_reader.read(
        config_file(
            "test-hopic-config.yaml",
            dedent(
                """\
                phases:
                  test:
                    example:
                      - run-on-change: always
                      -
                        - description: something happening here
                        -
                            - echo "Tada!"
                            - sh: echo "Tada!"
                            - sh: [echo, "Tada!"]
                """
            )
        ),
        {'WORKSPACE': None},
    )
    out = [dict(cmd) for cmd in cfg['phases']['test']['example']]
    assert out == [
        {"run-on-change": config_reader.RunOnChange.always},
        {"description": "something happening here"},
        {"environment": {}, "sh": ["echo", "Tada!"]},
        {"environment": {}, "sh": ["echo", "Tada!"]},
        {"environment": {}, "sh": ["echo", "Tada!"]},
    ]


def test_wait_on_full_previous_phase_dependency_type_mismatch():
    with pytest.raises(ConfigurationError, match=r"(?i)`wait-on-full-previous-phase` doesn't contain a boolean"):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    """\
                    phases:
                      y:
                        b:
                          - wait-on-full-previous-phase: noo
                    """
                )
            ),
            {'WORKSPACE': None},
        )


def test_wait_on_full_previous_phase_dependency_without_previous_phase():
    with pytest.raises(ConfigurationError, match=r"(?i)`wait-on-full-previous-phase` defined but there is no previous phase"):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    """\
                    phases:
                      x:
                        a:
                          - wait-on-full-previous-phase: no
                    """
                )
            ),
            {'WORKSPACE': None},
        )


def test_wait_on_full_previous_phase_dependency_without_previous_variant():
    with pytest.raises(ConfigurationError, match=r"(?i)`wait-on-full-previous-phase` disabled but previous phase `x` doesn't contain variant `c`"):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    """\
                    phases:
                      x:
                        a:
                          - {dep_option}
                        b:
                          - echo monkey
                      y:
                        b:
                          - wait-on-full-previous-phase: no
                        c:
                          - wait-on-full-previous-phase: no
                    """
                )
            ),
            {'WORKSPACE': None},
        )


def test_wait_on_full_previous_phase_dependency_multiple_definitions():
    with pytest.raises(ConfigurationError, match=r"(?i)`wait-on-full-previous-phase` defined multiple times"):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    """\
                    phases:
                      x:
                        a:
                          - {dep_option}
                        b:
                          - echo monkey
                      y:
                        b:
                          - wait-on-full-previous-phase: no
                          - wait-on-full-previous-phase: no
                    """
                )
            ),
            {'WORKSPACE': None},
        )


@pytest.mark.parametrize(
    "dep_option",
    (
        "run-on-change: never",
        "run-on-change: only",
        "run-on-change: new-version-only",
        "stash: {includes: test/**}",
        "worktrees: {doc/build/html: {commit-message: bla bla}}",
    ),
)
def test_wait_on_full_previous_phase_dependency_violation(dep_option):
    with pytest.raises(ConfigurationError, match=r"(?i)`wait-on-full-previous-phase` disabled but previous phase `x` uses dependency-creating options"):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    f"""\
                    phases:
                      x:
                        a:
                          - {dep_option}
                        b:
                          - echo monkey
                      y:
                        b:
                          - wait-on-full-previous-phase: no
                    """
                )
            ),
            {'WORKSPACE': None},
        )


def test_wait_on_full_previous_phase_dependency_run_on_change():
    with pytest.raises(ConfigurationError, match=r"(?i)`wait-on-full-previous-phase` disabled but `y`.`a`.`run-on-change` set to a value other than always"):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    """\
                    phases:
                      x:
                        a:
                          - run-on-change: always
                      y:
                        a:
                          - run-on-change: only
                            wait-on-full-previous-phase: no
                    """
                )
            ),
            {'WORKSPACE': None},
        )


def test_wait_on_full_previous_phase_dependency_default_yes():
    cfg = config_reader.read(
        config_file(
            "test-hopic-config.yaml",
            dedent(
                """\
                phases:
                  x:
                    b:
                      - touch monkey
                  y:
                    b:
                      - cat monkey
                    c:
                      - touch pig
                """
            )
        ),
        {'WORKSPACE': None},
    )
    (x_b,) = cfg['phases']['x']['b']
    (y_b,) = cfg['phases']['y']['b']
    (y_c,) = cfg['phases']['y']['c']
    assert 'wait-on-full-previous-phase' not in x_b
    assert y_b['wait-on-full-previous-phase'] is True
    assert 'wait-on-full-previous-phase' not in y_c


def test_docker_run_extra_arguments_wrong_type(capfd):
    with pytest.raises(ConfigurationError, match="`extra-docker-args` argument `hostname` for `v-one` should be a str, not a float"):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    """\
                    image:
                      default: buildpack-deps:18.04

                    phases:
                      p-one:
                        v-one:
                          - extra-docker-args:
                              hostname: 3.14
                          - echo This build shall fail
                    """
                )
            ),
            {'WORKSPACE': None},
        )


def test_ci_locks():
    cfg = config_reader.read(
        config_file(
            "test-hopic-config.yaml",
            dedent(
                '''\
        ci-locks:
            - branch: master
              repo-name: FICTIONAL/some-lock
            - branch: master
              repo-name: FICTIONAL/some-other-lock
              lock-on-change: never
                '''
            )
        ),
        {'WORKSPACE': None}
    )
    ci_locks = cfg['ci-locks']
    assert isinstance(ci_locks, list)
    assert ci_locks[0]['lock-on-change'] == 'always'
    assert ci_locks[1]['lock-on-change'] == 'never'


def test_ci_locks_wrong_lock_on_change_value():
    with pytest.raises(ConfigurationError, match='has an invalid attribute "lock-on-change", expected one of'):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
            ci-locks:
            - branch: master
              repo-name: FICTIONAL/some-other-lock
              lock-on-change: never123
                    '''
                )
            ),
            {'WORKSPACE': None}
        )


def test_ci_locks_wrong_branch_value():
    with pytest.raises(ConfigurationError, match='has an invalid attribute "branch", expected a str, but got a list'):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
            ci-locks:
            - branch:
              - master
              repo-name: FICTIONAL/some-other-lock
                    '''
                )
            ),
            {'WORKSPACE': None}
        )


def test_mutiple_options_on_archive():
    with pytest.raises(ConfigurationError, match=r"are not allowed in the same Archive configuration, use only 'allow-missing"):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
            phases:
              build:
                example:
                  - archive:
                     artifacts: doesnotexist.txt
                     allow-missing: true
                     allow-empty-archive: true
                    '''
                )
            ),
            {'WORKSPACE': None}
        )


def test_allow_empty_archive_empty_variant_removed():
    cfg = config_reader.read(
        config_file(
            "test-hopic-config.yaml",
            dedent(
                '''\
            phases:
              build:
                example:
                  - archive:
                     artifacts: doesnotexist.txt
                     allow-empty-archive: true
                '''
            )
        ),
        {'WORKSPACE': None}
    )

    out = cfg['phases']['build']['example'][0]['archive']
    assert 'allow-missing' in out
    assert type(out['allow-missing']) is bool
    assert 'allow-empty-archive' not in out


def test_archive_allow_missing_not_boolean():
    with pytest.raises(ConfigurationError, match=r"'build.example.archive.allow-missing' should be a boolean, not a str"):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
            phases:
              build:
                example:
                  - archive:
                     artifacts: doesnotexist.txt
                     allow-missing: 'true'
                    '''
                )
            ),
            {'WORKSPACE': None}
        )


def test_allow_empty_junit():
    with pytest.raises(ConfigurationError, match=r"JUnit configuration did not contain mandatory field 'test-results'"):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
            phases:
              build:
                example:
                  - junit:
                     test: doesnotexist.txt
                     allow-missing: true
                    '''
                )
            ),
            {'WORKSPACE': None}
        )


def test_generated_config_has_test_results():
    cfg = config_reader.read(
        config_file(
            "test-hopic-config.yaml",
            dedent(
                '''\
            phases:
              build:
                example:
                  - junit:
                     doesnotexistjunitresult.xml
                '''
            )
        ),
        {'WORKSPACE': None}
    )

    out = cfg['phases']['build']['example'][0]['junit']
    assert 'test-results' in out


def test_junit_allow_missing_not_boolean():
    with pytest.raises(ConfigurationError, match=r"'build.example.junit.allow-missing' should be a boolean, not a str"):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
            phases:
              build:
                example:
                  - junit:
                     test-results: doesnotexist.txt
                     allow-missing: 'true'
                    '''
                )
            ),
            {'WORKSPACE': None}
        )


def test_archive_type_mismatch():
    with pytest.raises(ConfigurationError, match=r"member is not a mapping"):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
                    phases:
                      test:
                        example:
                          - archive: null
                    '''
                )
            ),
            {'WORKSPACE': None},
        )


def test_archive_missing_artifacts():
    with pytest.raises(ConfigurationError, match=r"lacks the mandatory 'artifacts' member"):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
                    phases:
                      test:
                        example:
                          - archive: {}
                    '''
                )
            ),
            {'WORKSPACE': None},
        )


def test_archive_artifacts_missing_pattern():
    with pytest.raises(ConfigurationError, match=r"lacks the mandatory 'pattern' member"):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
                    phases:
                      test:
                        example:
                          - archive:
                              artifacts:
                                - {}
                    '''
                )
            ),
            {'WORKSPACE': None},
        )


def test_archive_artifacts_pattern_type_mismatch():
    with pytest.raises(ConfigurationError, match=r"pattern' is not a string"):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
                    phases:
                      test:
                        example:
                          - archive:
                              artifacts:
                                - pattern: null
                    '''
                )
            ),
            {'WORKSPACE': None},
        )


@pytest.mark.parametrize('pattern', (
    "**/a**",
    "**/a(*)(*)",
))
def test_archive_artifacts_pattern_invalid_double_star(pattern):
    with pytest.raises(
        ConfigurationError,
        match=fr"pattern' value of '{re.escape(pattern)}' is not a valid glob pattern: .*? '\*\*' can only be an entire path component",
    ):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    f"""\
                    phases:
                      test:
                        example:
                          - archive:
                              artifacts:
                                - pattern: {json.dumps(pattern)}
                    """
                )
            ),
            {'WORKSPACE': None},
        )


def test_junit_pattern_invalid_double_star():
    with pytest.raises(ConfigurationError, match=r"value of '.*?' is not a valid glob pattern: .*? '\*\*' can only be an entire path component"):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
                    phases:
                      test:
                        example:
                          - junit:
                            - "**/a**"
                    '''
                )
            ),
            {'WORKSPACE': None},
        )


def test_ci_locks_reference_invalid_phase():
    with pytest.raises(ConfigurationError, match=r"referenced phase in ci-locks \(non-existing-phase\) doesn't exist"):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
                    ci-locks:
                        - branch: branch
                          repo-name: repo
                          from-phase-onward: non-existing-phase

                    phases:
                      existing-phase:
                        example:
                          - echo 'test'
                    '''
                )
            ),
            {'WORKSPACE': None},
        )


def test_ci_locks_reference_wait_on_full_previous_phase_variant():
    with pytest.raises(ConfigurationError, match=r"referenced phase in ci-locks \(phase-2\) refers to variant \(wait-on-full-previous-phase-variant\) "
                                                 r"that has wait-on-full-previous-phase disabled"):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
                    ci-locks:
                        - branch: branch
                          repo-name: repo
                          from-phase-onward: phase-2

                    phases:
                      phase-1:
                        example:
                          - echo 'test'

                        wait-on-full-previous-phase-variant:
                          - echo 'disable waiting on previous phase'

                      phase-2:
                        example:
                          - echo 'test'

                        wait-on-full-previous-phase-variant:
                          - wait-on-full-previous-phase: no

                    '''
                )
            ),
            {'WORKSPACE': None},
        )


def test_ci_locks_duplicate_identifier():
    with pytest.raises(ConfigurationError, match=r"ci-lock with repo-name 'repo' and branch 'branch' already exists, "
                                                 r"this would lead to a deadlock"):
        config_reader.read(
            config_file(
                "test-hopic-config.yaml",
                dedent(
                    '''\
                    ci-locks:
                        - branch: branch
                          repo-name: repo
                          from-phase-onward: phase-1
                        - branch: branch
                          repo-name: repo

                    phases:
                      phase-1:
                        example:
                          - echo 'test'
                    '''
                )
            ),
            {'WORKSPACE': None},
        )


def test_ci_locks_on_phase_forward():
    cfg = config_reader.read(
        config_file(
            "test-hopic-config.yaml",
            dedent(
                '''\
                ci-locks:
                  - branch: branch
                    repo-name: repo
                    from-phase-onward: phase-1

                phases:
                  phase-1:
                    example:
                      - echo 'test'
                '''
            )
        ),
        {'WORKSPACE': None},
    )
    out = cfg['ci-locks'][0]
    assert 'from-phase-onward' in out
