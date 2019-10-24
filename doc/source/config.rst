Configuration
=============

The Hopic build configuration is stored in a file in your repository.
The default location where it will look for this is ``${repo}/hopic-ci-config.yaml``.

Build Phases
------------

.. option:: phases

Hopic's build flow is divided in ``phases``, during which a set of commands can be executed for different ``variants``.
The :option:`phases` option is a dictionary of dictionaries.
It's top-level key specifies the name of each phase.
The keys within each phase specify the names of variants to be executed within that phase.

Phases are executed in the order in which they appear in the configuration.
Within a phase each variant may be executed in parallel, possibly on different executors.
Every next phase only starts executing when each variant within the previous phase finished successfully.
I.e. the execution flow "forks" to each variant at the start of a phase and "joins" at the end.

A variant, identified by its name, may appear in multiple phases.
Variants appearing in multiple phases are guaranteed to run on the same executor within each phase.
This provides a stable environment (workspace) to work in and allows incremental steps, such as building in phase A and running built tests in phase B.

For example, the configuration example listed results in an execution flow as shown after that.

.. literalinclude:: ../../examples/parallel-phases.yaml
   :language: yaml

.. figure:: parallel-phases.svg

    Example execution flow of a Hopic build configuration.

Credentials
-----------

.. option:: with-credentials

Sometimes it's necessary to execute commands with privileged access.
For that purpose the :option:`with-credentials` configuration option can be used for a variant within a phase.
You need to specify an identifier (``id``), used for looking up the credential and its type (``type``).
In addition to that you can specify the name of the config and environment variable that should be set to contain them.

The supported types of credential are:

Username/password credential
   * ``type``: ``username-password``
   * ``username-variable`` default: ``USERNAME``
   * ``password-variable`` default: ``PASSWORD``

File credential
   * ``type``: ``file``
   * ``filename-variable`` default: ``SECRET_FILE``

String credential
   * ``type``: ``string``
   * ``string-variable`` default: ``SECRET``

.. literalinclude:: ../../examples/with-credentials.yaml
   :language: yaml

Container Image
---------------

.. option:: image

In order to execute commands within a Docker container Hopic needs to be told what image to use for creating a container.
This option can either contain a string in which case every variant will execute in a container constructed from that image.
Alternatively it can contain a mapping where the keys and values are the names of the variant and the image to execute those in respectively.
If using the mapping form, the ``default`` key will be used for variants that don't have an image specified explicitly.

An example of the mapping style where two different variants are executed in containers based on different images:

.. literalinclude:: ../../examples/image-mapping.yaml
    :language: yaml

For the purpose of using an image (name and version) specified in an Ivy dependency manifest file the `!image-from-ivy-manifest` type constructor exists.
When used its contents are a mapping with these keys:

``manifest``
    Path to the Ivy manifest file.
    This defaults to the first of these to exist:

    * ``${WORKSPACE}/dependency_manifest.xml``
    * ``${CFGDIR}/dependency_manifest.xml``

``repository``
    Docker repository to fetch the image from.

``path``
    Directory within the repository to fetch from.

``name``
    Name of the image to fetch.
    This defaults to the content of the ``name`` attribute in the Ivy manifest.

``rev``
    Version of the image to fetch.
    This defaults to the content of the ``rev`` attribute in the ivy manifest.

When used this will get treated as if the expansion of ``{repository}/{path}/{name}:{rev}`` was specified as a string value of this field.
This allows using Ivy as a mechanism for automatically keeping the Docker image up to date.

For example, when using this dependency manifest in ``${WORKSPACE}/dependency_manifest.xml``:

.. code-block:: xml

   <ivy-module version="2.0">
     <info module="p1cms" organisation="com.tomtom" revision="dont-care" />
     <dependencies>
       <dependency name="python" org="com.tomtom.toolchains" rev="3.6.5" revConstraint="[3.5,4.0[">
         <!-- identify this as the dependency specifying the Docker image for Hopic -->
         <conf mapped="toolchain" name="default" />
       </dependency>
     </dependencies>
   </ivy-module>

And this Hopic config file:

.. literalinclude:: ../../examples/image-ivy-manifest.yaml
    :language: yaml

The result will be to use the ``hub.docker.com/tomtom/python:3.6.5`` image by default.
The ``PyPy`` build will instead use the ``hub.docker.com/tomtom/pypy:3.6.5`` image.
I.e. for that build the image name is overridden from that used in the Ivy manifest, while still using the version from it.

Volumes
-------

.. option:: volumes

In order to execute commands within Docker it is often required to mount directories or a file to the docker container.
This can be done by specifying :option:`volumes`. 
:option:`volumes` doesn't have any effect when there is no :option:`image` specified.

