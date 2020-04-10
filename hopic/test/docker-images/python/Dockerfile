ARG PYTHON_VERSION
FROM python:${PYTHON_VERSION}-slim

ARG DOCKERVERSION=18.06.3-ce

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends curl git openssh-client \
 && rm -rf /var/lib/apt/lists/* \
 && curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKERVERSION}.tgz | tar -C /usr/local/bin --strip-components=1 -xzv docker/docker \
 && echo '82c7ae1ce6e314b697ec8a59074cf56b81b256b4a5f8c2f8614b57dd1709169c */usr/local/bin/docker' | sha256sum -c
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*
