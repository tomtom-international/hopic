import click
import os

try:
    from shlex import quote as shquote
except ImportError:
    from pipes import quote as shquote

def echo_cmd(fun, cmd, *args, **kwargs):
    click.echo('Executing: ' + click.style(' '.join(shquote(word) for word in cmd), fg='yellow'), err=True)

    # Set our locale for machine readability with UTF-8
    kwargs = kwargs.copy()
    try:
        env = kwargs['env'].copy()
    except KeyError:
        env = os.environ.copy()
    for key in list(env):
        if key.startswith('LC_') or key in ('LANG', 'LANGUAGE'):
            del env[key]
    env['LANG'] = 'C.UTF-8'
    kwargs['env'] = env

    try:
        output = fun(cmd, *args, **kwargs)
        return (output.decode('UTF-8') if isinstance(output, bytes) else output)
    except Exception as e:
        if hasattr(e, 'child_traceback'):
            click.echo("Child traceback: {}".format(e.child_traceback), err=True)
        raise
