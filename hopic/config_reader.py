# Copyright (c) 2018 - 2021 TomTom N.V. (https://tomtom.com)
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

from .execution import echo_cmd

from collections import OrderedDict
from collections.abc import (
        Generator,
        Mapping,
        Sequence,
    )
from decimal import Decimal
from enum import Enum
import errno
from functools import lru_cache
import inspect
import io
import itertools
import json
import keyword
import logging
from numbers import Real
import os
from pathlib import (
    Path,
    PurePath,
)
import re
import shlex
import subprocess
import sys
from textwrap import dedent
import typeguard
import typing
import xml.etree.ElementTree as ET
import yaml

if sys.version_info[:2] >= (3, 8):
    from typing import (
        Literal,
        TypedDict,
    )
else:
    from typing_extensions import (
        Literal,
        TypedDict,
    )

if sys.version_info[:2] >= (3, 7):
    from typing import ForwardRef
else:
    from typing import _ForwardRef as ForwardRef  # type: ignore[attr-defined]

from .compat import metadata
from .errors import ConfigurationError
from .types import PathLike

__all__ = (
    'RunOnChange',
    'expand_vars',
    'read',
    'expand_docker_volume_spec',
)

log = logging.getLogger(__name__)

Pattern = type(re.compile(''))
EmptyDict = TypedDict("EmptyDict", {}, total=True)


_interphase_dependent_meta = frozenset({
    'stash',
    'worktrees',
})
_env_var_re = re.compile(r'^(?P<var>[A-Za-z_][0-9A-Za-z_]*)=(?P<val>.*)$')


class RunOnChange(str, Enum):
    """
    The :option:`run-on-change` option allows you to specify when a step needs to be executed.
    The value of this option can be one of:
    """
    always           = 'always'
    """The steps will always be performed. (Default if not specified)."""
    never            = 'never'
    """The steps will never be performed for a change."""
    only             = 'only'
    """The steps will only be performed when the change is to be submitted in the current execution."""
    new_version_only = 'new-version-only'
    """The steps will only be performed when the change is on a new version and is to be submitted in the current execution."""

    default = always


class LockOnChange(str, Enum):
    """
    The :option:`lock-on-change` option allows you to specify when additional locks needs to be acquired.
    The value of this option can be one of:
    """
    always           = 'always'
    """Additional lock will always be acquired. (Default if not specified)."""
    never            = 'never'
    """Additional lock will never be acquired"""
    new_version_only = 'new-version-only'
    """Additional lock will only be acquired when the version was bumped and is to be submitted in the current execution."""

    default = always


class CredentialType(str, Enum):
    username_password = 'username-password'
    file              = 'file'
    string            = 'string'
    ssh_key           = 'ssh-key'

    default = username_password


class CredentialEncoding(str, Enum):
    plain   = 'plain'
    url     = 'url'

    default = plain


_variable_interpolation_re = re.compile(
    r"""
    # start with a non-escaped dollar
    (?<!\$)\$
    (?:
        # variable name without surrounding curly braces
        (\w+) |
        \{
            # variable name surround by curly braces
            ([^}$:]+)
            # optional fallback text
            (?:
            :-
            ([^}$]*)
            )?
        \}
    )
""",
    re.VERBOSE,
)


def expand_vars(vars, expr):  # noqa: E302 'expected 2 blank lines'
    if isinstance(expr, str):
        # Expand variables from our "virtual" environment
        last_idx = 0
        new_val = expr[:last_idx]
        for var in _variable_interpolation_re.finditer(expr):
            name = var.group(1) or var.group(2)
            default = var.group(3)
            if default is not None:
                value = vars.get(name, default)
            else:
                value = vars[name]
            if isinstance(value, Exception):
                raise value
            new_val = new_val + expr[last_idx:var.start()].replace('$$', '$') + value
            last_idx = var.end()

        new_val = new_val + expr[last_idx:].replace('$$', '$')
        return new_val
    if hasattr(expr, 'items'):
        expr = expr.copy()
        for key, val in expr.items():
            expr[key] = expand_vars(vars, expr[key])
        return expr
    try:
        return [expand_vars(vars, val) for val in expr]
    except TypeError:
        return expr


class TemplateNotFoundError(ConfigurationError):
    def __init__(self, name, props):
        self.name = name
        self.props = props
        super().__init__(self.format_message())

    def format_message(self):
        return f"No YAML template named '{self.name}' available (props={self.props})"


class OrderedLoader(yaml.SafeLoader):
    pass


def __yaml_construct_mapping(loader, node):
    loader.flatten_mapping(node)
    d = OrderedDict()
    for key, value in loader.construct_pairs(node):
        if key in d:
            raise ConfigurationError(f"Duplicate entry for key {key!r} in a mapping is not permitted")
        d[key] = value
    return d


OrderedLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, __yaml_construct_mapping)


def get_toolchain_image_information(dependency_manifest):
    """Returns, as a dictionary, the dependency in the given manifest that refers to the toolchain image to be used."""
    tree = ET.parse(dependency_manifest)

    def refers_to_toolchain(dependency):
        confAttribute = dependency.get("conf")
        if confAttribute and "toolchain" in confAttribute:
            return True

        for child in dependency:
            if child.tag == "conf":
                mappedAttribute = child.get("mapped")
                if mappedAttribute == "toolchain":
                    return True
        return False

    toolchain_dep, = (
        dep.attrib for dep in tree.getroot().find("dependencies") if refers_to_toolchain(dep))

    return toolchain_dep


class IvyManifestImage:
    def __init__(self, volume_vars, loader, node):
        self.props = loader.construct_mapping(node) if node.value else {}
        self.volume_vars = volume_vars

    def __str__(self):
        if 'manifest' in self.props:
            manifest = expand_vars(self.volume_vars, self.props['manifest'])
            for dir in ('CFGDIR', 'WORKSPACE'):
                if dir not in self.volume_vars:
                    continue
                manifest = os.path.join(self.volume_vars[dir], manifest)
        else:
            # Fall back to searching for dependency_manifest.xml in these directories
            for dir in ('WORKSPACE', 'CFGDIR'):
                if dir not in self.volume_vars:
                    continue
                manifest = os.path.join(self.volume_vars[dir], 'dependency_manifest.xml')
                if os.path.exists(manifest):
                    break
        if not os.path.exists(manifest):
            raise FileNotFoundError(errno.ENOENT, "required ivy manifest file is not found", os.path.abspath(manifest))

        image = get_toolchain_image_information(manifest)

        # Override dependency manifest with info from config
        image.update(self.props)

        # Construct a full, pullable, image path
        image['image'] = '/'.join(path for path in (image.get('repository'), image.get('path'), image['name']) if path)

        return '{image}:{rev}'.format(**image)


def get_default_error_variant(error_msg):
    error_str = 'An error occurred when parsing the hopic configuration file\n'
    return OrderedDict({'error-variant': [f'echo -e {shlex.quote("{}{}".format(error_str, error_msg))}', 'sh -c \'exit 42\'']})


# Non failure function in order to always be able to load hopic file, use default (error) variant in case of error
def load_embedded_command(volume_vars, loader, node):
    try:
        props = loader.construct_mapping(node) if node.value else {}
        if 'cmd' not in props:
            raise ConfigurationError('No \'cmd\' found for !embed')

        cmd = shlex.split(props['cmd'])
        for dir in ('CFGDIR', 'WORKSPACE'):
            if dir not in volume_vars:
                continue

            script_file = os.path.join(volume_vars[dir], cmd[0])
            if os.path.exists(script_file):
                cmd[0] = script_file
                break

        script_output = echo_cmd(subprocess.check_output, cmd)
        yaml_load = yaml.load(script_output, OrderedLoader)

    except Exception as e:
        log.error("Fatal error occurred, using empty variant %s" % format(e))
        return get_default_error_variant(e)

    return yaml_load


class JSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, (IvyManifestImage, Path)):
            return str(o)
        elif isinstance(o, Pattern):
            return o.pattern
        elif isinstance(o, (set, frozenset)):
            return list(o)
        return super().default(o)


def match_template_props_to_signature(
    template_name: str,
    signature: typing.Mapping[str, inspect.Parameter],
    params: typing.Mapping[str, typing.Any],
    *,
    globals: typing.Optional[typing.Dict[str, typing.Any]] = None,
    locals: typing.Optional[typing.Dict[str, typing.Any]] = None,
) -> typing.Mapping[str, typing.Any]:

    kwargs_var, *_ = [
        *[
            param for param in signature.values()
            if param.kind == inspect.Parameter.VAR_KEYWORD
        ],
        None
    ]

    required_params = [
        param for param_idx, param in enumerate(signature.values())
        if param.kind in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }
        and param.default is inspect.Parameter.empty
        # Skip first 'volume_vars' parameter, because Hopic passes that one instead of the user.
        and param_idx != 0
    ]

    new_params = OrderedDict()
    for prop, val in params.items():
        # Translate kebab-case to snake_case when the snake_case parameter exists
        orig_prop = prop
        snake_prop = prop.replace('-', '_')
        if (
            snake_prop in signature
            and prop not in signature
            # Prevent duplicates
            and snake_prop not in params
        ):
            prop = snake_prop

        if '_' in orig_prop and (
            kwargs_var is None
            or prop in signature
        ):
            # Disallow usage of snake_case as an alternate spelling of kebab-case parameters.
            kebab_name = prop.replace('_', '-')
            raise ConfigurationError(
                f"Trying to instantiate template `{template_name}` with unexpected parameter `{orig_prop}`. Did you mean `{kebab_name}`?")

        try:
            param = signature[prop]
        except KeyError as exc:
            if kwargs_var is not None:
                new_params[orig_prop] = val
                continue
            raise ConfigurationError(f"Trying to instantiate template `{template_name}` with unexpected parameter `{orig_prop}`") from exc
        else:
            new_params[prop] = val

        annotation = param.annotation
        if annotation is not inspect.Parameter.empty:
            # Ensure we check forward references to types too
            if isinstance(annotation, str):
                annotation = ForwardRef(annotation)
            try:
                typeguard.check_type(argname=orig_prop, value=val, expected_type=annotation, globals=globals, locals=locals)
            except TypeError as exc:
                raise ConfigurationError(f"Trying to instantiate template `{template_name}`: {exc}") from exc

        if param.kind in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        } or param.name == 'volume_vars':
            raise ConfigurationError(f"Trying to use reserved keyword `{orig_prop}` to instantiate template `{template_name}`")

    for param in required_params:
        if param.name not in new_params:
            kebab_name = param.name.replace('_', '-')
            raise ConfigurationError(f"Trying to instantiate template `{template_name}` without required parameter `{kebab_name}`")

    # Complain about templates with defaults that mismatch their own type annotation
    for param in signature.values():
        default = param.default
        annotation = param.annotation
        if default is inspect.Parameter.empty or annotation is inspect.Parameter.empty:
            continue
        if isinstance(annotation, str):
            annotation = ForwardRef(annotation)
        name = param.name
        if kwargs_var is None:
            name = name.replace('_', '-')

        try:
            typeguard.check_type(argname=name, value=default, expected_type=annotation, globals=globals, locals=locals)
        except TypeError as exc:
            raise ConfigurationError(f"Wrong default of parameter for template `{template_name}`: {exc}") from exc

    return new_params


@lru_cache()
def get_entry_points():
    return {ep.name: ep for ep in metadata.entry_points(group="hopic.plugins.yaml")}


def load_config_section(cfg):
    if 'config' not in cfg:
        return cfg

    allowed_duplicates = {'config', 'pass-through-environment-vars', 'pip'}
    illegal_duplicates = set(cfg.keys()) & set(cfg["config"].keys()) - allowed_duplicates
    if illegal_duplicates:
        raise ConfigurationError(f"top level configuration and 'config' item have duplicated keys: {illegal_duplicates}")

    config_item = cfg.pop('config')
    cfg.pop('pip', None)
    pass_through_environment_vars_item = cfg.pop("pass-through-environment-vars", None)
    if pass_through_environment_vars_item:
        config_item.setdefault("pass-through-environment-vars", []).extend(
            environment_var
            for environment_var in pass_through_environment_vars_item
            if environment_var not in config_item.get("pass-through-environment-vars", [])
        )
    cfg.update(config_item)
    return cfg


def load_yaml_template(volume_vars, extension_installer, loader, node):
    if node.id == 'scalar':
        props = {}
        name = loader.construct_scalar(node)
    else:
        props = loader.construct_mapping(node, deep=True)
        name = props.pop('name')

    try:
        template_fn = get_entry_points()[name].load()
    except KeyError as exc:
        raise TemplateNotFoundError(name=name, props=props) from exc

    template_sig = inspect.signature(template_fn)
    rt_type = template_sig.return_annotation
    if rt_type is inspect.Signature.empty:
        rt_type = typing.Any

    # Unwrap stacked decorators to get at the underlying function's annotations
    unwrapped = template_fn
    while (
        hasattr(unwrapped, '__wrapped__')
        and getattr(unwrapped.__wrapped__, '__annotations__', None) is not None
        and getattr(unwrapped, '__annotations__') is unwrapped.__wrapped__.__annotations__
    ):
        unwrapped = unwrapped.__wrapped__
    template_globals = getattr(unwrapped, '__globals__', None)

    props = match_template_props_to_signature(name, template_sig.parameters, props, globals=template_globals)
    cfg = template_fn(volume_vars, **props)

    try:
        typeguard.check_type(argname="return value", value=cfg, expected_type=rt_type, globals=template_globals)
    except TypeError as exc:
        raise ConfigurationError(f"Trying to instantiate template `{name}`: {exc}") from exc

    if isinstance(cfg, str):
        # Parse provided yaml without template substitution
        install_top_level_extensions(cfg, name, extension_installer, volume_vars)
        cfg = yaml.load(cfg, ordered_config_loader(volume_vars, extension_installer))
        cfg = load_config_section(cfg)
    elif isinstance(cfg, Generator):
        yielded_type = typing.Any
        if getattr(rt_type, "__origin__", None) in (typing.Generator, Generator):
            rt_args = getattr(rt_type, "__args__", None)
            if rt_args:
                yielded_type = rt_args[0]

        new_cfg = []
        idx = 0
        cfg = iter(cfg)
        while True:
            try:
                value = next(cfg)
            except StopIteration:
                break
            try:
                typeguard.check_type(argname=f"value yielded from generator at index {idx}", value=value, expected_type=yielded_type, globals=template_globals)
            except TypeError as exc:
                # Raise the exception from the yield statement that returned the last value instead of here
                cfg.throw(ConfigurationError(f"Trying to instantiate template `{name}`: {exc}"))

            idx += 1
            new_cfg.append(value)
        cfg = new_cfg

    return cfg


def ordered_config_loader(volume_vars, extension_installer, template_parsing=True):
    def pass_volume_vars(f):
        return lambda *args: f(volume_vars, *args)

    def pass_volume_vars_and_extension_installer(f):
        return lambda *args: f(volume_vars, extension_installer, *args)

    OrderedConfigLoader = type('OrderedConfigLoader', (OrderedLoader,), {})

    OrderedConfigLoader.add_constructor(
        '!image-from-ivy-manifest',
        pass_volume_vars(IvyManifestImage)
    )
    OrderedConfigLoader.add_constructor(
        '!embed',
        pass_volume_vars(load_embedded_command)
    )

    OrderedConfigLoader.add_constructor(
        '!template',
        pass_volume_vars_and_extension_installer(load_yaml_template) if template_parsing else pass_volume_vars(
            lambda *args, **kwargs: "")
    )

    return OrderedConfigLoader


