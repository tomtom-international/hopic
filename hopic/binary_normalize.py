# Copyright (c) 2018 - 2021 TomTom N.V. (https://tomtom.com)
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

import copy
from decimal import Decimal
from gzip import GzipFile
import os
import shutil
import sys
import tarfile
from tarfile import TarFile


class ArInfo(object):
    """Represents a single member in an ar archive."""
    HEADER_SIZE = 60

    mtime = 0
    uid = 0
    gid = 0
    perm = 0

    def __init__(self, fileobj, offset, size, name=None):
        self.fileobj = fileobj
        self.offset = offset
        self.size = size
        self.name = name
        self.pos = 0
        self.mode = 'rb'
        self.padded_size = size + (size % 2)

    def tell(self):
        return self.pos

    def seek(self, offset, whence=os.SEEK_SET):
        if whence == os.SEEK_SET:
            new_pos = offset
        elif whence == os.SEEK_CUR:
            new_pos = self.pos + offset
        elif whence == os.SEEK_END:
            new_pos = self.size + offset

        self.pos = max(0, min(new_pos, self.size))

    def read(self, size=None):
        max_size = self.size - self.pos
        if size is None:
            size = max_size
        else:
            size = min(size, max_size)

        self.fileobj.seek(self.offset + self.pos)
        self.pos += size

        buf = self.fileobj.read(size)
        if len(buf) != size:
            raise IOError("unexpected end of data")
        return buf

    def write(self, buf):
        if self.mode != 'ab':
            raise IOError(f"bad operation for mode {self.mode!r}")

        self.fileobj.seek(self.offset + self.pos)
        self.fileobj.write(buf)
        self.size += max(0, len(buf) - (self.size - self.pos))
        self.pos += len(buf)
        return len(buf)

    def close(self):
        if self.mode == 'rb':
            return

        remainder = self.size % 2
        self.padded_size = self.size + remainder
        if remainder > 0:
            self.fileobj.seek(self.offset + self.size)
            self.fileobj.write(b'\n' * remainder)

        self.fileobj.seek(self.offset - self.HEADER_SIZE)
        self.fileobj.write(self.tobuf())
        self.arfile.offset = self.offset + self.padded_size
        self.mode = 'rb'

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        if type is None:
            self.close()
        else:
            # An exception occurred. Don't call close() because we cannot afford to try writing the header
            self.mode = 'rb'

    @classmethod
    def frombuf(cls, fileobj, buf, data_offset):
        if len(buf) != cls.HEADER_SIZE:
            raise IOError(f"Too short a header for ar file: {len(buf)} instead of {cls.HEADER_SIZE}")
        member_name, mtime, uid, gid, perm, size, _ = (
            buf[ 0:16],  # noqa: E201
            buf[16:28],
            buf[28:34],
            buf[34:40],
            buf[40:48],
            buf[48:58],
            buf[58:60],
        )
        member_name = member_name.rstrip(b' ').rstrip(b'/').decode('ASCII')

        try:
            size = int(size)
        except ValueError:
            raise IOError("Non-numeric file size in ar header")

        arinfo = cls(fileobj, data_offset, size, member_name or None)

        arinfo.mtime = int(mtime)
        arinfo.uid   = int(uid)
        arinfo.gid   = int(gid)
        arinfo.perm  = int(perm, 8)

        return arinfo

    def tobuf(self):
        buf = f"{self.name:<16.16}{self.mtime:<12d}{self.uid:<6d}{self.gid:<6d}{self.perm:<8o}{self.size:<10d}`\n".encode('ASCII')
        if len(buf) != self.HEADER_SIZE:
            raise IOError(f"Exceeding maximum header size: {buf}")
        return buf


