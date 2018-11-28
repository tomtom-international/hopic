from collections import OrderedDict
import os
import re
from six import string_types
import xml.etree.ElementTree as ET
import yaml

__all__ = (
        'read',
    )

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

def image_from_ivy_manifest(manifest, loader, node):
    if manifest is None:
        return None

    props = loader.construct_mapping(node) if node.value else {}

    image = get_toolchain_image_information(manifest)

    # Override dependency manifest with info from config
    image.update(props)

    # Construct a full, pullable, image path
    image['image'] = '/'.join(filter(None, (image.get('repository'), image.get('path'), image['name'])))

    return '{image}:{rev}'.format(**image)

def ordered_image_ivy_loader(manifest):
    OrderedImageLoader = type('OrderedImageLoader', (OrderedLoader,), {})
    OrderedImageLoader.add_constructor(
            '!image-from-ivy-manifest',
            lambda *args: image_from_ivy_manifest(manifest, *args)
        )
    return OrderedImageLoader

def expand_docker_volume_spec(config_dir, volume_vars, volume_specs):
    var_re = re.compile(r'\$(?:(\w+)|\{([^}]+)\})')
    guest_volume_vars = {
            'WORKSPACE': '/code',
        }
    volumes = []
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
            source = os.path.expanduser(volume['source'])

            # Expand variables from our "virtual" environment
            last_idx = 0
            new_source = source[:last_idx]
            for var in var_re.finditer(source):
                name = var.group(1) or var.group(2)
                value = volume_vars[name]
                new_source = new_source + source[last_idx:var.start()] + value
                last_idx = var.end()

            new_source = new_source + source[last_idx:]
            # Make relative paths relative to the configuration directory.
            # Absolute paths will be absolute
            source = os.path.join(config_dir, new_source)
            volume['source'] = source

        # Expand target specification resolved on the guest side
        if 'target' in volume:
            target = volume['target']

            if target.startswith('~/'):
                target = '/home/sandbox' + target[1:]

            # Expand variables from our virtual guest side environment
            last_idx = 0
            new_target = target[:last_idx]
            for var in var_re.finditer(target):
                name = var.group(1) or var.group(2)
                value = guest_volume_vars[name]
                new_target = new_target + target[last_idx:var.start()] + value
                last_idx = var.end()

            new_target = new_target + target[last_idx:]
            target = new_target

            volume['target'] = target

        volumes.append(volume)
    return volumes

def read(config, manifest, volume_vars):
    config_dir = os.path.dirname(config)

    OrderedImageLoader = ordered_image_ivy_loader(manifest)

    with open(config, 'r') as f:
        cfg = yaml.load(f, OrderedImageLoader)

    cfg['volumes'] = expand_docker_volume_spec(config_dir, volume_vars, cfg.get('volumes', ()))
    return cfg
