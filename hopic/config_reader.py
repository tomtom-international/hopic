# Copyright (c) 2018 - 2020 TomTom N.V. (https://tomtom.com)
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
        Mapping,
        Sequence,
    )
from click import ClickException
import errno
try:
    # Python >= 3.8
    from importlib import metadata
except ImportError:
    import importlib_metadata as metadata
import json
import logging
import os
import re
import shlex
import subprocess
import xml.etree.ElementTree as ET
import yaml

__all__ = (
    'expand_vars',
    'read',
    'expand_docker_volume_spec',
)

log = logging.getLogger(__name__)

Pattern = type(re.compile(''))


_variable_interpolation_re = re.compile(r'(?<!\$)\$(?:(\w+)|\{([^}]+)\})')
def expand_vars(vars, expr):
    if isinstance(expr, str):
        # Expand variables from our "virtual" environment
        last_idx = 0
        new_val = expr[:last_idx]
        for var in _variable_interpolation_re.finditer(expr):
            name = var.group(1) or var.group(2)
            value = vars[name]
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


class ConfigurationError(ClickException):
    exit_code = 32

    def __init__(self, message, file=None):
        super().__init__(message)
        self.file = file

    def format_message(self):
        if self.file is not None:
            return "configuration error in '%s': %s" % (self.file, self.message)
        else:
            return "configuration error: %s" % (self.message,)


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
        if isinstance(o, IvyManifestImage):
            return str(o)
        elif isinstance(o, Pattern):
            return o.pattern
        return super().default(o)


def load_yaml_template(volume_vars, loader, node):
    if node.id == 'scalar':
        props = {}
        name = loader.construct_scalar(node)
    else:
        props = loader.construct_mapping(node)
        name = props.pop('name')

    for ep in metadata.entry_points().get('hopic.plugins.yaml', ()):
        if ep.name == name:
            break
    else:
        # TODO: lazy load here instead to deal with plugins that may be installed during Hopic's flow
        raise ConfigurationError(f"No YAML template named '{name}' available (props={props})")

    return ep.load()(volume_vars, **props)


def ordered_config_loader(volume_vars):
    def pass_volume_vars(f):
        return lambda *args: f(volume_vars, *args)

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
        pass_volume_vars(load_yaml_template)
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
        'WORKSPACE': '/code',
    }
    volumes = OrderedDict({});
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
            source = os.path.join(config_dir, source)
            volume['source'] = source

        # Expand target specification resolved on the guest side
        if 'target' in volume:
            target = volume['target']

            if target.startswith('~/'):
                target = '/home/sandbox' + target[1:]

            target = expand_vars(guest_volume_vars, target)

            volume['target'] = target
        volumes[target] = volume

    if add_defaults:
        volumes.setdefault(guest_volume_vars['WORKSPACE'], {
            'source': volume_vars['WORKSPACE'],
            'target': guest_volume_vars['WORKSPACE'],
        })
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

    volumes = OrderedDict([(target, volume)
        for target, volume in volumes.items()
        if volume['source'] is not None])

    return volumes


def read_version_info(config, version_info):
    if not isinstance(version_info, Mapping):
        raise ConfigurationError("`version` must be a mapping", file=config)

    bump = version_info.setdefault('bump', OrderedDict((('policy', 'constant'),)))
    if not isinstance(bump, (str, Mapping, bool)) or isinstance(bump, bool) and bump:
        raise ConfigurationError("`version.bump` must be a mapping, string or the boolean false", file=config)
    elif isinstance(bump, str):
        bump = version_info['bump'] = OrderedDict((
                ('policy', 'constant'),
                ('field', bump),
            ))
    elif isinstance(bump, bool):
        assert bump == False
        bump = version_info['bump'] = OrderedDict((
                ('policy', 'disabled'),
            ))
    if not isinstance(bump.get('policy'), str):
        raise ConfigurationError("`version.bump.policy` must be a string identifying a version bumping policy to use", file=config)
    bump.setdefault('on-every-change', True)
    if not isinstance(bump['on-every-change'], bool):
        raise ConfigurationError("`version.bump.on-every-change` must be a boolean", file=config)
    if bump['policy'] == 'constant' and not isinstance(bump.get('field'), (str, type(None))):
        raise ConfigurationError("`version.bump.field`, if it exists, must be a string identifying a version field to bump for the `constant` policy", file=config)
    if bump['policy'] == 'conventional-commits':
        bump.setdefault('strict', False)
        if not isinstance(version_info['bump']['strict'], bool):
            raise ConfigurationError("`version.bump.strict` field for the `conventional-commits` policy must be a boolean", file=config)
        bump.setdefault('reject-breaking-changes-on', re.compile(r'^(?:release/|rel-).*$'))
        bump.setdefault('reject-new-features-on', re.compile(r'^(?:release/|rel-)\d+\..*$'))
        if not isinstance(bump['reject-breaking-changes-on'], (str, Pattern)):
            raise ConfigurationError("`version.bump.reject-breaking-changes-on` field for the `conventional-commits` policy must be a regex or boolean", file=config)
        if not isinstance(bump['reject-new-features-on'], (str, Pattern)):
            raise ConfigurationError("`version.bump.reject-new-features-on` field for the `conventional-commits` policy must be a regex or boolean", file=config)

    return version_info