There are two formats how a volume can be specified:

**Format 1**

The volume can be specified as ``host-src``\[:``container-dest``][:``<options>``].
The ``options`` are [rw|ro]
The ``host-src`` is an absolute path or a name value.

**Format 2**

The volume can be specified using a dictionary with the following keys:
    - ``source``
    - [``target``]
    - [``read-only``]

Where ``source`` is equal to ``host-src``, ``target`` is equal to ``container-dest`` and ``read-only`` reflects the possible ``options`` with a boolean value.

By default the ``host-src`` is mounted rw.

When the given ``host-src`` doesn't exist it will be created as a directory.
If ``container-dest`` is not specified, it will take the same value as ``host-src``.
For the ``host-src`` path, ``$HOME`` or ``~`` will be expanded to the home directory of the current user.
While for the ``container-dest``, ``$HOME`` or ``~`` will be expanded to ``/home/sandbox``.

The following directories are mounted by default:

================== =============== ===============
host-src           container-dest  <options>
================== =============== ===============
/etc/passwd        /etc/passwd     read-only
/etc/group         /etc/group      read-only
``WORKSPACE`` [*]_ /code           read-write
================== =============== ===============

.. [*] ``WORKSPACE/code`` for repositories referring to other repositories for their code.

:option:`volumes` can be declared in every scope and will be used during the specified scopes
e.g. :option:`volumes` specified in global scope are used with every command. 
In case an inherited bind mount needs to be overridden, that can be accomplished by adding a volume with the same ``target_location``.
Consider the following example where `/tmp/downloads` is overridden:

**example:**

.. literalinclude:: ../../examples/volumes-override.yaml
   :language: yaml

Environment Variables
---------------------

.. option:: pass-through-environment-vars

This option allows passing environment variables of the host environment through into containers.
This is a list of strings.
Each string is the name of an environment variable.
If the named environment variable exists in the host environment, it will be set to the same value inside the container.

.. literalinclude:: ../../examples/pass-through-env-vars.yaml
   :language: yaml

Mounting Volumes From Other Containers
--------------------------------------

.. option:: volumes-from

The option ``volumes-from`` allows you to mount volumes that are defined in an external *Docker image*.
The behavior translates directly to a ``--volumes-from`` Docker-run option; the volumes are mapped to the path as originally specified in the external image.

Note that this option does nothing if you haven't specified a Docker image (see the :option:`image` option).

The option requires two keys to be specified:

``image-name``
    The full name of the Docker image.

``image-version``
    The targeted version of the Docker image.

The combination of ``<image-name>:<image-version>`` should result in a correct, downloadable Docker image.

**example:**

.. literalinclude:: ../../examples/with-volumes.yaml
    :language: yaml

Publish From Branch
-------------------

.. option:: publish-from-branch

The ``publish-from-branch`` option, when provided, specifies a regular expression matching the names of branches from which to allow publication.
Publication includes version bumping (see :option:`version`) and the execution of any steps marked with :option:`run-on-change` as ``only``.

If this option is omitted, Hopic allows publication from any branch.

The example below configures Hopic to only publish from the ``master`` branch or any branch starting with ``release/`` or ``rel-``.

**example:**

.. code-block:: yaml

  publish-from-branch: '^master$|^release/.*|^rel-.*'


Versioning
----------

.. option:: version

Hopic provides some support for determining and bumping of the currently checked out version.

It currently supports the syntax, sorting and bumping strategies of these versioning policies.
The policy to use can be specified in the ``format`` option of the ``version`` option section.

``semver``
   `Semantic Versioning`_. This is the default when no policy is explicitly specified.

   The default tag format is ``{version.major}.{version.minor}.{version.patch}``.

   The default component to bump is the pre-release label.

``carver``
   Caruso variation on Semantic Version for branching

   The default tag format is ``{version.major}{version.minor}{version.patch}+PI{version.increment}.{version.fix}``.

   The default component to bump is the pre-release label.

The version can be read from and stored in two locations:

``file``
    When this option is specified Hopic will always use this as the primary source for reading and storing the version.
    The first line to contain only a syntactically valid version, optionally prefixed with ``version=``, is assumed to be the version.
    When reading the version it'll use this verbatim.
    When storing a (likely bumped) version it'll only modify the version portion of that file.

``tag``
    When this option is set to ``true`` or a non-empty string Hopic will, when storing, create a tag every time it creates a new version.
    When this option is set to a string it will be interpreted according to `Python Format Specification`_ with the named variable ``version`` containing the version.
    When this option is set and ``file`` is not set it will use `git describe <https://git-scm.com/docs/git-describe>`_ to read the current version from tags.
    When used for reading, it will mark commits that don't have a tag a virtual prerelease of the predicted next version.

    Setting this option to a string can, for example, be used to add a prefix like ``v`` to tags, e.g. by using ``v{version}``.
    Having it set to ``true`` instead uses the version policy's default formatting.

