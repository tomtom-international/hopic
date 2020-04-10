ARG PYTHON_VERSION
FROM python:${PYTHON_VERSION}-slim

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends docker.io git openssh-client \
 && rm -rf /var/lib/apt/lists/*
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*
