# Copyright (c) 2019 - 2020 TomTom N.V. (https://tomtom.com)
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

from enum import Enum, unique
import os
import logging
import sys


log = logging.getLogger(__name__)


@unique
class GitObjectType(Enum):
    regular_file = 0b1000
    symlink = 0b1010
    gitlink = 0b1110


def determine_mtime_from_git(repo, files=None, author_time=False):
    encoding = sys.getfilesystemencoding() or sys.getdefaultencoding()

    if files is None:
        files = set(filter(None, repo.git.ls_files('-z', stdout_as_string=False).split(b'\0')))
    else:
        files = set((fname.encode(encoding) if isinstance(fname, str) else fname) for fname in files)

    log.debug('restoring mtime from git')

    # Set all files' modification times to their last commit's time
    whatchanged = repo.git.whatchanged(pretty='format:%at' if author_time else 'format:%ct', as_process=True)
    mtime = 0
    for line in whatchanged.stdout:
        if not files:
            break

        line = line.strip()
        if not line:
            continue
        if line.startswith(b':'):
            line = line[1:]

            props, filenames = line.split(b'\t', 1)
            old_mode, new_mode, old_hash, new_hash, operation = props.split(b' ')
            old_mode, new_mode = int(old_mode, 8), int(new_mode, 8)

            object_type = (new_mode >> (9 + 3)) & 0b1111
            try:
                object_type = GitObjectType(object_type)
            except ValueError:
                pass

            filenames = filenames.split(b'\t')
            if len(filenames) == 1:
                filenames.insert(0, None)
            old_filename, new_filename = filenames

            if new_filename in files:
                files.remove(new_filename)
                yield new_filename.decode(encoding), object_type, mtime
        else:
            mtime = int(line)
    try:
        whatchanged.terminate()
    except OSError:
        pass


def restore_mtime_from_git(repo, files=None):
    for filename, object_type, mtime in determine_mtime_from_git(repo, files):
        path = os.path.join(repo.working_tree_dir, filename)
        if object_type == GitObjectType.symlink:
            # Only attempt to modify symlinks' timestamps when the current system supports it.
            # E.g. Python >= 3.3 and Linux kernel >= 2.6.22
            if os.utime in getattr(os, 'supports_follow_symlinks', set()):
                os.utime(path, (mtime, mtime), follow_symlinks=False)
        elif object_type == GitObjectType.symlink:
            # Skip gitlinks: used by submodules, they don't exist as regular files
            pass
        elif object_type == GitObjectType.regular_file:
            os.utime(path, (mtime, mtime))
