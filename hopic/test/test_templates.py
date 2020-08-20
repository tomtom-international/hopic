# Copyright (c) 2019 - 2020 TomTom N.V. (https://tomtom.com)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from .test_show_config import run_with_config

from collections import OrderedDict
import json


def test_commisery_template(capfd):
    result = run_with_config('''\
phases:
  style:
    commit-messages: !template "commisery"
''', ('show-config',))

    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)
    expanded = output['phases']['style']['commit-messages']
    assert expanded[0]['image'] is None
    commits, head = [e['sh'] for e in expanded]
    assert 'commisery.checking' in commits
    assert head[-2:] == ['commisery.checking', 'HEAD']
