# Copyright (c) 2018 - 2020 TomTom N.V. (https://tomtom.com)
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

from datetime import (
        datetime,
        timedelta,
    )
from enum import Enum, unique
import os
import logging
import sys

from dateutil.tz import (
        tzlocal,
    )
import git

from .versioning import (
        GitVersion,
        read_version,
    )

log = logging.getLogger(__name__)


def determine_source_date(workspace):
    """
    Determine the date of most recent change to the sources in the given workspace
    """

    try:
        with git.Repo(workspace) as repo:
            try:
                source_date = repo.head.commit.committed_datetime
            except ValueError:
                # This happens for a repository that has been initialized but for which a commit hasn't yet been created
                # or checked out.
                #     $ git init
                #     $ git rev-parse HEAD
                #     fatal: ambiguous argument 'HEAD': unknown revision or path not in the working tree.
                return None

            changes = repo.index.diff(None)
            if changes:
                # Ensure that, no matter what happens, the source date is more recent than the check-in date
                source_date = source_date + timedelta(seconds=1)

            # Ensure a more accurate source date is used if there have been any changes to the tracked sources
            for diff in changes:
                if diff.deleted_file:
                    continue

                try:
                    st = os.lstat(os.path.join(repo.working_dir, diff.b_path))
                except OSError:
                    pass
                else:
                    file_date = datetime.utcfromtimestamp(st.st_mtime).replace(tzinfo=tzlocal())
                    source_date = max(source_date, file_date)

            log.debug("Date of last modification to source: %s", source_date)
            return source_date
    except (git.InvalidGitRepositoryError, git.NoSuchPathError):
        return None


def determine_git_version(repo):
    """
    Determines the current version of a git repository based on its tags.
    """

    return GitVersion.from_description(
            repo.git.describe(tags=True, long=True, dirty=True, always=True))


def determine_version(version_info, config_dir, code_dir=None):
    """
    Determines the current version for the given version configuration snippet.
    """

    if 'file' in version_info:
        params = {}
        if 'format' in version_info:
            params['format'] = version_info['format']
        fname = os.path.join(config_dir, version_info['file'])
        if os.path.isfile(fname):
            return read_version(fname, **params)

    if version_info.get('tag', False) and code_dir is not None:
        try:
            with git.Repo(code_dir) as repo:
                gitversion = determine_git_version(repo)
        except (git.InvalidGitRepositoryError, git.NoSuchPathError):
            pass
        else:
            params = {}
            if 'format' in version_info:
                params['format'] = version_info['format']
            if gitversion.dirty:
                return gitversion.to_version(dirty_date=determine_source_date(code_dir), **params)
            else:
                return gitversion.to_version(**params)


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
