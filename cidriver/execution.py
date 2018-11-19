import click

try:
    from shlex import quote as shquote
except ImportError:
    from pipes import quote as shquote

def echo_cmd(fun, cmd, *args, **kwargs):
  click.echo('Executing: ' + click.style(' '.join(shquote(word) for word in cmd), fg='yellow'), err=True)
  try:
    return fun(cmd, *args, **kwargs)
  except Exception as e:
    if hasattr(e, 'child_traceback'):
      click.echo("Child traceback: {}".format(e.child_traceback), err=True)
    raise
