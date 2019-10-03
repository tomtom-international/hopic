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