class ArFile(object):
    """Provides an interface to ar archives."""
    arinfo = ArInfo

    def __init__(self, name=None, mode='r', fileobj=None):
        modes = {'r': 'rb', 'w': 'wb'}
        if mode not in modes:
            raise ValueError("mode must be 'r' or 'w'")
        self.mode = mode

        if not fileobj:
            fileobj = open(name, modes[mode])
            self._extfileobj = False
        else:
            if name is None:
                name = getattr(fileobj, 'name', None)
            self._extfileobj = True

        try:
            self.name = os.path.abspath(name) if name else None
            self.fileobj = fileobj
            self.closed = False

            if mode == 'r':
                self.read_signature = False
            if mode == 'w':
                self.fileobj.write(b'!<arch>\n')
            self.offset = self.fileobj.tell()
        except:  # noqa: E722: we re-raise, so it's not a problem
            if not self._extfileobj:
                fileobj.close()
            self.closed = True
            raise

    def close(self):
        if self.closed:
            return

        self.closed = True
        if not self._extfileobj:
            self.fileobj.close()

    def next(self):
        if self.closed:
            raise IOError("ArFile is closed")
        if self.mode != 'r':
            raise IOError(f"bad operation for mode {self.mode!r}")

        self.fileobj.seek(self.offset)
        if not self.read_signature:
            signature = self.fileobj.read(8)
            self.offset += len(signature)
            expected_signature = b'!<arch>\n'
            if len(signature) < len(expected_signature):
                raise StopIteration
            if signature != expected_signature:
                raise IOError('Invalid ar file signature')
            self.read_signature = True

        file_header = self.fileobj.read(self.arinfo.HEADER_SIZE)
        self.offset += len(file_header)
        if len(file_header) < self.arinfo.HEADER_SIZE:
            raise StopIteration
        arinfo = self.arinfo.frombuf(self.fileobj, file_header, self.offset)
        self.offset += arinfo.padded_size
        return arinfo

    def __next__(self):
        return self.next()

    def __iter__(self):
        if self.closed:
            raise IOError("ArFile is closed")
        if self.mode != 'r':
            raise IOError(f"bad operation for mode {self.mode!r}")

        self.offset = 0
        self.read_signature = False
        return self

    def appendfile(self, arinfo):
        if self.closed:
            raise IOError("ArFile is closed")
        if self.mode != 'w':
            raise IOError(f"bad operation for mode {self.mode!r}")

        arinfo = copy.copy(arinfo)

        self.fileobj.seek(self.offset)
        buf = arinfo.tobuf()
        self.fileobj.write(buf)
        arinfo.offset = self.offset + len(buf)

        arinfo.fileobj = self.fileobj
        arinfo.arfile = self
        arinfo.mode = 'ab'
        arinfo.size = 0
        arinfo.pos = 0

        return arinfo

    def addfile(self, arinfo, fileobj):
        with self.appendfile(arinfo) as outfile:
            shutil.copyfileobj(fileobj, outfile)

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self.close()


