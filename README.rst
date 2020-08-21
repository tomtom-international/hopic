.. You can view the documentation for Hopic at URL  : /pages/PIPE/hopic/pages/browse/

In order to simplify the CI configuration we are switching away from the **generic jenkins shared pipeline** which is completely written in Groovy.
Instead we are switching to the Hopic project which only has a minimal **CI driver** component written in Groovy with the rest written in Python.
With Hopic local debugging is made significantly easier.

As of now the commit stage can be adapted to use Hopic's functionality.
With this most of the required configuration will live in the hopic-ci-config.yaml file replacing the groovy snippets from the commit stage in jenkins file.
This can be locally tested with the command - **"hopic"**


Using Hopic locally
-------------------

**Install the hopic** command with below package

.. code-block:: console

   pip3 install --user 'git+https://github.com/tomtom-international/hopic.git@release/1#egg=hopic[interactive]'

.. _BashComplete: https://click.palletsprojects.com/en/7.x/bashcomplete/#activation

**Enable TAB completion** - include the below line in your .bashrc ( BashComplete_ )

.. code-block:: console

   eval "$(_HOPIC_COMPLETE=source hopic)"

For the command line help on hopic usage

.. code-block:: console

   hopic --help