def expand_docker_volumes_from(volume_vars, volumes_from_vars):
    # Glue the Docker image name together with the (mandatory) version and expand
    volumes = []
    for volume in volumes_from_vars:
        if 'image-name' not in volume or 'image-version' not in volume:
            raise ConfigurationError('`volumes-from` requires `image-name` and `image-version` to be provided')
        image_name = volume['image-name']
        image_version = volume['image-version']
        volume['image'] = expand_vars(volume_vars, os.path.expanduser(":".join((image_name, image_version))))
        volumes.append(volume)

    return volumes


def expand_docker_volume_spec(config_dir, volume_vars, volume_specs, add_defaults=True):
    guest_volume_vars = {
        "WORKSPACE": "/code",
        "CFGDIR": "/cfg",
    }
    volumes = OrderedDict()
    for volume in volume_specs:
        # Expand string format to dictionary format
        if isinstance(volume, str):
            volume = volume.split(':')
            source = volume.pop(0)
            try:
                target = volume.pop(0)
            except IndexError:
                target = source
            try:
                read_only = {'rw': False, 'ro': True}[volume.pop(0)]
            except IndexError:
                read_only = None
            volume = {
                'source': source,
                'target': target,
            }
            if read_only is not None:
                volume['read-only'] = read_only

        # Expand source specification resolved on the host side
        if volume.get('source') is not None:
            source = expand_vars(volume_vars, os.path.expanduser(volume['source']))

            # Make relative paths relative to the configuration directory.
            # Absolute paths will be absolute
            source = config_dir / source
            volume['source'] = str(source)

        # Expand target specification resolved on the guest side
        if 'target' in volume:
            target = volume['target']

            if target.startswith('~/'):
                target = '/home/sandbox' + target[1:]

            target = expand_vars(guest_volume_vars, target)

            volume['target'] = target
        volumes[target] = volume

    if add_defaults:
        for var in guest_volume_vars:
            volumes.setdefault(
                guest_volume_vars[var],
                {
                    "source": volume_vars[var],
                    "target": guest_volume_vars[var],
                },
            )

        volumes.setdefault('/etc/passwd', {
            'source': '/etc/passwd',
            'target': '/etc/passwd',
            'read-only': True,
        })
        volumes.setdefault('/etc/group', {
            'source': '/etc/group',
            'target': '/etc/group',
            'read-only': True,
        })

    volumes = OrderedDict([
        (target, volume)
        for target, volume in volumes.items()
        if volume['source'] is not None
    ])

    return volumes


def read_version_info(config, version_info):
    if not isinstance(version_info, Mapping):
        raise ConfigurationError("`version` must be a mapping", file=config)

    tag = version_info.setdefault("tag", False)
    if not isinstance(tag, (str, bool)):
        raise ConfigurationError("`version.tag` must be a boolean or format string", file=config)

    if "bump" not in version_info:
        if tag or "file" in version_info:
            version_info["bump"] = {"policy": "constant"}
        else:
            version_info["bump"] = {"policy": "disabled"}

    bump = version_info["bump"]
    if not isinstance(bump, (str, Mapping, bool)) or isinstance(bump, bool) and bump:
        raise ConfigurationError("`version.bump` must be a mapping, string or the boolean false", file=config)
    elif isinstance(bump, str):
        bump = version_info['bump'] = OrderedDict((
                ('policy', 'constant'),
                ('field', bump),
            ))
    elif isinstance(bump, bool):
        assert bump is False
        bump = version_info['bump'] = OrderedDict((
                ('policy', 'disabled'),
            ))
    if not isinstance(bump.get('policy'), str):
        raise ConfigurationError("`version.bump.policy` must be a string identifying a version bumping policy to use", file=config)
    bump.setdefault('on-every-change', True)
    if not isinstance(bump['on-every-change'], bool):
        raise ConfigurationError("`version.bump.on-every-change` must be a boolean", file=config)
    if bump['policy'] == 'constant' and not isinstance(bump.get('field'), (str, type(None))):
        raise ConfigurationError(
                "`version.bump.field`, if it exists, must be a string identifying a version field to bump for the `constant` policy", file=config)
    if bump['policy'] == 'conventional-commits':
        bump.setdefault('strict', False)
        if not isinstance(version_info['bump']['strict'], bool):
            raise ConfigurationError("`version.bump.strict` field for the `conventional-commits` policy must be a boolean", file=config)

        bump.setdefault('reject-breaking-changes-on', re.compile(r'^(?:release/|rel-).*$'))
        bump.setdefault('reject-new-features-on', re.compile(r'^(?:release/|rel-)\d+\..*$'))

        if not isinstance(bump['reject-breaking-changes-on'], (str, Pattern)):
            raise ConfigurationError(
                    "`version.bump.reject-breaking-changes-on` field for the `conventional-commits` policy must be a regex or boolean", file=config)
        if not isinstance(bump['reject-new-features-on'], (str, Pattern)):
            raise ConfigurationError(
                    "`version.bump.reject-new-features-on` field for the `conventional-commits` policy must be a regex or boolean", file=config)

        if isinstance(bump["reject-breaking-changes-on"], str):
            bump["reject-breaking-changes-on"] = re.compile(bump["reject-breaking-changes-on"])
        if isinstance(bump["reject-new-features-on"], str):
            bump["reject-new-features-on"] = re.compile(bump["reject-new-features-on"])

    if 'build' in version_info:
        if 'format' in version_info and version_info['format'] != 'semver':
            raise ConfigurationError("`version.build` field must only be used when version.format is semver", file=config)
        build = version_info['build']
        if not isinstance(build, str):
            raise ConfigurationError("`version.build` field must be a string identifying the build metadata", file=config)
        if not re.match(r"^[-0-9a-zA-Z]+(?:\.[-0-9a-zA-Z]+)*$", build):
            raise ConfigurationError("`version.build` field must be a valid semantic versioning build metadata string", file=config)

    hotfix_branch = version_info.get(
        "hotfix-branch",
        r"^hotfix/\d+\.\d+\.\d+-(?P<id>[a-zA-Z](?:[-.a-zA-Z0-9]*[a-zA-Z0-9])?)$",
    )
    if isinstance(hotfix_branch, str):
        hotfix_branch = re.compile(hotfix_branch)
    if not isinstance(hotfix_branch, Pattern):
        raise ConfigurationError(
            "`version.hotfix-branch` field must be a string containing a valid regex",
            file=config,
        )
    if "id" not in hotfix_branch.groupindex and "ID" not in hotfix_branch.groupindex:
        unnamed_groups = [n for n in range(1, hotfix_branch.groups + 1) if n not in hotfix_branch.groupindex.values()]
        if len(unnamed_groups) != 1:
            raise ConfigurationError(
                "`version.hotfix-branch` field must contain a regex with a named capture group called 'id' or 'ID' or exactly one unnamed capture group",
                files=config,
            )
    version_info["hotfix-branch"] = hotfix_branch

    hotfix_allowed_start_tags = version_info.get("hotfix-allowed-start-tags", frozenset())
    try:
        typeguard.check_type(
            argname="version.hotfix-allowed-start-tags",
            value=hotfix_allowed_start_tags,
            expected_type=typing.Union[typing.AbstractSet[str], typing.Sequence[str]],
        )
    except TypeError as exc:
        raise ConfigurationError(
            "'version.hotfix-allowed-start-tags' member is not a list of commit tag strings",
            file=config,
        ) from exc
    hotfix_allowed_start_tags = frozenset(hotfix_allowed_start_tags)
    rejected_tags = hotfix_allowed_start_tags & {"fix", "feat"}
    if rejected_tags:
        raise ConfigurationError(
            f"'version.hotfix-allowed-start-tags' contains rejected tags {'.'.join(rejected_tags)}",
            file=config,
        )
    version_info["hotfix-allowed-start-tags"] = hotfix_allowed_start_tags

    return version_info