def normalize(filename, fileobj=None, outname='', outfileobj=None, source_date_epoch=0):
    """Make the given file as close to reproducible as possible. Mostly be clamping timestamps to source_date_epoch."""
    if (fileobj is None or outfileobj is None) and not os.path.isfile(filename):
        return

    if filename.suffix == ".tar" or filename.suffixes[-2:] == [".tar", ".gz"]:
        if outfileobj is None:
            archivefile = outfile = open(filename.with_suffix(filename.suffix + ".tmp"), 'wb')
        else:
            archivefile = outfile = outfileobj
        with TarFile.open(filename, fileobj=fileobj) as in_archive:
            try:
                compress = False
                if filename.suffix == ".gz":
                    compress = True
                    archivefile = GzipFile(filename=outname, mode='wb', compresslevel=9, fileobj=outfile, mtime=source_date_epoch)

                with TarFile.open(outname, fileobj=archivefile, format=tarfile.PAX_FORMAT, mode='w', encoding='UTF-8') as out_archive:
                    if sys.version_info < (3, 9, 0):
                        # Erase major/minor fields for non-device files as other tar-archive producing tools do and Python does since 3.9.0.

                        archive_props = {
                            "offset": 0,
                            "next_header_offset": 0,
                        }
                        original_write = archivefile.write

                        def write_archivefile(data):
                            # Intercept writes to the archive file and rewrite any headers matching our criteria
                            for block_idx in range((len(data) + tarfile.BLOCKSIZE - 1) // tarfile.BLOCKSIZE):
                                block = data[block_idx * tarfile.BLOCKSIZE : (block_idx + 1) * tarfile.BLOCKSIZE]
                                cur_offset = archive_props["offset"]
                                archive_props["offset"] += len(block)

                                if cur_offset != archive_props["next_header_offset"]:
                                    continue

                                try:
                                    tarinfo = out_archive.tarinfo.frombuf(block, out_archive.encoding, out_archive.errors)
                                except tarfile.EOFHeaderError:  # type: ignore[attr-defined]
                                    break

                                block_count = (tarinfo.size + tarfile.BLOCKSIZE - 1) // tarfile.BLOCKSIZE
                                archive_props["next_header_offset"] = archive_props["offset"] + block_count * tarfile.BLOCKSIZE

                                if tarinfo.devmajor != 0 or tarinfo.devmajor != 0 or tarinfo.type in (tarfile.CHRTYPE, tarfile.BLKTYPE):
                                    continue

                                # Erase major/minor fields for non-device files as other tar-archive producing tools do.
                                if not isinstance(data, bytearray):
                                    data = bytearray(data)

                                DEVNUMBERS_OFFSET = 329
                                DEVNUMBERS_LENGTH = 2 * 8
                                DEVNUMBERS_RANGE = slice(
                                    block_idx * tarfile.BLOCKSIZE + DEVNUMBERS_OFFSET,
                                    block_idx * tarfile.BLOCKSIZE + DEVNUMBERS_OFFSET + DEVNUMBERS_LENGTH,
                                )
                                CHECKSUM_OFFSET = 148
                                CHECKSUM_LENGTH = 8
                                CHECKSUM_RANGE = slice(
                                    block_idx * tarfile.BLOCKSIZE + CHECKSUM_OFFSET,
                                    block_idx * tarfile.BLOCKSIZE + CHECKSUM_OFFSET + CHECKSUM_LENGTH,
                                )

                                data[DEVNUMBERS_RANGE] = b"\0" * DEVNUMBERS_LENGTH

                                # Recompute checksum, but whipe checksum field (with spaces) before doing so
                                data[CHECKSUM_RANGE] = b" " * CHECKSUM_LENGTH
                                chksum, *_ = tarfile.calc_chksums(  # type: ignore[attr-defined]
                                    data[block_idx * tarfile.BLOCKSIZE : (block_idx + 1) * tarfile.BLOCKSIZE]
                                )
                                data[CHECKSUM_RANGE] = b"%06o\0 " % (chksum,)

                            return original_write(data)

                        archivefile.write = write_archivefile

                    # Sorting the file list ensures that we don't depend on the order that files appear on disk
                    for member in sorted(in_archive, key=lambda x: x.name):
                        # Clamping mtime to source_date_epoch ensures that source files are the only sources of timestamps, not build time
                        mtime = min(Decimal(member.pax_headers.pop("mtime", member.mtime)), source_date_epoch)
                        # Store in PAX header if it needs sub-integer precision
                        if int(mtime) != mtime:
                            member.mtime = int(mtime)
                            member.pax_headers["mtime"] = f"{mtime:f}"
                        else:
                            member.mtime = int(mtime)
                            member.pax_headers.pop("mtime", None)

                        # Don't store atime or ctime. These are just too volatile.
                        member.pax_headers.pop("atime", None)
                        member.pax_headers.pop("ctime", None)

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
            os.utime(outfile.name, (source_date_epoch, source_date_epoch))
            os.rename(outfile.name, filename)

    elif filename.suffix == ".deb":
        with ArFile(filename) as in_pkg, ArFile(filename.with_suffix(filename.suffix + ".tmp"), 'w') as out_pkg:
            # A valid Debian package contains these files in this order
            expected_files = [
                (u'debian-binary',),
                (u'control.tar', u'control.tar.gz', u'control.tar.xz'),
                (u'data.tar', u'data.tar.gz', u'data.tar.bz2', u'data.tar.xz'),
            ]
            for pkg_member in in_pkg:
                if expected_files:
                    expected = expected_files.pop(0)
                    if pkg_member.name not in expected:
                        break

                # Clamping mtime to source_date_epoch ensures that source files are the only sources of timestamps, not build time
                pkg_member.mtime = min(pkg_member.mtime, source_date_epoch)

                # Prevent including permission information
                pkg_member.uid = 0
                pkg_member.gid = 0
                pkg_member.perm = 0o100644

                with out_pkg.appendfile(pkg_member) as outfile:
                    normalize(pkg_member.name, fileobj=pkg_member, outname=pkg_member.name, outfileobj=outfile, source_date_epoch=source_date_epoch)
            else:
                in_pkg.close()
                out_pkg.close()
                os.utime(out_pkg.name, (source_date_epoch, source_date_epoch))
                os.rename(out_pkg.name, in_pkg.name)

    elif fileobj is not None and outfileobj is not None:
        shutil.copyfileobj(fileobj, outfileobj)
