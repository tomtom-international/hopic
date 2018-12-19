import os
import shutil
import subprocess
import tempfile

def normalize(filename, cwd=None, source_date_epoch=0):
    """Make the given file as close to reproducible as possible. Mostly be clamping timestamps to source_date_epoch."""
    if not os.path.isfile(filename):
        return

    if filename.endswith('.tar.gz'):
        tmpd = tempfile.mkdtemp()
        try:
            subprocess.check_call(('tar', '-x', '-z', '-C', tmpd, '-f', filename), cwd=cwd)
            # clamping mtime to SOURCE_DATE_EPOCH should ensure that source files are the only sources of timestamps, not time of building
            # sorting the file list ensures that we don't depend on the order that files appear on disk
            subprocess.check_call(['tar', '--sort=name', '--clamp-mtime', '--mtime=@{source_date_epoch}'.format(**locals()), '--format=ustar', '--owner=0', '--group=0', '--numeric-owner', '-c', '-C', tmpd, '-f', os.path.splitext(filename)[0]] + sorted(os.listdir(tmpd)), cwd=cwd)

            # --no-name: ensures neither the original file name nor its timestamp get recorded in the file content
            subprocess.check_call(('gzip', '--force', '--best', '--no-name', os.path.splitext(filename)[0]), cwd=cwd)
        finally:
            shutil.rmtree(tmpd)