def parse_pip_config(config_obj, config):
    if not isinstance(config_obj, Mapping):
        return ()

    pip = config_obj.setdefault('pip', ()) if config_obj else ()
    if not isinstance(pip, Sequence):
        raise ConfigurationError(f"`pip` doesn't contain a sequence but a {type(pip).__name__}", file=config)
    for idx, spec in enumerate(pip):
        if isinstance(spec, str):
            pip[idx] = spec = OrderedDict((('packages', (spec,)),))
        if not isinstance(spec, Mapping):
            raise ConfigurationError(f"`pip[{idx}]` doesn't contain a mapping but a {type(spec).__name__}", file=config)
        if 'packages' not in spec:
            raise ConfigurationError(f"`pip[{idx}].packages` doesn't exist, so pip[{idx}] is useless", file=config)
        packages = spec['packages']
        if not (isinstance(packages, Sequence) and not isinstance(packages, str)):
            raise ConfigurationError(
                f"`pip[{idx}].packages` is not a sequence of package specification strings but a {type(packages).__name__}",
                file=config)
        if not packages:
            raise ConfigurationError(f"`pip[{idx}].packages` is empty, so pip[{idx}] is useless", file=config)
        from_idx = spec.get('from-index')
        if from_idx is not None and not isinstance(from_idx, str):
            raise ConfigurationError(
                f"`pip[{idx}].from-index` doesn't contain an URL string but a {type(from_idx).__name__}", file=config)
        with_extra_index = spec.setdefault('with-extra-index', ())
        if isinstance(with_extra_index, str):
            spec['with-extra-index'] = with_extra_index = (with_extra_index,)
        if not isinstance(with_extra_index, Sequence):
            raise ConfigurationError(
                f"`pip[{idx}].with-extra-index` doesn't contain a sequence of URL strings but a {type(with_extra_index).__name__}",
                file=config)
        for eidx, extra in enumerate(with_extra_index):
            if not isinstance(extra, str):
                raise ConfigurationError(
                    f"`pip[{idx}].with-extra-index[{eidx}]` doesn't contain an URL string but a {type(extra).__name__}",
                    file=config)

    return pip


def install_top_level_extensions(yaml_config, config_path, extension_installer, volume_vars):
    no_template_cfg = yaml.load(yaml_config, ordered_config_loader(volume_vars, extension_installer, False))
    pip_cfg = parse_pip_config(no_template_cfg, config_path)
    extension_installer(pip_cfg)
    return no_template_cfg


_basic_image_types = (str, IvyManifestImage, type(None))


def flatten_command_list(phase, variant, cmds, config_file=None):
    """Flattens a list of command lists into a single list of commands."""

    if not isinstance(cmds, Sequence):
        raise ConfigurationError(f"variant `{phase}.{variant}` doesn't contain a sequence but a {type(cmds).__name__}", file=config_file)

    for i, cmd in enumerate(cmds):
        if isinstance(cmd, str):
            yield OrderedDict((('sh', cmd),))
        elif isinstance(cmd, Sequence) and not isinstance(cmd, (str, bytes)):
            yield from flatten_command_list(phase, f"variant[{i}]", cmd, config_file=config_file)
        else:
            yield cmd


AllowedDockerOptions = TypedDict(
    "AllowedDockerOptions",
    {
        "device": typing.Union[typing.List[str], typing.Tuple[str], str],
        "add-host": typing.Union[typing.List[str], typing.Tuple[str], str],
        "hostname": str,
        "entrypoint": str,
        "dns": str,
        "init": bool,
    },
    total=False,
)


WorkTreeOptions = TypedDict(
    "WorkTreeOptions",
    {
        "commit-message": str,
        "changed-files": typing.Optional[typing.Union[typing.List[PathLike], typing.Tuple[PathLike, ...]]],
    },
    total=True,
)


