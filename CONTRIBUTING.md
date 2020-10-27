# Contributing
First off, thank you for taking the time to contribute!
The following information should help you make this process as smooth as possible.

## Design philosophy
Before making a contribution to this repository, it's good to know the philosophy upon which Hopic was written.

### 1. The importance of local development
Hopic was conceived mainly due to the previous CI system and its syntax being terribly undescriptive and difficult to debug.
This led to one of Hopic's main requirements: a **local developer should be able to perform the same build steps as the CI system**.
The user needs to know whether his changes will build _before_ submitting it.

Aside from that, *when* the CI system runs into an issue. The logs must contain useful information that can be used to replicate the issue locally, making debugging the issue less painful.

### 2. Clear and readable configuration format
We want to keep Hopic's configuration format as dumb as we can get away with, but not any dumber than that.
When a human examines a Hopic file, the build steps that Hopic performs should be clear, unambiguous and unaffected by external factors.

Therefore, **keeping complexity low** is always a key consideration in changes to Hopic's configuration format. Well, that, and **maintaining backwards compatibility**, of course.

### 3. Testability
Hopic is intended to be used by CI systems by way of a branch reference (that is, they directly install `release/N`).
Active development happens on the release branch directly (except for breaking changes, which are staged in `master`). This means that any changes that are merged to those branches are picked up immediately by dozens of users.
Therefore, **testing is vitally important** in Hopic; any new feature or bug-fix should be covered by one or more tests.

## Setting up your environment
### Prerequisites
In order to start development on Hopic, you'll want to make sure the following packages are installed:
- `python3-pip`, `python3-setuptools` and `git` for core functionality
- `docker.io` for executing Hopic's own build
- `tox` for local testing

If they aren't, install them through your platform's package manager, e.g.:
```
apt install --no-install-recommends python3-pip python3-setuptools git docker.io
pip3 install tox
```

### Installation
For development, we recommend installing Hopic as an editable package, through either `pip`:
```
pip3 install --user -e .
```
or `setuptools`:
```
python3 setup.py develop
```
This will install Hopic's dependencies and configure the `hopic` Python 3 package to use a "live" version; any changes you make to the code will be immediately reflected in your environment's `hopic` executable.

## Making changes
Hopic consists of two parts:
- a Python module, containing the CLI, merging and execution logic, etc.
- a Groovy file, which contains some glue code to specific CI system components (Jenkins and Bitbucket)

As mentioned before, avoid any additions to the Groovy file as much as possible. Instead, create generic, CI-system-independent solutions.

### Python
The Python code can be found in `/hopic`.
The test set lives in `/hopic/test`.

The [PyTest framework](https://docs.pytest.org/en/stable/index.html) is used as a testing framework; API reference can be found [here](https://docs.pytest.org/en/stable/reference.html).
The [Click library](https://click.palletsprojects.com/en/7.x/) is used for command line handling; API reference can be found [here](https://click.palletsprojects.com/en/7.x/#api-reference).
The [GitPython library](https://gitpython.readthedocs.io/en/stable/intro.html) is used to facilitate Git repository access; API reference can be found [here](https://gitpython.readthedocs.io/en/stable/reference.html).

Make sure that any bug fixes or feature additions are properly backed by the appropriate tests.

### Groovy
The Groovy code can be found in `/vars/getCiDriver.groovy`.
In order to keep Hopic generic, testable and CI-system-independent, avoid writing Groovy code where you can.
Since the Groovy code is specific to Jenkins (and cannot be tested), it should contain as little logic as we can get away with.

In other words: keep your feature/bugfix generic, and only add Groovy if you need an interface to Jenkins (or any information Jenkins provides that cannot be otherwise obtained).

## Python coding style
We use `flake8` to enforce PEP8 coding style.

To run it, use:
```
tox -e flake8
```
The ignore-list can be found in `tox.ini`.

## Testing your changes
To run the full test set, run `tox` with the `py3` environment:
```
tox -e py3
```

Tests are PyTest-based and are located in `hopic/test/`.
You can provide parameters to PyTest through `tox` by appending `-- [OPTIONS]`.
As an example, using PyTest's test-substring-matching option `-k` to run tests related to conventional commits:
```
tox -e py3 -- -k conventional
```

To run the tests on all supported Python versions, you can just run Hopic's `test`-phase, which will start a Docker image for all supported versions:
```
hopic build --phase test
```

### Caution advised
The Hopic repository is designed to be used "live"; your changes will be propagated immediately to a lot of projects.
Submitting changes should therefore not be done lightly and they should be covered by tests as much as possible.

While fixing bugs, create a test case first that cleanly reproduces the bug, which you can use to verify your solution and can be used in the future to prevent regression.

## Commits and commit messages
### Contents of a commit
Keep commits atomic.
A commit should consist of one "logical" change (that is, one "task" that can't or shouldn't sensibly be split up further), and a commit must not fail a build.
For example, if your new feature requires another feature that is not directly related, it can be isolated into its own commit.

### Commit message format
Commit messages are used to decide which version field to bump, so they should adhere to the [conventional commits specification](https://www.conventionalcommits.org/en/v1.0.0/).

Specific requirements in this repo:
- Commit messages follow this format:
  ```
  <tag>: commit title

  <optional commit body>

  <optional footer>
  ```
- Note the linebreak between the commit title, body and footer
- `<tag>` should be one of: `feat`, `fix`, `build`, `chore`, `ci`, `docs`, `perf`, `refactor`, `revert`, `style`, `test`, or `improvement`.
- The commit title should start with a lowercase and the total commit subject line should not exceed 80 characters.
- An optional commit body allows you to elaborate on your change and the rationale for creating it.
- The optional footer consists of "key-value"-like items and contains any metadata you want to store in the commit.
The format is inspired by RFC822 e-mail headers and git trailer conventions, e.g.: `Addresses: ISSUE-521`.

References to tickets/issues should be done using one of the following footers, whichever applies best:
```
Implements: PIPE-123
Fixes: PIPE-123
Addresses: PIPE-123
```

We enforce adherence to that aforementioned with the tool [Commisery](https://pypi.org/project/commisery/), which can be run locally with:
```
pip3 install commisery
commisery-verify-msg [ref]
```
Where `[ref]` is a reference to a commit, following the specification for `git-rev-parse(1)`.
If `[ref]` is omitted, `HEAD` is implied.

## Before creating a pull request
Make sure that:

- Commits adhere to the [commit policy](#Commits-and-commit-messages)
- Documentation was added or updated, as appropriate
- Your change is covered by [one or more tests](#Testing-your-changes)
- Tests and code style checks pass

## During a pull request
### Address review comments with fixups
Be kind to your reviewers and don't force push to your pull request unnecessarily.

To address review comments that should be squashed into an earlier changelist, make the required changes, then:
```
git add <changed-files>
# or:
# git add -u
git commit --fixup=<ref>
```
Where `<ref>` is a ref to the commit that this fixup should later be squashed into. Refer to `gitrevisions(7)` for the allowed formats (e.g. `1a2b3c4`, `:/"part of commit message"`, etc.)

### Autosquash review comments when approved
When all reviewers approve, rebase and (auto-)squash your fixups with:
```
git rebase -i --autosquash <ref-parent>~1
```
Where `<ref>` this time is the target branch or the _first parent_ of the commit that should receive a fixup, e.g. `HEAD~3`, `master`, `1a2b3c4~1`, etc.
