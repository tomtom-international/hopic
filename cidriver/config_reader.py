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

from collections import (
        OrderedDict,
        Sequence,
    )
try:
    from collections.abc import (
            Mapping,
        )
except ImportError:
    from collections import (
            Mapping,
        )
from click import ClickException
import errno
import json
import os
import re
from six import string_types
import xml.etree.ElementTree as ET
import yaml

__all__ = (
    'expand_vars',
    'read',
    'expand_docker_volume_spec',
)


Pattern = type(re.compile(''))


_variable_interpolation_re = re.compile(r'(?<!\$)\$(?:(\w+)|\{([^}]+)\})')
def expand_vars(vars, expr):
    if isinstance(expr, string_types):
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
    return OrderedDict(loader.construct_pairs(node))


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
        else:
            # Fall back to searching for dependency_manifest.xml in these directories
            for dir in ('WORKSPACE', 'CFGDIR'):
                if dir not in self.volume_vars:
                    continue
                manifest = os.path.join(self.volume_vars[dir], 'dependency_manifest.xml')
                if os.path.exists(manifest):
                    break
        if not os.path.exists(manifest):
            try:
                FileNotFoundError
            except NameError:
                FileNotFoundError = IOError
            raise FileNotFoundError(errno.ENOENT, "required ivy manifest file is not found", os.path.abspath(manifest))

        image = get_toolchain_image_information(manifest)

        # Override dependency manifest with info from config
        image.update(self.props)

        # Construct a full, pullable, image path
        image['image'] = '/'.join(path for path in (image.get('repository'), image.get('path'), image['name']) if path)

        return '{image}:{rev}'.format(**image)


class JSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, IvyManifestImage):
            return str(o)
        elif isinstance(o, Pattern):
            return o.pattern
        return super().default(o)


def ordered_image_ivy_loader(volume_vars):
    OrderedImageLoader = type('OrderedImageLoader', (OrderedLoader,), {})
    OrderedImageLoader.add_constructor(
        '!image-from-ivy-manifest',
        lambda *args: IvyManifestImage(volume_vars, *args)
    )
    return OrderedImageLoader


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


def expand_docker_volume_spec(config_dir, volume_vars, volume_specs):
    guest_volume_vars = {
        'WORKSPACE': '/code',
    }
    volumes = OrderedDict({});
    for volume in volume_specs:
        # Expand string format to dictionary format
        if isinstance(volume, string_types):
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
        if 'source' in volume:
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
    if '/code' not in volumes:
        volumes[guest_volume_vars['WORKSPACE']] = {
            'source': volume_vars['WORKSPACE'],
            'target': guest_volume_vars['WORKSPACE'],
        }

    return volumes