Whether and what to bump can be controlled by the ``bump`` option.
When set to ``false`` it disables automated bumping completely.
When not specified it defaults to bumping the default-to-bump part of the used version policy.
When set to a string it bumps the named component of the version.

When bumping is enabled, Hopic bumps each time that it applies a change.
Usually this means when it's merging a pull request.
Another option is when it's performing a modality change (currently only ``UPDATE_DEPENDENCY_MANIFEST``).

.. todo:: Describe ``after-submit``. Maybe?

.. _Semantic Versioning: https://semver.org/
.. _Python Format Specification: https://docs.python.org/3/library/string.html#formatspec

Modality Changes
----------------

.. option:: modality-source-preparation

The ``modality-source-preparation`` option allows for influencing the build according to the ``MODALITY`` parameter.
If Hopic is called with a ``MODALITY`` that is present in the configuration file, then the commands as specified in that section are executed before the other phases.

See the description of the ``apply-modality-change`` parameter on the `Usage` page for the calling syntax.

Note that this is, above all, a remnant of the previous generation pipeline; it is currently only used to perform ``UPDATE_DEPENDENCY_MANIFEST`` builds.

.. note:: Defining new functionality using this option is discouraged.

``description``
    An optional description for the command, which will be printed in the logs.

``sh``
    The actual command to be run. Variables will be expanded, similar to commands defined in the :option:`phases`.

``changed-files``
    Specifies the files that are changed by the command, which are to be added to the commit.

    If omitted, Hopic forces a clean repository before running the command specified by ``sh``.
    Upon completion of the command, all files that are changed, removed and/or previously untracked are added to the commit.

``commit-message``
    The message that will be used to commit the changes when this modality is run.

    If omitted, the value of the ``MODALITY`` parameter is used as the commit message.

**example:**

.. code-block:: yaml

  modality-source-preparation:
    UPDATE_DEPENDENCY_MANIFEST:
      - sh: update_dependency_manifest.py ${CFGDIR}/dependency_manifest.xml ${CFGDIR}/ivysettings.xml
        changed-files:
          - ${CFGDIR}/dependency_manifest.xml
        commit-message: Update of dependency manifest


Restricting Variants to Specific Build Nodes
--------------------------------------------

.. option:: node-label

.. todo::

    Document :option:`node-label` option.

Restricting Steps to Changes or Not
-----------------------------------

.. option:: run-on-change

.. todo::

    Document :option:`run-on-change` option.

Sharing Output Data Between Variants
------------------------------------

.. option:: stash

.. todo::

    Document :option:`stash` option.

Customizing Step Description
----------------------------

.. option:: description

.. todo::

    Document :option:`description` option.

Branches in Subdirectory Worktrees
----------------------------------

.. option:: worktrees

.. todo::

    Document :option:`worktrees` option.

Repeating Steps for Commits
---------------------------

.. option:: foreach

.. todo::

    Document :option:`foreach` option.

Change Request Commits
^^^^^^^^^^^^^^^^^^^^^^

``SOURCE_COMMIT``

Change Request Autosquashed Commits
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

``AUTOSQUASHED_COMMIT``

Sub SCM
-------

.. option:: scm

.. todo::

    Document :option:`scm` option.

JUnit Test Results
------------------

.. option:: junit

.. todo::

    Document :option:`junit` option.

Artifact Archiving
------------------

.. option:: archive

The option ``archive`` allows you to archive build artifacts.
The artifacts can be stored on Jenkins and/or archived to Artifactory.

The base directory is the workspace.
Artifacts specified are discovered relative to the workspace.

Use Wildcards like `module/dist/**/*.zip`.
A `*` expands only to a single directory entry, where `**` expands to multiple directory levels deep.

Use the ``pattern`` option to identify and upload a specific artifact.
The specific artifact can then be uploaded to artifactory with the option ``target``.

**example:**

.. literalinclude:: ../../examples/archive.yaml
    :language: yaml

Archiving To Artifactory
^^^^^^^^^^^^^^^^^^^^^^^^

.. option:: upload-artifactory

.. todo::

    Document :option:`upload-artifactory` option.

Promoting Builds in Artifactory
"""""""""""""""""""""""""""""""

.. option:: artifactory

.. todo::

    Document :option:`artifactory` option.

Artifact Fingerprint
--------------------

.. option:: fingerprint

.. todo::

    Document :option:`fingerprint` option.
