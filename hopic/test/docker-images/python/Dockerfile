# Copyright (c) 2020 - 2020 TomTom N.V. (https://tomtom.com)
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

ARG PYTHON_VERSION
FROM python:${PYTHON_VERSION}-slim

ARG DOCKERVERSION=18.06.3-ce

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        git \
        libnss-wrapper \
        openssh-client \
 && rm -rf /var/lib/apt/lists/* \
 && curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKERVERSION}.tgz | tar -C /usr/local/bin --strip-components=1 -xzv docker/docker \
 && echo '82c7ae1ce6e314b697ec8a59074cf56b81b256b4a5f8c2f8614b57dd1709169c */usr/local/bin/docker' | sha256sum -c

RUN pip install --no-cache-dir 'tox>=3.19,<4'

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python"]

ADD ssh.sh /usr/local/bin/ssh
ADD entrypoint.sh /usr/local/bin/