def read(config, volume_vars):
    config_dir = os.path.dirname(config)

    volume_vars = volume_vars.copy()
    volume_vars['CFGDIR'] = config_dir
    OrderedConfigLoader = ordered_config_loader(volume_vars)

    with open(config, 'r') as f:
        cfg = yaml.load(f, OrderedConfigLoader)

    if cfg is None:
        cfg = OrderedDict()

    cfg['volumes'] = expand_docker_volume_spec(config_dir, volume_vars, cfg.get('volumes', ()))
    cfg['version'] = read_version_info(config, cfg.get('version', OrderedDict()))

    env_vars = cfg.setdefault('pass-through-environment-vars', ())
    cfg.setdefault('clean', [])
    if not (isinstance(env_vars, Sequence) and not isinstance(env_vars, str)):
        raise ConfigurationError('`pass-through-environment-vars` must be a sequence of strings', file=config)
    for idx, var in enumerate(env_vars):
        if not isinstance(var, str):
            raise ConfigurationError("`pass-through-environment-vars` must be a sequence containing strings only: element {idx} has type {type!r}".format(idx=idx, type=type(idx)), file=config)

    basic_image_types = (str, IvyManifestImage, type(None))
    valid_image_types = (basic_image_types, Mapping)
    image = cfg.setdefault('image', OrderedDict())
    if not isinstance(image, valid_image_types):
        raise ConfigurationError("`image` must be a string, mapping, or `!image-from-ivy-manifest`", file=config)
    if not isinstance(image, Mapping):
        image = cfg['image'] = OrderedDict((('default', cfg['image']),))
    for variant, name in image.items():
        if not isinstance(name, basic_image_types):
            raise ConfigurationError(f"`image` member `{variant}` must be a string or `!image-from-ivy-manifest`", file=config)

    # Flatten command lists
    for phasename, phase in cfg.setdefault('phases', OrderedDict()).items():
        if not isinstance(phase, Mapping):
            raise ConfigurationError(f"phase `{phasename}` doesn't contain a mapping but a {type(phase)}", file=config)
        for variantname, variant in phase.items():
            if not isinstance(variant, Sequence):
                raise ConfigurationError(f"variant `{phasename}.{variantname}` doesn't contain a sequence but a {type(variant)}", file=config)
            for i in reversed(range(len(variant))):
                var = variant[i]
                if isinstance(var, Sequence) and not isinstance(var, (str, bytes)):
                    variant[i:i+1] = var

    # Convert multiple different syntaxes into a single one
    for phase in cfg['phases'].values():
        for variant, items in phase.items():
            for var in items:
                if isinstance(var, str):
                    continue
                if isinstance(var, (OrderedDict, dict)):
                    for var_key in var:
                        if var_key in ('archive', 'fingerprint') and isinstance(var[var_key], (OrderedDict, dict)) and 'artifacts' in var[var_key]:
                            artifacts = var[var_key]['artifacts']

                            # Convert single artifact string to list of single artifact specification
                            if isinstance(artifacts, str):
                                artifacts = [{'pattern': artifacts}]

                            # Expand short hand notation of just the artifact pattern to a full dictionary
                            artifacts = [({'pattern': artifact} if isinstance(artifact, str) else artifact) for artifact in artifacts]

                            try:
                                target = var[var_key]['upload-artifactory'].pop('target')
                            except (KeyError, TypeError):
                                pass
                            else:
                                for artifact in artifacts:
                                    artifact.setdefault('target', target)

                            var[var_key]['artifacts'] = artifacts
                        if var_key == 'junit':
                            if isinstance(var[var_key], str):
                                var[var_key] = [var[var_key]]
                        if var_key == 'with-credentials':
                            if isinstance(var[var_key], str):
                                var[var_key] = OrderedDict([('id', var[var_key])])
                            if not isinstance(var[var_key], Sequence):
                                var[var_key] = [var[var_key]]
                            for cred in var[var_key]:
                                cred_type = cred.setdefault('type', 'username-password')
                                if cred_type == 'username-password':
                                    if not isinstance(cred.setdefault('username-variable', 'USERNAME'), str):
                                        raise ConfigurationError(
                                                f"'username-variable' in with-credentials block `{cred['id']}` for "
                                                f"`{phasename}.{variantname}` is not a string", file=config)
                                    if not isinstance(cred.setdefault('password-variable', 'PASSWORD'), str):
                                        raise ConfigurationError(
                                                f"'password-variable' in with-credentials block `{cred['id']}` for "
                                                f"`{phasename}.{variantname}` is not a string", file=config)
                                elif cred_type == 'file':
                                    if not isinstance(cred.setdefault('filename-variable', 'SECRET_FILE'), str):
                                        raise ConfigurationError(
                                                f"'filename-variable' in with-credentials block `{cred['id']}` for "
                                                f"`{phasename}.{variantname}` is not a string", file=config)
                                elif cred_type == 'string':
                                    if not isinstance(cred.setdefault('string-variable'  , 'SECRET'), str):
                                        raise ConfigurationError(
                                                f"'string-variable' in with-credentials block `{cred['id']}` for "
                                                f"`{phasename}.{variantname}` is not a string", file=config)

                        if var_key == "image":
                            if not isinstance(var[var_key], basic_image_types):
                                raise ConfigurationError(
                                    f"`image` member `{variant}` must be a string or `!image-from-ivy-manifest`",
                                    file=config)

                        if var_key == 'volumes-from':
                            var[var_key] = expand_docker_volumes_from(volume_vars, var[var_key])

    return cfg
