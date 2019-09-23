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
