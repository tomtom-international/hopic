..
   Copyright (c) 2021 - 2021 TomTom N.V. (https://tomtom.com)
   
   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at
   
       http://www.apache.org/licenses/LICENSE-2.0
   
   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.

.. _hotfix:

Hotfix Version Requirements
===========================


The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL
NOT", "SHOULD", "SHOULD NOT", "RECOMMENDED",  "MAY", and
"OPTIONAL" in this document are to be interpreted as described in
:rfc:`2119`.

Restrictions
------------

* Hotfix branches *MUST NOT* contain any breaking changes or new features
* Hotfix branches *SHOULD NOT* contain any changes other than *urgent* bug fixes
* Hotfix bug fixes *MUST* use the ``fix`` conventional commit tag
* Hotfix branches *MUST* have an identifier unique to the version they're based on

  This could be "customername" if and only if there is no more than a single hotfix branch for that customer on that version.

* Hotfix branches *SHOULD* split off directly from (tagged) releases
* Hotfix branches *MUST NOT* split off from pre-release versions

  Because hotfixes are only relevant for full releases, not pre-releases or development snapshots.

Requirements
------------

A hotfix version:

* *MUST* always compare as coming *after* the version it's based on and *before* any next released version
* *MUST* always compare as coming after any previous hotfix version, with the same identifier, it's directly based on

In order to address these requirements, for semver 1 and 2, the following format of a hotfix version is prescribed:

.. _hotfix-id:

Given a base version ``X.Y.Z`` that the hotfix is based on, a hotfix version is formatted as ``X.Y.${Z + 1}-hotfix.${ID}.${N}``.

* ``ID`` represents an alpha-numeric identifier that should be unique and is determined based on configuration.
    - ``ID`` *MUST* start with a letter (``[a-zA-Z]``) and *MUST* end with an alphanumeric character (``[a-zA-Z0-9]``) and *MAY* contain hyphens and periods (``[-.a-zA-Z0-9]``)
    - In order to prevent compatibility problems ``ID`` *MUST NOT* have any of these forms ``^(a|b|c|rc|alpha|beta|pre|preview|post|rev|r|dev)[-.]?[0-9]*$``
* ``N`` is a monotonically increasing integer
 
``N`` *MAY* be determined by the commit distance through the first parent of the hotfix change to the base version.
``N`` *MAY* alternatively be the direct successor of the hotfix produced immediately before (0 for the first, 1, 2, 3, etc.).
Consumers *SHOULD NOT* rely on the absolute value of ``N`` and *SHOULD* instead only use it to derive a total order for all versions that share the same identifier.

As a default, in absence of configuration, ``ID`` *SHOULD* be derived from the commit hash of the direct descendent of the base version.
If there are multiple such direct descendants, the one reachable through the left-most (usually first) parents *MUST* be used.
If and only if ``ID`` is constructed directly from that commit hash (derived or in full) it *MUST* be formatted as ``g${PREFIX_OF_COMMIT_HASH}``.

*If* ``ID`` is _not_ constructed from (a prefix of) the aforementioned commit hash it *MUST NOT* match the regex ``/^g[0-9a-fA-F]+$/`` to prevent ambiguity.

It *SHOULD* be possible to configure ``ID`` to be derived from the name of the hotfix branch.
This *MAY* be accomplished by a configurable regex.
If such a regex is used the implementation *SHOULD* reject duplicating of the base version in the ``ID`` field.
