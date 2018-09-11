import click

from datetime import datetime
from dateutil.parser import parse as date_parse
from dateutil.tz import (tzoffset, tzlocal, tzutc)
import os
import re
import xml.etree.ElementTree as ET
import yaml

class DateTime(click.ParamType):
    name = 'date'
    stamp_re = re.compile(r'^@(?P<utcstamp>\d+)(?:\s+(?P<tzdir>[-+])(?P<tzhour>\d{1,2}):?(?P<tzmin>\d{2}))?$')

    def convert(self, value, param, ctx):
        if value is None or isinstance(value, datetime):
            return value

        try:
            stamp = self.stamp_re.match(value)
            if stamp:
                def int_or_none(i):
                    if i is None:
                        return None
                    return int(i)

                tzdir  = (-1 if stamp.group('tzdir') == '-' else 1)
                tzhour = int_or_none(stamp.group('tzhour'))
                tzmin  = int_or_none(stamp.group('tzmin' ))

                if tzhour is not None:
                    tz = tzoffset(None, tzdir * (tzhour * 3600 + tzmin * 60))
                else:
                    tz = tzutc()
                return datetime.fromtimestamp(int(stamp.group('utcstamp')), tz)

            dt = date_parse(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tzlocal())
            return dt
        except ValueError as e:
            self.fail('Could not parse datetime string "{value}": {e}'.format(value=value, e=' '.join(e.args)), param, ctx)

def get_toolchain_image_information(dependency_manifest):
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

@click.group(context_settings=dict(help_option_names=('-h', '--help')))
@click.option('--config', type=click.Path(exists=True, readable=True, resolve_path=True), required=True)
@click.option('--workspace', type=click.Path(exists=True, file_okay=False, dir_okay=True))
@click.option('--dependency-manifest', type=click.File('r'))
@click.pass_context
def cli(ctx, config, workspace, dependency_manifest):
    if ctx.obj is None:
        ctx.obj = {}

    config_dir = os.path.dirname(config)
    def image_from_ivy_manifest(loader, node):
        props = loader.construct_mapping(node) if node.value else {}

        # Fallback to 'dependency_manifest.xml' file in same directory as config
        manifest = (dependency_manifest if dependency_manifest
                else (os.path.join(workspace or config_dir, 'dependency_manifest.xml')))
        image = get_toolchain_image_information(manifest)

        # Override dependency manifest with info from config
        image.update(props)

        # Construct a full, pullable, image path
        image['image'] = os.path.join(*filter(None, (image.get('repository'), image.get('path'), image['name'])))

        return '{image}:{rev}'.format(**image)
    yaml.add_constructor('!image-from-ivy-manifest', image_from_ivy_manifest)

    with open(config, 'r') as f:
        cfg = yaml.load(f)

    ctx.obj['cfg'] = cfg

@cli.command('checkout-source-tree')
@click.option('--target-remote'     , metavar='<url>')
@click.option('--target-ref'        , metavar='<ref>')
def checkout_source_tree(target_remote, target_ref):
    pass

@cli.command('prepare-source-tree')
# git
@click.option('--target-remote'     , metavar='<url>', help='<target> remote in which to merge <source>')
@click.option('--target-ref'        , metavar='<ref>', help='ref of <target> remote in which to merge <source>')
@click.option('--source-remote'     , metavar='<url>', help='<source> remote to merge into <target>')
@click.option('--source-ref'        , metavar='<ref>', help='ref of <source> remote to merge into <target>')
@click.option('--pull-request'      , metavar='<identifier>'           , help='Identifier of pull-request to use in merge commit message')
@click.option('--pull-request-title', metavar='<title>'                , help='''Pull request title to incorporate in merge commit's subject line''')
@click.option('--author-name'       , metavar='<name>'                 , help='''Name of pull-request's author''')
@click.option('--author-email'      , metavar='<email>'                , help='''E-mail address of pull-request's author''')
@click.option('--author-date'       , metavar='<date>', type=DateTime(), help='''Time of last update to the pull-request''')
# misc
@click.option('--bump-api'          , type=click.Choice(('major', 'minor', 'patch')))
def prepare_source_tree(target_remote, target_ref, source_remote, source_ref, pull_request, pull_request_title, author_name, author_email, author_date, bump_api):
    pass

@cli.command()
@click.option('--ref'               , metavar='<ref>', help='''Commit-ish that's checked out and to be built''')
def build(ref):
    pass

@cli.command()
@click.option('--target-remote'     , metavar='<url>')
@click.option('--target-ref'        , metavar='<ref>')
@click.option('--ref'               , metavar='<ref>', help='''Commit-ish that has been verified and is to be submitted''')
def submit(target_remote, target_ref, ref):
    pass