class VariantCmd:
    cmd_rejected_fields: typing.ClassVar[typing.AbstractSet[str]] = frozenset(
        {
            "changed-files",
        }
    )
    cmd_supported_fields: typing.ClassVar[typing.Optional[typing.AbstractSet[str]]] = None

    def __init__(self, *, phase: str, variant: str, config_file: PathLike, volume_vars: typing.Mapping):
        self._phase = phase
        self._variant = variant
        self._config_file = config_file
        self._volume_vars = volume_vars

    def _finally(self, value, *, name: str, keys: typing.AbstractSet[str]):
        cmd_list = list(
            FinallyCmd(phase=self._phase, variant=self._variant + "-finally", config_file=self._config_file, volume_vars=self._volume_vars).process_cmd_list(
                flatten_command_list(self._phase, self._variant + "-finally", value, self._config_file)
            )
        )
        yield name, cmd_list

    def sh(self, value, *, name: str, keys: typing.AbstractSet[str]):
        if isinstance(value, str):
            value = shlex.split(value)

        if not isinstance(value, Sequence) or not all(isinstance(x, str) for x in value):
            raise ConfigurationError(
                f"'{name}' member is not a command string, nor a list of argument strings",
                file=self._config_file,
            )

        if "environment" not in keys:
            # Strip off prefixed environment variables from this command-line and apply them
            env = OrderedDict()
            while value:
                m = _env_var_re.match(value[0])
                if not m:
                    break
                env[m.group("var")] = m.group("val")
                value = value[1:]
            yield "environment", env

        yield name, value

    def description(self, value, *, name: str, keys: typing.AbstractSet[str]):
        if not isinstance(value, str):
            raise ConfigurationError(
                f"'{self._phase}.{self._variant}.{name}' is not a string",
                file=self._config_file,
            )

        yield name, value

    def run_on_change(self, value, *, name: str, keys: typing.AbstractSet[str]) -> typing.Iterable[typing.Tuple[str, RunOnChange]]:
        try:
            yield name, RunOnChange(value)
        except ValueError as exc:
            raise ConfigurationError(
                f"'{name}' member's value of {value!r} is not among the valid options ({', '.join(RunOnChange)})",
                file=self._config_file,
            ) from exc

    def archive(self, value, *, name: str, keys: typing.AbstractSet[str]):
        if not isinstance(value, (OrderedDict, dict)):
            raise ConfigurationError(
                f"'{self._phase}.{self._variant}.{name}' member is not a mapping",
                file=self._config_file,
            )
        try:
            artifacts = value["artifacts"]
        except KeyError:
            raise ConfigurationError(
                f"'{self._phase}.{self._variant}.{name}' lacks the mandatory 'artifacts' member",
                file=self._config_file,
            )

        # Convert single artifact string to list of single artifact specification
        if isinstance(artifacts, str):
            artifacts = [{"pattern": artifacts}]

        # Expand short hand notation of just the artifact pattern to a full dictionary
        artifacts = [({"pattern": artifact} if isinstance(artifact, str) else artifact) for artifact in artifacts]

        try:
            target = value["upload-artifactory"].pop("target")
        except (KeyError, TypeError):
            pass
        else:
            for artifact in artifacts:
                artifact.setdefault("target", target)

        for artifact_idx, artifact in enumerate(artifacts):
            try:
                pattern = artifact["pattern"]
            except KeyError:
                raise ConfigurationError(
                    f"'{self._phase}.{self._variant}.{name}[{artifact_idx}]' lacks the mandatory 'pattern' member",
                    file=self._config_file,
                )
            if not isinstance(pattern, str):
                raise ConfigurationError(
                    f"'{self._phase}.{self._variant}.{name}[{artifact_idx}].pattern' is not a string but a `{type(pattern).__name__}",
                    file=self._config_file,
                )
            try:
                for _ in Path(os.path.devnull).glob(pattern.replace("(*)", "*")):
                    break
            except ValueError as exc:
                raise ConfigurationError(
                    f"'{self._phase}.{self._variant}.{name}[{artifact_idx}].pattern' value of {pattern!r} is not a valid glob pattern: {exc}",
                    file=self._config_file,
                ) from exc

        value["artifacts"] = artifacts

        if "allow-empty-archive" in value:
            if "allow-missing" in value:
                raise ConfigurationError(
                    f"'allow-empty-archive' and 'allow-missing' are not allowed in the same {name} configuration, use only 'allow-missing'",
                    file=self._config_file,
                )

            allow_empty_archive = value.pop("allow-empty-archive")
            value["allow-missing"] = allow_empty_archive

        allow_missing = value.setdefault("allow-missing", False)
        if not isinstance(allow_missing, bool):
            raise ConfigurationError(
                f"'{self._phase}.{self._variant}.{name}.allow-missing' should be a boolean, not a {type(allow_missing).__name__}",
                file=self._config_file,
            )

        yield name, value

    def fingerprint(self, value, *, name: str, keys: typing.AbstractSet[str]):
        yield from self.archive(value, name=name, keys=keys)

    def junit(self, value, *, name: str, keys: typing.AbstractSet[str]):
        if isinstance(value, list):
            value = OrderedDict([("test-results", value)])
        elif isinstance(value, str):
            value = OrderedDict([("test-results", [value])])

        try:
            test_results = value["test-results"]
        except KeyError:
            raise ConfigurationError("JUnit configuration did not contain mandatory field 'test-results'", file=self._config_file)
        if isinstance(test_results, str):
            test_results = value["test-results"] = [test_results]
        try:
            typeguard.check_type(argname=f"{self._phase}.{self._variant}.{name}.test-results", value=test_results, expected_type=typing.Sequence[str])
        except TypeError as exc:
            raise ConfigurationError(
                "'{self._phase}.{self._variant}.{name}.test-results' member is not a list of file pattern strings",
                file=self._config_file,
            ) from exc
        allow_missing = value.setdefault("allow-missing", False)
        if not isinstance(allow_missing, bool):
            raise ConfigurationError(
                f"'{self._phase}.{self._variant}.{name}.allow-missing' should be a boolean, not a {type(allow_missing).__name__}",
                file=self._config_file,
            )
        allow_failures = value.setdefault("allow-failures", False)
        if not isinstance(allow_failures, bool):
            raise ConfigurationError(
                f"'{self._phase}.{self._variant}.{name}.allow-failures' should be a boolean, not a {type(allow_failures).__name__}",
                file=self._config_file,
            )
        for pattern_idx, pattern in enumerate(test_results):
            try:
                for _ in Path(os.path.devnull).glob(pattern):
                    break
            except ValueError as exc:
                raise ConfigurationError(
                    f"'{self._phase}.{self._variant}.{name}[{pattern_idx}]' value of {pattern!r} is not a valid glob pattern: {exc}",
                    file=self._config_file,
                ) from exc

        yield name, value

    def with_credentials(self, value, *, name: str, keys: typing.AbstractSet[str]):
        if isinstance(value, str):
            value = OrderedDict([("id", value)])
        if not isinstance(value, Sequence):
            value = [value]
        for cred_idx, cred in enumerate(value):
            try:
                cred_type = cred["type"] = CredentialType(cred.get("type", CredentialType.default))
            except ValueError as exc:
                raise ConfigurationError(
                    f"'{name}[{cred_idx}].type' value of {cred['type']!r} is not among the valid options ({', '.join(CredentialType)})",
                    file=self._config_file,
                ) from exc
            if cred_type == CredentialType.username_password:
                try:
                    cred['encoding'] = CredentialEncoding(cred.get('encoding', CredentialEncoding.default))
                except ValueError as exc:
                    raise ConfigurationError(
                            f"'{name}[{cred_idx}].encoding' value of {cred['encoding']!r} is not among the valid options "
                            f"({', '.join(CredentialEncoding)})",
                            file=self._config_file) from exc
                if not isinstance(cred.setdefault('username-variable', 'USERNAME'), str):
                    raise ConfigurationError(
                            f"'username-variable' in {name} block `{cred['id']}` for "
                            f"`{self._phase}.{self._variant}` is not a string", file=self._config_file)
                if not isinstance(cred.setdefault('password-variable', 'PASSWORD'), str):
                    raise ConfigurationError(
                            f"'password-variable' in {name} block `{cred['id']}` for "
                            f"`{self._phase}.{self._variant}` is not a string", file=self._config_file)
            elif cred_type == CredentialType.file:
                if not isinstance(cred.setdefault('filename-variable', 'SECRET_FILE'), str):
                    raise ConfigurationError(
                            f"'filename-variable' in {name} block `{cred['id']}` for "
                            f"`{self._phase}.{self._variant}` is not a string", file=self._config_file)
            elif cred_type == CredentialType.string:
                if not isinstance(cred.setdefault('string-variable'  , 'SECRET'), str):  # noqa: E203
                    raise ConfigurationError(
                            f"'string-variable' in {name} block `{cred['id']}` for "
                            f"`{self._phase}.{self._variant}` is not a string", file=self._config_file)
            elif cred['type'] == CredentialType.ssh_key:
                if not isinstance(cred.setdefault('ssh-command-variable', 'SSH'), str):
                    raise ConfigurationError(
                            f"'ssh-command-variable' in {name} block `{cred['id']}` for "
                            f"`{self._phase}.{self._variant}` is not a string", file=self._config_file)

        yield name, value

    def timeout(self, value, *, name: str, keys: typing.AbstractSet[str]):
        if not isinstance(value, (Decimal, Real)) or isinstance(value, bool) or value <= 0:
            raise ConfigurationError(
                f"`{name}` member of `{self._phase}.{self._variant}` must be a positive real number",
                file=self._config_file,
            )

        yield name, value

    def image(self, value, *, name: str, keys: typing.AbstractSet[str]):
        if not isinstance(value, _basic_image_types):
            raise ConfigurationError(
                f"`{name}` member of `{self._variant}` must be a string or `!image-from-ivy-manifest`",
                file=self._config_file)

        yield name, value

    def volumes_from(self, value, *, name: str, keys: typing.AbstractSet[str]):
        yield name, expand_docker_volumes_from(self._volume_vars, value)

    def extra_docker_args(self, args, *, name: str, keys: typing.AbstractSet[str]):
        if not isinstance(args, Mapping) or not all(isinstance(key, str) for key in args):
            raise ConfigurationError(
                f"`{name}` member of `{self._variant}` should be a Mapping with string keys, not a {type(args).__name__}",
                file=self._config_file)

        disallowed_options = args.keys() - AllowedDockerOptions.__annotations__.keys()
        if disallowed_options != set():
            raise ConfigurationError(dedent('''
                `extra-docker-args` member of `{}` contains one or more options that are not allowed:
                  {}
                Allowed options:
                  {}
                '''.format(
                     self._variant,
                     ', '.join(disallowed_options),
                     ', '.join(AllowedDockerOptions.__annotations__)
                )),
                file=self._config_file)
        try:
            typeguard.check_type(argname="extra-docker-args", value=args, expected_type=AllowedDockerOptions)
        except TypeError as exc:
            raise ConfigurationError(
                f"`{self._phase}.{self._variant}.{name}` is not a valid mapping of extra docker arguments: {exc}",
                file=self._config_file,
            ) from exc
        for k, v in args.items():
            if isinstance(v, str) and " " in v:
                raise ConfigurationError(
                    f"`{self._phase}.{self._variant}.{name}` argument `{k}` for `{self._variant}` contains whitespace, which is not permitted.",
                    file=self._config_file,
                )

        yield name, args

    def environment(self, env, *, name: str, keys: typing.AbstractSet[str]):
        if "sh" not in keys:
            raise ConfigurationError(
                f"Trying to set '{name}' member for a command entry that doesn't have 'sh'",
                file=self._config_file,
            )

        if not isinstance(env, Mapping):
            raise ConfigurationError(
                f"'{name}' member is not a mapping of strings to strings",
                file=self._config_file,
            )
        for i, (k, v) in enumerate(env.items()):
            if not isinstance(k, str):
                raise ConfigurationError(
                    f"'{name}' member has key `{k!r}` at index {i} that is not a string but a `{type(k).__name__}`",
                    file=self._config_file,
                )
            if v is not None and not isinstance(v, str):
                raise ConfigurationError(
                    f"`{name}[{k!r}]` is not a string or null but a `{type(v).__name__}`",
                    file=self._config_file,
                )

        yield name, env

    def worktrees(self, trees, *, name: str, keys: typing.AbstractSet[str]) -> typing.Iterable[typing.Tuple[str, typing.Mapping[str, WorkTreeOptions]]]:
        if not isinstance(trees, Mapping):
            raise ConfigurationError(
                f"`{name}` member of `{self._phase}.{self._variant}` should be a Mapping with string keys and values, not a {type(trees).__name__}",
                file=self._config_file,
            )

        new_trees: typing.Dict[str, WorkTreeOptions] = OrderedDict()
        for tree_idx, (subdir, worktree) in enumerate(trees.items()):
            if not isinstance(subdir, str) or os.path.isabs(subdir):
                raise ConfigurationError(
                    f"{tree_idx}th member of `{self._phase}.{self._variant}.{name}` should be a string representing a relative path",
                    file=self._config_file,
                )

            if not isinstance(worktree, Mapping):
                raise ConfigurationError(
                    f"`{self._phase}.{self._variant}.{name}.{subdir}` should be a Mapping",
                    file=self._config_file,
                )

            worktree = typing.cast(WorkTreeOptions, OrderedDict(worktree))
            changed_files = worktree.setdefault("changed-files", None)
            if isinstance(changed_files, str):
                worktree["changed-files"] = (changed_files,)

            try:
                typeguard.check_type(argname="`{self._phase}.{self._variant}.{name}.{subdir}", value=worktree, expected_type=WorkTreeOptions)
            except TypeError as exc:
                raise ConfigurationError(
                    f"`{self._phase}.{self._variant}.{name}.{subdir}` is not a valid mapping of worktree options: {exc}",
                    file=self._config_file,
                ) from exc

            new_trees[subdir] = worktree

        yield name, new_trees

    def process_unknown_cmd_item(self, name, value, keys: typing.AbstractSet[str]):
        yield name, value

    def process_cmd_item(self, name, value, keys: typing.AbstractSet[str]):
        if "_" not in name:
            try:
                func_name = name.replace("-", "_")
                if func_name in keyword.kwlist:
                    func_name = "_" + func_name
                key_proc = getattr(self, func_name)
            except AttributeError:
                pass
            else:
                yield from key_proc(value, name=name, keys=keys)
                return

        yield from self.process_unknown_cmd_item(name=name, value=value, keys=keys)

    def process_cmd(self, cmd: typing.Mapping):
        assert not isinstance(cmd, str), "internal error: string commands should have been converted to 'sh' dictionary format"
        assert isinstance(cmd, Mapping)

        return dict(
            itertools.chain.from_iterable(
                self.process_cmd_item(name, value, keys=cmd.keys())
                for name, value in cmd.items()
            )
        )

    def process_cmd_list(self, cmds: typing.Iterable[typing.Mapping]) -> typing.Iterable:
        seen_sh = False
        seen_finally_global = False
        global_timeout = None
        summed_timeout = 0

        for cmd_idx, cmd in enumerate(cmds):
            rejected_fields = cmd.keys() & self.cmd_rejected_fields
            if rejected_fields:
                raise ConfigurationError(
                    f"`{self._phase}.{self._variant}[{cmd_idx}]` contains forbidden fields {', '.join(rejected_fields)}",
                    file=self._config_file,
                )
            unsupported_fields = frozenset() if self.cmd_supported_fields is None else (cmd.keys() - self.cmd_supported_fields)
            if unsupported_fields:
                raise ConfigurationError(
                    f"`{self._phase}.{self._variant}[{cmd_idx}]` contains unsupported fields {', '.join(unsupported_fields)}",
                    file=self._config_file,
                )

            cmd = self.process_cmd(cmd)

            if "sh" in cmd:
                seen_sh = True
                if seen_finally_global:
                    raise ConfigurationError(
                        f"`{self._phase}.{self._variant}[{cmd_idx}]` attempting to define sh list after the global finally",
                        file=self._config_file,
                    )

            if "finally" in cmd and "sh" not in cmd:
                seen_finally_global = True

            if "timeout" in cmd:
                if not seen_sh:
                    if global_timeout is not None:
                        raise ConfigurationError(
                            f"`{self._phase}.{self._variant}[{cmd_idx}]` attempting to define global `timeout` multiple times",
                            file=self._config_file,
                        )
                    global_timeout = cmd["timeout"]
                elif "sh" not in cmd:
                    raise ConfigurationError(
                        f"`{self._phase}.{self._variant}[{cmd_idx}]` attempting to define global `timeout` after an 'sh' command has already been given",
                        file=self._config_file,
                    )
                else:
                    summed_timeout += cmd["timeout"]
                if global_timeout is not None and global_timeout <= summed_timeout:
                    raise ConfigurationError(
                        f"`{self._phase}.{self._variant}[0].timeout` ({global_timeout} seconds) is not greater than summed per-command timeouts"
                        f" ({summed_timeout} seconds)",
                        file=self._config_file,
                    )

            yield cmd


