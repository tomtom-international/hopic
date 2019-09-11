Configuration
=============

The Hopic build configuration is stored in a file in your repository.
The default location where it will look for this is ``${repo}/hopic-ci-config.yaml``.

Credentials
-----------

.. option:: with-credentials

Sometimes it's necessary to execute commands with privileged access.
For that purpose the :option:`with-credentials` configuration option can be used for a variant within a phase.
You need to specify an identifier (``id``), used for looking up the credential and its type (``type``).
In addition to that you can specify the name of the config and environment variable that should be set to contain them.

The support types of credential are:

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