def read(config, volume_vars):
    config_dir = os.path.dirname(config)

    volume_vars = volume_vars.copy()
    volume_vars['CFGDIR'] = config_dir
    OrderedImageLoader = ordered_image_ivy_loader(volume_vars)

    with open(config, 'r') as f:
        cfg = yaml.load(f, OrderedImageLoader)

    cfg['volumes'] = expand_docker_volume_spec(config_dir, volume_vars, cfg.get('volumes', ()))

    version_info = cfg.setdefault('version', OrderedDict())
    if not isinstance(version_info, Mapping):
        raise ConfigurationError("`version` must be a mapping", file=config)

    bump = version_info.setdefault('bump', OrderedDict((('policy', 'constant'),)))
    if not isinstance(bump, (string_types, Mapping, bool)) or isinstance(bump, bool) and bump:
        raise ConfigurationError("`version.bump` must be a mapping, string or the boolean false", file=config)
    elif isinstance(bump, string_types):
        bump = version_info['bump'] = OrderedDict((
                ('policy', 'constant'),
                ('field', bump),
            ))
    elif isinstance(bump, bool):
        assert bump == False
        bump = version_info['bump'] = OrderedDict((
                ('policy', 'disabled'),
            ))
    if not isinstance(bump.get('policy'), string_types):
        raise ConfigurationError("`version.bump.policy` must be a string identifying a version bumping policy to use", file=config)
    bump.setdefault('on-every-change', True)
    if not isinstance(bump['on-every-change'], bool):
        raise ConfigurationError("`version.bump.on-every-change` must be a boolean", file=config)
    if bump['policy'] == 'constant' and not isinstance(bump.get('field'), (string_types, type(None))):
        raise ConfigurationError("`version.bump.field`, if it exists, must be a string identifying a version field to bump for the `constant` policy", file=config)
    if bump['policy'] == 'conventional-commits':
        bump.setdefault('strict', False)
        if not isinstance(version_info['bump']['strict'], bool):
            raise ConfigurationError("`version.bump.strict` field for the `conventional-commits` policy must be a boolean", file=config)
        bump.setdefault('reject-breaking-changes-on', re.compile(r'^(?:release/|rel-).*$'))
        bump.setdefault('reject-new-features-on', re.compile(r'^(?:release/|rel-)\d+\..*$'))
        if not isinstance(bump['reject-breaking-changes-on'], (string_types, Pattern)):
            raise ConfigurationError("`version.bump.reject-breaking-changes-on` field for the `conventional-commits` policy must be a regex or boolean", file=config)
        if not isinstance(bump['reject-new-features-on'], (string_types, Pattern)):
            raise ConfigurationError("`version.bump.reject-new-features-on` field for the `conventional-commits` policy must be a regex or boolean", file=config)

    env_vars = cfg.setdefault('pass-through-environment-vars', ())
    if not (isinstance(env_vars, Sequence) and not isinstance(env_vars, string_types)):
        raise ConfigurationError('`pass-through-environment-vars` must be a sequence of strings', file=config)
    for idx, var in enumerate(env_vars):
        if not isinstance(var, string_types):
            raise ConfigurationError("`pass-through-environment-vars` must be a sequence containing strings only: element {idx} has type {type!r}".format(idx=idx, type=type), file=config)

    image = cfg.setdefault('image', OrderedDict())
    if not isinstance(image, (Mapping, str, IvyManifestImage)):
        raise ConfigurationError("`image` must be a string, mapping, or `!image-from-ivy-manifest`", file=config)
    if not isinstance(image, Mapping):
        image = cfg['image'] = OrderedDict((('default', cfg['image']),))
    for variant, name in image.items():
        if not isinstance(name, (str, IvyManifestImage)):
            raise ConfigurationError("`image` member `{variant}` must be a string or `!image-from-ivy-manifest`".format(**locals()), file=config)

    # Convert multiple different syntaxes into a single one
    for phase in cfg.setdefault('phases', OrderedDict()).values():
        for variant in phase.values():
            for var in variant:
                if isinstance(var, string_types):
                    continue
                if isinstance(var, (OrderedDict, dict)):
                    for var_key in var:
                        if var_key in ('archive', 'fingerprint') and isinstance(var[var_key], (OrderedDict, dict)) and 'artifacts' in var[var_key]:
                            artifacts = var[var_key]['artifacts']

                            # Convert single artifact string to list of single artifact specification
                            if isinstance(artifacts, string_types):
                                artifacts = [{'pattern': artifacts}]

                            # Expand short hand notation of just the artifact pattern to a full dictionary
                            artifacts = [({'pattern': artifact} if isinstance(artifact, string_types) else artifact) for artifact in artifacts]

                            try:
                                target = var[var_key]['upload-artifactory'].pop('target')
                            except (KeyError, TypeError):
                                pass
                            else:
                                for artifact in artifacts:
                                    artifact.setdefault('target', target)

                            var[var_key]['artifacts'] = artifacts
                        if var_key == 'junit':
                            if isinstance(var[var_key], string_types):
                                var[var_key] = [var[var_key]]
                        if var_key == 'with-credentials':
                            if isinstance(var[var_key], string_types):
                                var[var_key] = OrderedDict([('id', var[var_key])])
                        if var_key == 'volumes-from':
                            var[var_key] = expand_docker_volumes_from(volume_vars, var[var_key])

    return cfg