class FinallyCmd(VariantCmd):
    cmd_rejected_fields = frozenset(
        {
            "archive",
            "changed-files",
            "finally",
            "fingerprint",
            "foreach",
            "junit",
            "node-label",
            "run-on-change",
            "stash",
            "wait-on-full-previous-phase",
            "worktrees",
        }
    )

    def __init__(self, *, phase: str, variant: str, config_file: PathLike, volume_vars: typing.Mapping):
        super().__init__(phase=phase, variant=variant, config_file=config_file, volume_vars=volume_vars)

    def process_cmd_list(self, cmds: typing.Iterable[typing.Mapping]) -> typing.Iterable:
        seen_sh = False
        for cmd_idx, cmd in enumerate(super().process_cmd_list(cmds)):
            if "sh" in cmd:
                seen_sh = True
            if "timeout" in cmd and not seen_sh:
                raise ConfigurationError(
                    f"`{self._phase}.{self._variant}[{cmd_idx}]` global `timeout` for `finally` is not supported",
                    file=self._config_file,
                )
            yield cmd


class ModalitySourcePreparationCmd(VariantCmd):
    cmd_rejected_fields = frozenset(
        {
            "archive",
            "fingerprint",
            "foreach",
            "junit",
            "node-label",
            "run-on-change",
            "stash",
            "worktrees",
        }
    )

    def __init__(self, *, modality: str, config_file: PathLike, volume_vars: typing.Mapping):
        super().__init__(phase="modality-source-preparation", variant=modality, config_file=config_file, volume_vars=volume_vars)

    def process_cmd_list(self, cmds: typing.Iterable) -> typing.Iterable:
        seen_commit_message = False

        for cmd_idx, cmd in enumerate(super().process_cmd_list(cmds)):
            present_msg_keys = cmd.keys() & {"commit-message", "commit-message-cmd"}
            if present_msg_keys:
                if seen_commit_message or len(present_msg_keys) >= 2:
                    raise ConfigurationError(
                        f"`{self._phase}.{self._variant}[{cmd_idx}]` attempting to define `commit-message` or `commit-message-cmd` multiple times",
                        file=self._config_file,
                    )
                seen_commit_message = True

                if "commit-message-cmd" in cmd:
                    msg_cmd = cmd["commit-message-cmd"]
                    if isinstance(msg_cmd, str):
                        msg_cmd = {"sh": msg_cmd}
                    if not isinstance(msg_cmd, Mapping) or "sh" not in msg_cmd:
                        raise ConfigurationError(
                            f"'{self._phase}.{self._variant}[{cmd_idx}].commit-message-cmd' member is neither a command string, nor a mapping with 'sh' key",
                            file=self._config_file,
                        )
                    (cmd["commit-message-cmd"],) = super().process_cmd_list([msg_cmd])

            yield cmd

        if not seen_commit_message:
            yield {"commit-message": self._variant}

    def changed_files(
        self, value, *, name: str, keys: typing.AbstractSet[str]
    ) -> typing.Iterable[typing.Tuple[str, typing.Union[typing.Sequence[PathLike], Literal[":"], EmptyDict]]]:
        if isinstance(value, (str, PurePath)):
            value = (value,)

        try:
            typeguard.check_type(argname=f"{self._phase}.{self._variant}.{name}", value=value, expected_type=typing.Sequence[PathLike])
        except TypeError as exc:
            raise ConfigurationError(
                "'{self._phase}.{self._variant}.{name}' member is not a list of path strings",
                file=self._config_file,
            ) from exc

        yield name, value

        if "sh" not in keys:
            # Add a NOP command to ensure 'changed-files' gets picked up during the build loop
            yield from self.sh((":",), name="sh", keys=keys)


