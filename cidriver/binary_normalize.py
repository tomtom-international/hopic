from gzip import GzipFile
import os
import shutil
import tarfile
import sys

if sys.version_info < (3,5,2):
    class TarInfoWithoutGreedyNameSplitting(tarfile.TarInfo):
        """Variant of tarfile.TarInfo that helps to ensure reproducible builds."""

        def _posix_split_name(self, name, *_):
            """Split a path into a prefix and a name part.

            This is a non-greedy variant of this function for Python versions older than 3.5.2.

            This ensures that archives before and after that version are equal at the byte level.
            This change is necessary due to the fix for https://bugs.python.org/issue24838
            """
            prefix = name[:-tarfile.LENGTH_NAME]
            while prefix and prefix[-1] != "/" and len(prefix) < len(name):
                prefix = name[:len(prefix)+1]

            name = name[len(prefix):]
            prefix = prefix[:-1]

            if len(name) > tarfile.LENGTH_NAME:
                raise ValueError("path is too long")
            return prefix, name

    class TarFile(tarfile.TarFile):
        tarinfo = TarInfoWithoutGreedyNameSplitting
else:
    TarFile = tarfile.TarFile

def normalize(filename, fileobj=None, outname='', outfileobj=None, source_date_epoch=0):
    """Make the given file as close to reproducible as possible. Mostly be clamping timestamps to source_date_epoch."""
    if (fileobj is None or outfileobj is None) and not os.path.isfile(filename):
        return

    if filename.endswith('.tar') or filename.endswith('.tar.gz'):
        if outfileobj is None:
            archivefile = outfile = open(filename + '.tmp', 'wb')
        else:
            archivefile = outfile = outfileobj
        with TarFile.open(filename, fileobj=fileobj) as in_archive:
            try:
                compress = False
                if filename.endswith('.gz'):
                    compress = True
                    archivefile = GzipFile(filename=outname, mode='wb', compresslevel=9, fileobj=outfile, mtime=source_date_epoch)

                with TarFile.open(outname, fileobj=archivefile, format=tarfile.USTAR_FORMAT, mode='w', encoding='UTF-8') as out_archive:
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
                if compress:
                    archivefile.close()
            finally:
                if outfileobj is not None:
                    outfile.close()
        if outfileobj is None:
            os.utime(filename + '.tmp', (source_date_epoch, source_date_epoch))
            os.rename(filename + '.tmp', filename)

    elif fileobj is not None and outfileobj is not None:
        shutil.copyfileobj(fileobj, outfileobj)
