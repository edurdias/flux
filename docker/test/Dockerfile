# Minimal image for the docker/airgapped runner integration tests
# (tests/flux/test_docker_runner.py, tests/flux/test_airgapped_runner.py).
#
# Unlike the top-level Dockerfile, which installs flux-core from PyPI, this
# installs the wheel built from the working tree — the point of the tests is
# to exercise the *current* checkout's child protocol inside a container.
#
# The build context is this directory (the repository .dockerignore excludes
# dist/, so the wheel is copied here first). Build from the repository root:
#     poetry build -f wheel
#     cp dist/*.whl docker/test/
#     docker build -t flux-test:local docker/test
#     FLUX_TEST_DOCKER_IMAGE=flux-test:local poetry run pytest \
#         tests/flux/test_docker_runner.py tests/flux/test_airgapped_runner.py
# or simply: make test-docker
ARG PYTHON_IMAGE_VERSION=3.12-slim
FROM python:${PYTHON_IMAGE_VERSION}

COPY *.whl /tmp/dist/
RUN pip install --no-cache-dir /tmp/dist/*.whl && rm -rf /tmp/dist

# The airgapped runner runs containers read-only as a non-root user; a
# non-root default here keeps the plain docker runner tests honest too.
RUN useradd --create-home --uid 1000 flux
USER flux
WORKDIR /home/flux
