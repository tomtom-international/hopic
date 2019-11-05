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
import errno
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

class ConfigurationError(Exception):
    pass


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
            raise FileNotFoundError(errno.ENOENT, "required ivy manifest file is not found", os.path.abspath(manifest))

        image = get_toolchain_image_information(manifest)

        # Override dependency manifest with info from config
        image.update(self.props)

        # Construct a full, pullable, image path
        image['image'] = '/'.join(path for path in (image.get('repository'), image.get('path'), image['name']) if path)

        return '{image}:{rev}'.format(**image)


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

    env_vars = cfg.setdefault('pass-through-environment-vars', ())
    if not (isinstance(env_vars, Sequence) and not isinstance(env_vars, string_types)):
        raise ConfigurationError('`pass-through-environment-vars` must be a sequence of strings')
    for idx, var in enumerate(env_vars):
        if not isinstance(var, string_types):
            raise ConfigurationError("`pass-through-environment-vars` must be a sequence containing strings only: element {idx} has type {type!r}".format(idx=idx, type=type))

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
