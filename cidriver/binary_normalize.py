from gzip import GzipFile
import os
import tarfile

def normalize(filename, source_date_epoch=0):
    """Make the given file as close to reproducible as possible. Mostly be clamping timestamps to source_date_epoch."""
    if not os.path.isfile(filename):
        return

    if filename.endswith('.tar') or filename.endswith('.tar.gz'):
        with tarfile.open(filename) as in_archive, open(filename + '.tmp', 'wb') as outfile:
            if filename.endswith('.gz'):
                outfile = GzipFile(filename=filename, mode='wb', compresslevel=9, fileobj=outfile, mtime=source_date_epoch)
            try:
                with tarfile.open('', fileobj=outfile, format=tarfile.USTAR_FORMAT, mode='w', encoding='UTF-8') as out_archive:
                    # Sorting the file list ensures that we don't depend on the order that files appear on disk
                    for member in sorted(in_archive, key=lambda x: x.name):
                        # Clamping mtime to source_date_epoch ensures that source files are the only sources of timestamps, not build time
                        member.mtime = min(member.mtime, source_date_epoch)

                        # Prevent including the account details of the account used to execute the build
                        if member.uid == os.getuid() or member.gid == os.getgid():
                            member.uid = 0
                            member.gid = 0
                        member.uname = ''
                        member.gname = ''

                        fileobj = (in_archive.extractfile(member) if member.isfile() else None)
                        out_archive.addfile(member, fileobj)
            finally:
                outfile.close()
        os.utime(filename + '.tmp', (source_date_epoch, source_date_epoch))
        os.rename(filename + '.tmp', filename)
