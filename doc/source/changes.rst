..
   Copyright (c) 2019 - 2021 TomTom N.V. (https://tomtom.com)
   
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

1.41.1-8+gec19e87f53e49c
======

* fix(ciDriver): test MODALITY from env, not from params (#375)
* fix: raise error when initial version couldn't be found (#365)

1.41.0
======

New features
------------

* feat: use change applicator message to determine version bump (#367)
* feat(groovy): expose Jenkins' version in environment variable JENKINS_VERSION (#372)
* feat(build): support a per-command and per-variant timeout (#373)

Improvements
------------

* improvement(build): log reason for skipping run-on-change steps (#366)

Bug fixes
---------

* fix(build): run new-version-only steps whenever the version is actually bumped (#366)
* fix(modality): expand vars in commit message (#370)

Documentation improvements
--------------------------

* docs: repair list in configuration:PUBLISH_VERSION (#368)

1.40.2
======

Improvements
------------

* improvement(hotfix): accept periods in hotfix IDs too (#363)

Bug fixes
---------

* fix(binary-normalize): ensure that long paths can be archived as well (#364)
* fix(binary-normalize): clamp mtime in PAX header too (#364)
* fix(hotfix): reject all PEP-440 reserved keywords from being used in hotfix IDs (#363)

1.40.1
======

Bug fixes
---------

* fix(merge): determine base version before checking that it's a valid hotfix base (#362)

1.40.0
======

* feat: extend top-level config instead of replacing it with 'config' sub-member (#360)
* feat: print critical path of pipeline (#355)
* feat(merge): version bumping and error checking for PRs to hotfix branches (#357)

Documentation improvements
--------------------------

* docs: use consistent boolean form in docs and examples (#310)

1.39.2
======

Bug fixes
---------

* fix: initialize all global variables when parsing config (#354)
* fix: warning about old-style metadata.entry_points usage (#361)
* fix: introduce mypy type checking and fix type annotations (#359)

1.39.1
======

Bug fixes
---------

* fix(binary-normalize): zero out major/minor number of non-device files (#358)

1.39.0
======

New features
------------

* feat(build): expose build name, number, URL, start time and duration as vars (#352)
* feat: log ip address of node first time it is used (#298)

1.38.0
======

New features
------------

* feat: make source commit ranges available without 'foreach' (#349)

Bug fixes
---------

* fix: read config file directly after merge (#350)
* fix: pin typeguard version until breaking issue is resolved (#353)

1.37.0
======

New features
------------

* feat(template.utils): support options with the same name as Python keywords (#348)

1.36.0
======

New features
------------

* feat(template): add helper functions for creating command argument lists (#346)

Bug fixes
---------

* fix: only parse merge commit message for merge change requests (#347)

1.35.0
======

New features
------------

* feat(groovy): expose lock wrapper (#343)


Performance improvements
------------------------

* perf: cache template entry points (#344)

Improvements
------------

* improvement(groovy): don't log NOP submits as having run (#342)

1.34.1
======

Bug fixes
---------

* fix(build): allow variables to be used in artifact/junit patterns (#341)

1.34.0
======

New features
------------

* feat(groovy): allow taking additional locks only from specified phase onward (#338)

1.33.2
======

Bug fixes
---------

* fix: remove indentation in git notes message (#339)

1.33.1
======

Bug fixes
---------

* fix: cache static jenkins SCM properties (#332)
* fix: avoid adding duplicate notes to same commit (#332)

1.33.0
======

New features
------------

* feat: add allow-missing for junit and archive config (#325)
* feat: increase abbreviated commit hash' length in version number to 14 nibbles (#328)
* feat(groovy): abort submits when BitBucket PR state changed since the start (#327)
* feat(config): add new default config file location .ci/hopic-ci-config.yaml (#336)

Improvements
------------

* improvement(config): type check member options of archive/fingerprint/junit (#329)
* improvement(archival): process Ant-style ``dir/**/subdir/*`` glob patterns (#329)
* improvement(build): detect and complain about declared-but-missing artifacts (#329)

Bug fixes
---------

* fix(groovy): restore BB PR metadata to avoid altering the msg during a build (#330)
* fix(autocomplete): load default config file as well during autocompletion (#331)
* fix: avoid using specific versions of typeguard (#333)
* fix(unbundle): don't delete tags we cannot fetch again (#335)

1.32.0
======

New features
------------

* feat(checkout): support checking out a specific commit of the target branch (#316)

Improvements
------------

* improvement(groovy): abort early when a build's PR changed since build started (#315)

Bug fixes
---------

* fix(groovy): ensure to build the same commit of the target branch on all nodes (#316)
* fix(checkout): don't try to check out the same commit on the configured repo too (#323)
* fix(groovy): only pin target branch to commits obtained while holding merge lock (#324)
* fix: only run docker with tty when stdout is a terminal (#321)

1.31.0
======

New features
------------

* feat: expose ci lock timings (#313)
* feat: add lock-on-change to ci-locks config (#319)
* feat: expose has_prerelease function from cidriver (#319)

Improvements
------------

* refactor(groovy): extract taking a resource lock to new function (#313)
* improvement: log a more helpful error for invalid merge commit message (#312)

1.30.0
======

New features
------------

* feat: add version check for pull request title (#300)
* feat: expose node allocation timings via an interface (#302)
* feat: add more detailed information to build info metrics (#309)
* feat: introduce allow-empty-archive (#307)
* feat: allow extra 'docker run' args to be specified per variant (#284)

Improvements
------------

* improvement(versioning): log 'git describe' form of failed-to-parse git version (#301)
* improvement: set human-friendly error for unknown VERSION (#266)

Documentation improvements
--------------------------

* docs: requirements for the version format to support a hotfix process (#308)

Bug fixes
---------

* fix: use only simple types on node information interface (#309)
* fix: handle all build status values of Jenkins (#309)
* fix: run archive before junit (#307)

1.29.2
======

Improvements
------------

* improvement(groovy): mark methods that override something from the base as such (#299)

Bug fixes
---------

* fix(groovy): abort before submitting a changed PR (#295)
* fix: align abort_if_changed method signature (#297)
* fix(groovy): annotate and align method signatures between base and derived (#299)

1.29.1
======

Bug fixes
---------

* fix(credentials): import the submodules of 'keyring' that we use (#296)

1.29.0
======

New features
------------

* feat(config): add option to avoid waiting on the full previous phase (#270)
* feat: execute a variant's next phase in the current one if asked to (#270)
* feat(groovy): detect and skip execution of empty NOP variants (#270)

Improvements
------------

* improvement: provide information when build is called with unknown parameters (#289)
* improvement: use the variant's name only as the parallel block's name (#270)
* improvement(config): reject differing run-on-change settings in the same variant (#270)
* improvement(groovy): log output from 'git' commands too at debug verbosity (#291)

Bug fixes
---------

* fix: don't ask for credentials in a dry run (#288)
* fix: restore java based path relativization (#290)
* fix(groovy): ensure we build the same commit from the PR on every node (#292)
* fix(groovy): use Iterable.first() instead of Iterable[0] (#293)
* fix(groovy): add missing script-approval to determine job properties (#294)

1.28.1
======

Improvements
------------

* add labels to all ci-driver build steps (#287)

Bug fixes
---------

* fix: ensure base class of MissingCredentialVar is initialized (#286)
* fix: do not pretend that a missing credential is a credential (#286)
* fix(git_time): check for intended GitObjectType value (#285)

1.28.0
======

New features
------------

* feat(templates): use 'typeguard' pkg to type check arguments to templates (#272)
* feat(config): complain about templates' defaults not matching their own types (#273)
* feat(groovy): log node usage at end of pipeline (#275)
* feat(groovy): automatically add verbosity and clean parameters to jobs (#283)
* feat(config): support generator template functions (#282)
* feat(config): type check the results yielded from a generator template function (#282)

Improvements
------------

* improvement: git clean sub modules and sub repositories too (#274)
* improvement(logging): log version bumps at INFO level including original version (#276)                     
* improvement(extensions): inform users when they might need to update pip (#281)             
* improvement(config): check return value of templates agains their annotations (#282)
* improvement(config): raise type error from yield statement in generator template (#282)

Bug fixes
---------

* fix(groovy): keep a reference to the usage entry we're updating (broken by #275) (#278)                     
* fix(credentials): don't encode for forms but for URLs (#280)

Improvements
------------

* improvement: git clean sub modules and sub repositories too (#274)

1.27.1
======

Bug fixes
---------

* fix(groovy): force new checkout on initial node when publishing (#264)
* fix: don't try to obfuscate empty credential strings (#267)
* fix(config): recurse when flattening command lists (#271)

1.27.0
======

New features
------------

* feat: add additional ci-locks to hopic (#214)
* feat: hide credential information during command printing (#253)
* improvement: allow for providing phase and variant as short options (#252)
* improvement: add support for multiple executors on a single node (#251)
* feat: report build status in same way as bitbucket Jenkins plugin (#257)
* feat(config): add the 'environment' keyword for easier overriding of env vars (#256)

Improvements
------------

* improvement(template): type check Sequence template parameters (#255)
* fix(groovy): always generate merge commits in the UTC timezone (#260)
* improvement: use GIT_SEQUENCE_EDITOR to override only the 'git rebase -i' editor (#262)
* improvement(config): reject attempts to use conflicting 'node-label' values (#259)

Documentation improvements
--------------------------

* test(doc): examples used in the documentation are syntactically valid (#263)

Documentation fixes
-------------------

* docs: don't swap the phase and variant names (#263)
* docs(with-credentials): fix typo in credential type (#263)

1.26.0
======

New features
------------

* feat: add publishable-version to hopic (#229)
* feat: add post-submit block that gets executed just after submission (#230)
* feat: perform type and existance checking of template parameters (#249)

Improvements
------------

* improvement: log error when root config object is not a map (#245)
* improvement(getinfo): only expose first value of permitted fields (#246)

Bug fixes
---------

* fix: determine git's commit hash even when not creating a tag (#248)
* fix: expose credentials in local environment as well (#250)

Documentation fixes
-------------------

* docs: update instructions to enable interactive support post install on macos (#247)

1.25.0
======

New features
------------

* feat: support url encoding in username/password credentials (#235)
* feat: on macosx pack the username and password into the password field (#234)
* feat: add support for using Jenkins' SSH key credentials (#241)

Bug fixes
---------

* fix: only determine Hopic's commit hash once (#238)
* fix: pin 'keyring' on a version that we can actually work with (#242)
* fix: handle signals while stopping Docker containers (#236)

Documentation improvements
--------------------------

* docs: include contribution guidelines in the produced documentation (#243)

1.24.0
======

New features
------------

* feat: make credential used during Bitbucket operations configurable

Improvements
------------

* improvement: remove error logs during template loading
* improvement: use longer timestamp in local version dirty field
* docs: add CONTRIBUTING.md

Bug fixes
---------

* fix: remove script approval requirement for reporting build status
* fix: return result of echo_cmd when click context is used
* fix: remove Jenkins script approval requirement for stash
* fix: prevent splitting footers with empty lines

1.23.0
======

New features
------------

* feat: add dry-run option to build command
* feat: add version option to hopic
* feat: support yaml strings from templates

Bug fixes
---------

* fix: ignore YAML errors while reading optional config file

1.22.0
======

New features
------------

* feat: install extensions more thoroughly and log their versions

Bug fixes
---------

* fix: update __main__.py with previously moved cli entrypoint
* fix: mark our produced package as zip-safe to increase installation speed
* fix: give notes the same commit/author times as the commits they're annotating
* fix: don't create a git note for existing commits
* fix: use exec flag for tmpfs docker parameter

1.21.2
======

Bug fixes
---------

* fix: handle /dev/null config file

1.21.1
======

Documentation fixes
-------------------

* docs: fix reference in 'usage' page

1.21.0
======

New features
------------

* feat(groovy): notify BitBucket about our build status

1.20.1
======

Bug fixes
---------

* fix: convert with-extra-index into a list of itself, not its container

1.20.0
======

New features
------------

* feat: add support for installation of packages with pip before building

1.19.2
======

Bug fixes
---------

* hopic.cli sub package too

1.19.1
======

Bug fixes
---------

* fix: don't use typing.Final because it depends on Python 3.8+

1.19.0
======

New features
------------

* feat: support using /dev/null as config file to indicate using defaults only

1.18.0
======

New features
------------

* feat: enable bumping on past commits instead of just the current PR's commits

1.17.0
======

New features
------------

* feat: allow restricting steps to run only for new versions

1.16.3
======

Bug fixes
---------

* fix: split off the branch name from the end of the URL only

1.16.2
======

Bug fixes
---------

* fix: handle different credential variable names for same credential ids

1.16.1
======

Bug fixes
---------

* fix: don't refer to undefined variables in error messages
* fix: use operator '=' instead of operator '==' where assignment is required

1.16.0
======

Empty release

1.15.0
======

New features
------------

* feat: support command argument lists instead of space-splitted strings

1.14.3
======

Bug fixes
---------

* fix(credentials): don't import unused 'secretstorage'

Documentation fixes
-------------------

* docs: use correct syntax for specifying 'extra' requirements to install

1.14.2
======

Bug fixes
---------

* fix: don't attempt to add deleted files to the git index

1.14.1
======

Bug fixes
---------

* fix: enable deep construction while deserializing non-scalar yaml values

1.14.0
======

New features
------------

* feat: attempt to obtain credentials from the user's keyring

Improvements
------------

* improvement: upgrade to GitPython 3.y.z as we don't need Python 2 support

1.13.4
======

Improvements
------------

* improvement: mock a username for the current uid inside docker with nss-wrapper

Bug fixes
---------

* fix: pass on committer metadata to sub worktree

1.13.3
======

Bug fixes
---------

* fix: don't crash for initialized but empty repositories

1.13.2
======

Empty release

1.13.1
======

Bug fixes
---------

* fix: use author's display name instead of user name for git author

1.13.0
======

New features
------------


* feat: enable overriding the default volumes with 'null' to disable them

1.12.0
======

New features
------------

* feat: add PURE_VERSION config and env variables

1.11.3
======

Bug fixes
---------

* fix: make hopic compatible with NK2 CI

1.11.2
======

Bug fixes
---------

* fix: don't assume branch name is available

Documentation fixes
-------------------

* docs: fix indentation in Sphinx config file

1.11.1
======

Bug fixes
---------

* fix: don't assume GIT_COMMITTER_XXX to be set, ensure it

1.11.0
======

New features
------------

* feat: allow executing multiple phases/variants instead of just a single one

Improvements
------------

* improvement: raise a readable error when phases/variants have the wrong type

1.10.1
======

Improvements
------------

* improvement: prevent attempts to define multiple phases with the same name

Bug fixes
---------

* fix: reset the WORKSPACE variable based on the use of an image before every step

1.10.0
======

New features
------------

* feat: check copyright end date against last year of modification of each file

1.9.0
======

New features
------------

* feat: add template support for YAML snippets

Improvements
------------
* improvement: log when all merge criteria are met
* improvement: log failure of sub commands instead of exiting with a traceback
* docs: describe Hopic variables

1.8.0
======

New features
------------

* feat: add !embed support in configuration

Improvements
------------

* improvement: show a warning when failing to parse the version part of a git tag

1.7.2
======

Bug fixes
---------

* fix: remove workspace before cloning to it

1.7.1
======

Bug fixes
---------

* fix: avoid crash when passing empty variant

1.7.0
======

New features
------------

* feat: allow prepare-source-tree to be used without checkout-source-tree

1.6.0
======

New features
------------

* feat: allow specifying the parents for commits produced by modality changes

1.5.2
======

Bug fixes
---------

* fix: prevent build reincarnation due to internal Jenkins exception

1.5.1
======

Improvements
------------

* improvement: switch to 'slim' image for Python instead of 'alpine'

Bug fixes
---------

* fix: ensure that we always pass the --workspace and --config arguments to Hopic

1.5.0
======

New features
------------

* feat: make Hopic command available as param to on_build_node/with_hopic closures

1.4.0
======

New features
------------

* feat: add configuration to upload artifacts on failed builds

1.3.0
======

New features
------------

* feat: allow docker-in-docker access

1.2.2
======

Bug fixes
---------

* fix: always get the same last Hopic version on every build node
* revert: add configuration to upload artifacts on failed builds

1.2.1
======

Bug fixes
---------

* fix: always use most recent change request information

1.2.0
======

New features
------------

* feat: add configuration to upload artifacts on failed builds

1.1.0
======

New features
------------

* feat: stop the running Docker container when receiving SIGINT or SIGTERM

Improvements
------------

* refactor: use commisery's commit message parsing

1.0.0
======

Improvements
------------

* ci: run tests with Python 3.7 too

Cleanup
-------

* refactor!: rename 'ci-driver' to 'hopic'
* chore!: get rid of old cfg.yml as default config file name
* chore!: switch over to Python 3.6.5 (PIPE-251)
* chore(cli)!: delete unused 'phases' and 'variants' sub commands

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
