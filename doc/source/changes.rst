..
   Copyright (c) 2019 - 2020 TomTom N.V. (https://tomtom.com)
   
   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at
   
       http://www.apache.org/licenses/LICENSE-2.0
   
   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.

=========
Changelog
=========

0.15.2
======

Bug fixes
---------

* fix: don't force type conversion to bytes

0.15.1
======

Bug fixes
---------

* fix: prevent pip from looking at the current repo while installing Hopic

0.15.0
======

New features
------------

* feat: don't execute run on change variants if build isn't green
* feat: only version bump if it contains a new feature, bug fix or breaking change

Bug fixes
---------

* fix: increase git-rebase --autosquash timeout from 5 secs to 5 mins
* fix: don't clear Acked-By on autosquashes
* fix: crash when moving submodule in PR
* fix: give decent error messages for conventional commit syntax errors

0.14.1
======

Bug fixes
---------

* fix(groovy): stash files relative to Hopic's workspace, not Jenkins'

0.14.0
======

New Features
------------

* feat: don't clear Acked-By on autosquashes

Bug fixes
---------

* fix: ensure hopic is always executed with a UTF-8 locale

0.13.1
======

Bug fixes
---------

* fix: display type of invalid element instead of 'type' function

0.13.0
======

New features
------------

* feat: add docker image override within a phase #PIPE-367
* feat: allow ptrace operations within docker #PIPE-385
* feat(config): search for specified Ivy manifest relative to $CFGDIR

Improvements
------------

* ci(message-checker): ignore tag merges as well as branch merges
* improvement(logging): display info used by conventional-commits bumping policy

Bug fixes
---------

* fix: replace DOS line endings with Unix line endings in produced commit messages
* fix: use Python 2-compatible super() function
* fix: parsing of conventional-commits on Python 2 #PIPE-405

0.12.1
======

Bug fixes
---------

* fix: handle missing BitBucket users without raising an exception

0.12.0
======

New features
------------

* feat: use conventional commits for bumping and branch restriction (d313ddf)

  * feat: add commit message decomposition class (e0b8a29)
  * feat: add Conventional Commmit parser (6e90e39)
  * feat: add conventional commit footer parsing (9d04254)
  * feat(config): add a bumping policy (ef34046)
  * feat(merge): parse commit messages according to the configured policy (27d8858)
  * feat(merge): bump the correct version field according to conventional commits (2905ea9)
  * feat(merge): allow a version bumping policy for less than every change (eb3b8b6)
  * feat(merge): reject breaking changes and new features on release branches (d200cdf)

* feat: make clean checkout commands customizable (3b0fafb)
* feat: allow multiple with-credentials (d3418a1)

Improvements
------------

* improvement: detect wrongly typed `image` options (8c706af)
* refactor(config): unify the produced 'image' config structure (449c744)
* improvement(config): display config error messages without backtrace (c8329b0)
* improvement: have workspace default to containing repository of config file (e8e89c7)
* docs: add documentation for description and stash (a427d90)

Bug fixes
---------

* fix(show-config): allow JSON serialization of '!image-from-ivy-manifest' images (b37321b)
* fix(carver): separate the major, minor and patch components by dots (b23b733)
* fix: use relative config path for version file (59199f1)
* fix: handle CredentialNotFoundException where it can be thrown (a47cdd4)
* fix: avoid wrapping in withCredentials when no credentials are requested (f08e9c2)

0.11.0
======

New features
------------

* feat: make execution possible with 'hopic' as command

Improvements
------------

* improvement: raise exception when specified ivy manifest does not exist
* improvement(log): add hint for initial version tag

Bug fixes
---------

* fix: only restore mtime for regular files and symlinks
* fix: use the common ancestor of the source and target commit for autosquash
* fix: ignore submodule checkout failure during checkout-source-tree
* fix: use git submodule sync to update submodule url when checking out source

0.10.2
======

Bug fixes
---------

* fix: provide an empty dict instead of nothing for metadata-less variants

0.10.1
======

Improvements
------------

* improvement(groovy): retrieve execution graph in a single 'getinfo' call

Bug fixes
---------

* fix: use full repository directory when updating submodules recursively
* fix: reset the config directory after re-reading the config file

0.10.0
======

New features
------------

* feat: allow passing environment variables into containers

Improvements
------------

* improvement: log reason why Bitbucket refuses to merge

Bug fixes
---------

* fix: use blacklisted object when printing error to avoid crash

0.9.0
======

New features
------------

* feat: checkout submodules too during checkout
* feat: note the used Hopic version in the merge commit

Improvements
------------

* improvement: use Hopic's default config location in the CI-Driver
* improvement(groovy): log when we're skipping submission for replays

Bug fixes
---------

* fix: re-check default locations for config file after checking out and merging

0.8.1
======

Bug fixes
---------

* fix(groovy): avoid confusing e-mail addresses for usernames
* fix(carver): don't include the prerelease portion in tags by default

0.8.0
======

New features
------------

* feat: reject submission of replay builds

0.7.1
======

Bug fixes
---------

* fix(groovy): move regex evaluation to non-CPS context

Improvements
------------

* improvement: use : as GIT_EDITOR to prevent starting an editor at all

0.7.0
======

New features
------------

* feat: add support for volume overrides per variant

0.6.0
======

New features
------------

* feat: add support for Docker `--volume-from` mapping at variant level
* feat: expose current GIT_COMMIT and GIT_BRANCH

Bug fixes
---------

* fix: ensure that the execution flow is built _after_ merging

0.5.1
======

Bug fixes
---------

* fix: only remove/add files from non-empty lists
* doc: document all release branch versions

0.5.0
======

New features
------------

* feat: support file and string credentials too

0.4.1
======

Logging improvements
--------------------

* logging(debug): tell when we're restoring mtimes
* improvement: don't log a back trace for fatally terminated commands

0.4.0
======

New features
------------

* feat: make Hopic's verbosity controllable via environment variables

0.3.1
======

Improvements
------------

* improvement: add debug logging about pre/post autosquashing commit sets
* improvement: log the failure information when failing to autosquash
* improvement: ensure hash stability of autosquashed commit

0.3.0
======

New features
------------

* feat: add support for promoting builds after submission
* feat: make default node expression configurable via optional param
* feat: support feature branches
* feat: execute a command once for every autosquashed source commit

0.2.5
======

Documentation fixes
-------------------

* docs: match installation URL to current branch

0.2.4
======

Bug fixes
---------

* fix: ensure that the execution flow is built *after* merging

0.2.3
======

Bug fixes
---------

* fix: only remove/add files from non-empty lists
* doc: document all release branch versions

0.2.2
======

Improvements
------------

* improvement: better logging about submittability

Bug fixes
---------

* fix: only restore mtimes for clean builds
* fix: avoid scientific notation for timestamps
* fix: workaround Groovy regexes producing null matches
* fix: ensure $HOME is available for modality changes

0.2.1
======

Artifactory related improvements

Improvements
------------

* improvement: handle artifactory 'target' in config reader
* improvement: expose all versioning related environment variables
* improvement: perform all artifactory build uploads from a single node
* improvement: translate Artifactory FileSpec patterns to Ant FileSet

0.2.0
======

New features
------------

* feat: execute a command once for every source commit
* feat: make the branch name, build id and lock name public
* feat(bb-pr): expand '@user' tokens in pull request descriptions
* feat: add support for executing commands with credentials
* feat(git): support for other branches in subdirectory worktrees

0.1.9
======

Bug fixes
---------

fix: ensure that the execution flow is built _after_ merging

0.1.8
======

Documentation
-------------

* doc: document all release branch versions

0.1.7
======

Bug fixes
---------

* fix: only restore mtimes for clean builds
* fix: avoid scientific notation for timestamps
* fix: ensure $HOME is available for modality changes

Improvements
------------

* improvement: better logging about submittability

0.1.6
======

Artifactory related improvements

Improvements
------------

* improvement: handle artifactory 'target' in config reader
* improvement: expose all versioning related environment variables
* improvement: perform all artifactory build uploads from a single node
* improvement: translate Artifactory FileSpec patterns to Ant FileSet

0.1.5
======

Bugfix and greater docker volume specification flexibility

Improvements
------------

* improvement: allow overriding the ${WORKSPACE} volume

Bug fixes
---------

* fix: use slicing instead of indexing to get string suffix

0.1.4
======

Fix versioning bugs and improve CLI defaults

Improvements
------------

* improvement: don't destroy config sections until we're done with them
* improvement: give --config a default
* improvement: give --workspace a decent default

Bug fixes
---------

* fix: find version file relative to CI config file
* fix: use version-policy specific defaults for the formatting of tags
* fix: prevent tag failure for non-semver versioning policies

0.1.3
======

Fix various bugs and produce more stable build ids on Artifactory

Improvements
------------

* improvement: produce more stable build names and numbers on Artifactory

Bug fixes
---------

* fix: don't forget to delete checkouts if we don't have change-only steps
* fix: remove checkouts without wrongly checking for them first
* fix: work around bug JENKINS-47730
* fix: don't break when given multiple target artifactory servers
* fix: lock without 'run-on-change: only' steps too when submitting
* fix: workaround Jenkins Git plugin bug causing wrong GIT_COMMIT
* fix: submit even if we don't have any build steps
* fix: prevent infinite downloads from blocking the build forever
* fix: don't read config file before checking it out
* fix: complain when trying to bump a non-existant version
* fix(config): allow using Hopic CI driver without build steps

0.1.2
======

Improvements
------------

* improvement: allow stacking prepare-source-tree commands

Bug fixes
---------

* fix: prevent failure when failing to read an optional config file
* fix: apply version bumping policy for the change that introduces it too
* fix: prevent interpreting local time as UTC
* fix: don't remove submit-config until successfully used
* fix(groovy): lock change target branch instead of target repo
* fix(git): don't remove or add empty lists of files
* fix(restore-mtimes): don't update mtime of symlink targets
* fix(shell-completion): only yield completions matching (partial) input
* fix(groovy): allow expansion of ${WORKSPACE} always

Documentation
-------------

* docs: add the start of documentation

0.1.1
======

Bug fixes
---------

* fix: properly detect submission failures

0.1.0
======

Initial release
