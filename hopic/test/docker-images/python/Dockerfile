ARG PYTHON_VERSION
FROM python:${PYTHON_VERSION}-alpine

RUN apk add --no-cache docker-cli git openssh-client
RUN apk add --no-cache --virtual .build-deps gcc musl-dev
