.. You can view the documentation for CI-Driver at URL  : /pages/PIPE/hopic/pages/browse/

In order to simplify the CI configuration we are switching away from the **generic jenkins shared pipeline** which is completely written in Groovy.
Instead we are switching to the Hopic project which only has a minimal **CI driver** component written in Groovy with the rest written in Python.
With Hopic CI Driver local debugging is made significantly easier.

As of now commit stage can be adapted to use CI-Driver functionality.
With this most of the required configuration will live in the hopic-ci-config.yaml file replacing the groovy snippets from the commit stage in jenkins file.
This can be locally tested with the command - **"ci-driver"**


Test CI Driver locally
----------------------

**Install the ci-driver** command with below package

.. code-block:: console

   pip install --user git+https://github.com/tomtom-international/hopic.git@release/0

.. _BashComplete: https://click.palletsprojects.com/en/7.x/bashcomplete/#activation

**Enable TAB completion** - include the below line in your .bashrc ( BashComplete_ )

.. code-block:: console

   eval "$(_CI_DRIVER_COMPLETE=source ci-driver)"

For the command line help on ci-driver usage

.. code-block:: console

   ci-driver --help