class PostSubmitCmd(VariantCmd):
    cmd_rejected_fields = VariantCmd.cmd_rejected_fields | frozenset(
        {
            "archive",
            "fingerprint",
            "stash",
            "worktrees",
        }
    )
    cmd_supported_fields = frozenset({
        "environment",
        "description",
        "docker-in-docker",
        "image",
        "node-label",
        "run-on-change",
        "sh",
        "timeout",
        "volumes",
        "with-credentials",
    })

    def __init__(self, *, phase: str, config_file: PathLike, volume_vars: typing.Mapping):
        super().__init__(phase="post-submit", variant=phase, config_file=config_file, volume_vars=volume_vars)


def read(config, volume_vars, extension_installer=lambda *args: None):
    if isinstance(config, io.TextIOBase):
        f = config
        config = f.name
        file_close = False
    else:
        f = open(config, 'r')
        file_close = True

    try:
        config = Path(config)
        config_dir = config.parent

        volume_vars = volume_vars.copy()
        volume_vars['CFGDIR'] = str(config_dir)

        cfg = install_top_level_extensions(f, config, extension_installer, volume_vars)
        f.seek(0)
        try:
            cfg = yaml.load(f, ordered_config_loader(volume_vars, extension_installer))
        except TemplateNotFoundError as e:
            cfg['phases'] = OrderedDict([
                ("yaml-error", {
                    f"{e.name}": [{
                        'description': str(e),
                        'sh': ('false',)
                    }]
                })]
            )
        else:
            if cfg is None:
                cfg = OrderedDict()

            cfg = load_config_section(cfg)
    finally:
        if file_close:
            f.close()

    if not isinstance(cfg, Mapping):
        raise ConfigurationError(f"top level configuration should be a map, but is a {type(cfg).__name__}", file=config)

    cfg['volumes'] = expand_docker_volume_spec(config_dir, volume_vars, cfg.get('volumes', ()))
    cfg['version'] = read_version_info(config, cfg.get('version', OrderedDict()))

    env_vars = cfg.setdefault('pass-through-environment-vars', ())
    cfg.setdefault('clean', [])
    if not (isinstance(env_vars, Sequence) and not isinstance(env_vars, str)):
        raise ConfigurationError('`pass-through-environment-vars` must be a sequence of strings', file=config)
    for idx, var in enumerate(env_vars):
        if not isinstance(var, str):
            raise ConfigurationError(
                    f"`pass-through-environment-vars` must be a sequence containing strings only: element {idx} has type {type(var).__name__}", file=config)

    ci_locks = cfg.setdefault('ci-locks', [])
    if not isinstance(ci_locks, Sequence):
        raise ConfigurationError(f"`ci-locks` doesn't contain a sequence but a {type(ci_locks).__name__}", file=config)
    ci_locks_argument_mapping = {
        'branch'           : {'type': str },
        'repo-name'        : {'type': str },
        'lock-on-change'   : {'type': LockOnChange, 'default': LockOnChange.default},
        'from-phase-onward': {'type': str, 'optional': True }
    }
    for lock_properties in ci_locks:
        for property, argument_spec in ci_locks_argument_mapping.items():
            if property not in lock_properties:
                if 'default' in argument_spec:
                    lock_properties[property] = argument_spec['type'](argument_spec['default'])
                elif not argument_spec.get('optional', False):
                    raise ConfigurationError(f"`ci-locks` {lock_properties} doesn't contain a {property}", file=config)
                else:
                    continue

            msg = f'`ci-locks` {lock_properties} has an invalid attribute "{property}", expected %s, but got a {type(lock_properties[property]).__name__}'
            if issubclass(ci_locks_argument_mapping[property]['type'], Enum):
                try:
                    isinstance(argument_spec['type'](lock_properties[property]), argument_spec['type'])
                except ValueError:
                    raise ConfigurationError(msg % ("one of " + ", ".join(f'"{x}"' for x in LockOnChange)))
            elif not isinstance(lock_properties[property], argument_spec['type']):
                raise ConfigurationError(msg % f"a {argument_spec['type'].__name__}")

    valid_image_types = (_basic_image_types, Mapping)
    image = cfg.setdefault('image', OrderedDict())
    if not isinstance(image, valid_image_types):
        raise ConfigurationError("`image` must be a string, mapping, or `!image-from-ivy-manifest`", file=config)
    if not isinstance(image, Mapping):
        image = cfg['image'] = OrderedDict((('default', cfg['image']),))
    for variant, name in image.items():
        if not isinstance(name, _basic_image_types):
            raise ConfigurationError(f"`image` member `{variant}` must be a string or `!image-from-ivy-manifest`", file=config)

    if 'project-name' in cfg and not isinstance(cfg['project-name'], str):
        raise ConfigurationError('`project-name` setting must be a string', file=config)

    scm = cfg.setdefault("scm", OrderedDict())
    if not isinstance(scm, Mapping):
        raise ConfigurationError(f"`scm` doesn't contain a mapping but a {type(scm).__name__}", file=config)

    git_cfg = scm.setdefault("git", OrderedDict())
    if not isinstance(git_cfg, Mapping):
        raise ConfigurationError(f"`scm.git` doesn't contain a mapping but a {type(git_cfg).__name__}", file=config)

    worktrees = git_cfg.setdefault("worktrees", OrderedDict())
    try:
        typeguard.check_type(argname="scm.git.worktrees", value=worktrees, expected_type=typing.Mapping[PathLike, str], globals=globals, locals=locals)
    except TypeError as exc:
        raise ConfigurationError(f"`scm.git.worktrees` is not a valid mapping of worktree paths to branches: {exc}", file=config) from exc

    # Convert multiple different syntaxes into a single one
    variant_node_label = OrderedDict()
    variant_node_label_phase = OrderedDict()
    variant_node_label_idx = OrderedDict()

    dependent_meta = OrderedDict()
    previous_phase = None
    for phasename, phase in cfg.setdefault('phases', OrderedDict()).items():
        if not isinstance(phase, Mapping):
            raise ConfigurationError(f"phase `{phasename}` doesn't contain a mapping but a {type(phase).__name__}", file=config)
        dependent_meta[phasename] = set()
        for variant in phase:
            if variant == 'post-submit':
                raise ConfigurationError(f"variant name 'post-submit', used in phase `{phasename}`, is reserved for internal use", file=config)
            phase[variant] = list(
                VariantCmd(phase=phasename, variant=variant, config_file=config, volume_vars=volume_vars).process_cmd_list(
                    flatten_command_list(phasename, variant, phase[variant], config_file=config)
                )
            )
            wait_on_full_previous_phase = None
            run_on_change = None
            for cmd_idx, cmd in enumerate(phase[variant]):
                for metakey, metaval in cmd.items():
                    if metakey in _interphase_dependent_meta:
                        metatype = type(metaval)
                        if not hasattr(metatype, 'default') or metaval != metatype.default:
                            dependent_meta[phasename].add(metakey)
                if 'node-label' in cmd:
                    node_label = cmd['node-label']
                    if not isinstance(node_label, str):
                        raise ConfigurationError(
                            f"`{phasename}`.`{variant}`[{cmd_idx}].`node-label` doesn't contain a string but a {type(node_label).__name__}",
                            file=config,
                        )
                    if variant not in variant_node_label:
                        variant_node_label[variant] = node_label
                        variant_node_label_phase[variant] = phasename
                        variant_node_label_idx[variant] = cmd_idx
                    if variant_node_label[variant] is None:
                        raise ConfigurationError(
                            f"`{phasename}`.`{variant}`[{cmd_idx}].`node-label` ({node_label!r}) attempts to override default set for "
                            f"`{variant_node_label_phase[variant]}`.`{variant}`",
                            file=config,
                        )
                    if node_label != variant_node_label[variant]:
                        raise ConfigurationError(
                            f"`{phasename}`.`{variant}`[{cmd_idx}].`node-label` ({node_label!r}) differs from that previously defined in "
                            f"`{variant_node_label_phase[variant]}`.`{variant}`[{variant_node_label_idx[variant]}] ({variant_node_label[variant]!r})",
                            file=config,
                        )
                if 'run-on-change' in cmd:
                    if run_on_change is not None and cmd['run-on-change'] != run_on_change:
                        raise ConfigurationError(
                            f"`{phasename}`.`{variant}`[{cmd_idx}].`run-on-change` ({cmd['run-on-change']!r}) differs from that previously defined",
                            file=config,
                        )
                    run_on_change = cmd['run-on-change']
                if 'wait-on-full-previous-phase' in cmd:
                    if wait_on_full_previous_phase is not None:
                        raise ConfigurationError(
                            f"`wait-on-full-previous-phase` defined multiple times for `{phasename}`.`{variant}`",
                            file=config,
                        )
                    wait_on_full_previous_phase = cmd['wait-on-full-previous-phase']
                    if not isinstance(wait_on_full_previous_phase, bool):
                        raise ConfigurationError(
                            f"`{phasename}`.`{variant}`[{cmd_idx}].`wait-on-full-previous-phase` doesn't contain a boolean but a "
                            f"{type(wait_on_full_previous_phase).__name__}",
                            file=config,
                        )
                    elif not wait_on_full_previous_phase and previous_phase is None:
                        raise ConfigurationError(
                            f"`{phasename}`.`{variant}`[{cmd_idx}].`wait-on-full-previous-phase` defined but there is no previous phase",
                            file=config,
                        )
                    elif not wait_on_full_previous_phase and variant not in cfg['phases'][previous_phase]:
                        raise ConfigurationError(
                            f"`{phasename}`.`{variant}`[{cmd_idx}].`wait-on-full-previous-phase` disabled but previous phase `{previous_phase}` "
                            f"doesn't contain variant `{variant}`",
                            file=config,
                        )
                    elif not wait_on_full_previous_phase and dependent_meta[previous_phase]:
                        raise ConfigurationError(
                            f"`{phasename}`.`{variant}`[{cmd_idx}].`wait-on-full-previous-phase` disabled but previous phase `{previous_phase}` "
                            f"uses dependency-creating options {dependent_meta[previous_phase]}",
                            file=config,
                        )
            # If the node label has not been set in the first phase that a variant occurs in it's the default
            if variant not in variant_node_label:
                variant_node_label[variant] = None
                variant_node_label_phase[variant] = phasename

            if wait_on_full_previous_phase is False:
                run_on_change_previous_phase = next((cmd["run-on-change"] for cmd in cfg["phases"][previous_phase][variant] if "run-on-change" in cmd), None)

                if run_on_change is not run_on_change_previous_phase:
                    raise ConfigurationError(
                        f"`{phasename}`.`{variant}`.`wait-on-full-previous-phase` disabled but "
                        f"`{phasename}`.`{variant}`.`run-on-change` set to a value other than {run_on_change_previous_phase}",
                        file=config,
                    )
            if (
                wait_on_full_previous_phase is None
                and previous_phase is not None
                and variant in cfg['phases'][previous_phase]
            ):
                if not phase[variant]:
                    phase[variant].insert(0, OrderedDict())
                phase[variant][0]['wait-on-full-previous-phase'] = True
        previous_phase = phasename

    modalities = cfg.setdefault("modality-source-preparation", OrderedDict())
    for modality in modalities:
        modalities[modality] = tuple(
            ModalitySourcePreparationCmd(modality=modality, config_file=config, volume_vars=volume_vars).process_cmd_list(
                flatten_command_list("modality-source-preparation", modality, modalities[modality], config_file=config)
            )
        )

    lock_names = []
    for ci_lock in ci_locks:
        if 'from-phase-onward' in ci_lock:
            if ci_lock['from-phase-onward'] not in cfg['phases']:
                raise ConfigurationError(
                    f"referenced phase in ci-locks ({ci_lock['from-phase-onward']}) doesn't exist",
                    file=config,
                )
            for variant_name, variant in cfg['phases'][ci_lock['from-phase-onward']].items():
                if any('wait-on-full-previous-phase' in item and not item['wait-on-full-previous-phase'] for item in variant):
                    raise ConfigurationError(
                        f"referenced phase in ci-locks ({ci_lock['from-phase-onward']}) "
                        f"refers to variant ({variant_name}) that has wait-on-full-previous-phase disabled",
                        file=config,
                    )

        lock_id = ci_lock['repo-name'] + ci_lock['branch']
        if lock_id in lock_names:
            raise ConfigurationError(
                f"ci-lock with repo-name '{ci_lock['repo-name']}' and branch '{ci_lock['branch']}' already exists, "
                "this would lead to a deadlock",
                file=config,
            )
        lock_names.append(ci_lock['repo-name'] + ci_lock['branch'])

    post_submit = cfg.setdefault('post-submit', OrderedDict())
    if not isinstance(post_submit, Mapping):
        raise ConfigurationError(f"`post-submit` doesn't contain a mapping but a {type(post_submit).__name__}", file=config)
    post_submit_node_label = None
    post_submit_node_label_phase = None
    post_submit_node_label_idx = None
    for phase in post_submit:
        post_submit[phase] = list(
            PostSubmitCmd(phase=phase, config_file=config, volume_vars=volume_vars).process_cmd_list(
                flatten_command_list("post-submit", phase, post_submit[phase], config_file=config)
            )
        )
        for cmd_idx, cmd in enumerate(post_submit[phase]):
            if 'node-label' in cmd:
                if post_submit_node_label is None:
                    post_submit_node_label = cmd['node-label']
                    post_submit_node_label_phase = phase
                    post_submit_node_label_idx = cmd_idx
                if cmd['node-label'] != post_submit_node_label:
                    raise ConfigurationError(
                        f"`post-submit`.`{phase}`[{cmd_idx}].`node-label` ({cmd['node-label']!r}) differs from that previously defined in "
                        f"`post-submit`.`{post_submit_node_label_phase}`[{post_submit_node_label_idx}] ({post_submit_node_label!r})",
                        file=config,
                    )

    return cfg
