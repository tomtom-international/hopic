# Copyright (c) 2018 - 2020 TomTom N.V.
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

import logging
import os
import re
import subprocess
import sys

from .execution import echo_cmd_click as echo_cmd


log = logging.getLogger(__name__)


class FatalSignal(Exception):
    def __init__(self, signum):
        self.signal = signum


class DockerContainers(object):
    """
    This context manager class manages a set of Docker containers, handling their creation and deletion.
    """
    def __init__(self):
        self.containers = set()

    def __enter__(self):
        return self

    def __exit__(self, ex_type, ex_value, tb):
        if self.containers:
            log.info('Cleaning up Docker containers: %s', ' '.join(self.containers))
            try:
                echo_cmd(subprocess.check_call, ['docker', 'rm', '-v'] + list(self.containers))
            except subprocess.CalledProcessError as e:
                log.error('Could not remove all Docker volumes, command failed with exit code %d', e.returncode)
            self.containers.clear()

    def __iter__(self):
        return iter(self.containers)

    def add(self, volume_image):
        log.info('Creating new Docker container for image %s', volume_image)
        try:
            container_id = echo_cmd(subprocess.check_output, ['docker', 'create', volume_image]).strip()
        except subprocess.CalledProcessError as e:
            log.exception('Command fatally terminated with exit code %d', e.returncode)
            sys.exit(e.returncode)

        # Container ID's consist of 64 hex characters
        if not re.match('^[0-9a-fA-F]{64}$', container_id):
            log.error('Unable to create Docker container for %s', volume_image)
            sys.exit(1)

        self.containers.add(container_id)


def volume_spec_to_docker_param(volume):
    if not os.path.exists(volume['source']):
        os.makedirs(volume['source'])
    param = '{source}:{target}'.format(**volume)
    try:
        param = param + ':' + ('ro' if volume['read-only'] else 'rw')
    except KeyError:
        pass
    return param
