# Pinned to the 3.12 minor version. For reproducible production builds, pin to a
# digest instead, e.g. PYTHON_IMAGE_VERSION=3.12-slim@sha256:<digest>
# (resolve with: docker buildx imagetools inspect python:3.12-slim)
ARG PYTHON_IMAGE_VERSION=3.12-slim
ARG FLUX_VERSION=latest
# flux-core extras baked into the image (comma-separated). The published image
# ships "postgresql,observability,ai" so one image covers server, worker,
# runner-child, MCP, and agent roles.
ARG FLUX_EXTRAS=""
# Extra pip packages for workflow code (e.g. "pandas numpy")
ARG EXTRA_PACKAGES=""

FROM python:${PYTHON_IMAGE_VERSION} AS runtime

ARG FLUX_VERSION
ARG FLUX_EXTRAS
ARG EXTRA_PACKAGES

LABEL org.opencontainers.image.title="Flux" \
      org.opencontainers.image.description="Distributed workflow orchestration engine — one image for server, worker, docker-runner child, MCP, and agent roles" \
      org.opencontainers.image.source="https://github.com/edurdias/flux" \
      org.opencontainers.image.licenses="Apache-2.0"

# Security patches for the base layer, plus tini as PID 1: workflows and the
# runner child spawn subprocesses, and an init process reaps zombies and
# forwards signals (SIGTERM-based drain/cancellation) without requiring
# `docker run --init`.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# setuptools provides distutils for Python 3.12+ compatibility
RUN pip install --no-cache-dir setuptools

# Install flux-core (with extras) from PyPI
RUN PKG="flux-core"; \
    if [ -n "${FLUX_EXTRAS}" ]; then PKG="flux-core[${FLUX_EXTRAS}]"; fi; \
    if [ "${FLUX_VERSION}" = "latest" ]; then \
        pip install --no-cache-dir "${PKG}"; \
    else \
        pip install --no-cache-dir "${PKG}==${FLUX_VERSION}"; \
    fi

# Install any extra packages if specified
RUN if [ -n "${EXTRA_PACKAGES}" ]; then \
        pip install --no-cache-dir ${EXTRA_PACKAGES}; \
    fi

# Containers are ephemeral: without bytecode baked into the image, every
# `docker run` recompiles imports from source and throws the result away —
# measured to double the docker runner's per-execution latency.
RUN python -m compileall -q -j 0 "$(python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"

# Create non-root user
RUN useradd --create-home --uid 1000 flux

# Copy configuration file
COPY flux.toml ./

# Create flux directories (home, cache and local storage are relative to /app)
# and hand the working directory to the non-root user
RUN mkdir -p .flux/.workflows .flux/.cache .flux/.storage && \
    touch .flux/.workflows/__init__.py && \
    chown -R flux:flux /app

# Bytecode is precompiled above; don't litter (possibly read-only) filesystems
# with __pycache__ at runtime.
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set environment variables for configuration
ENV FLUX_MODE=server
ENV FLUX_HOST=0.0.0.0
ENV FLUX_PORT=8000
ENV FLUX_SERVER_URL=""
ENV FLUX_WORKER_NAME=""
ENV FLUX_MCP_NAME="flux-workflows"
ENV FLUX_MCP_HOST=0.0.0.0
ENV FLUX_MCP_PORT=8080
ENV FLUX_MCP_TRANSPORT="streamable-http"

COPY docker/scripts/entrypoint.sh /entrypoint.sh
COPY docker/scripts/healthcheck.sh /healthcheck.sh
RUN chmod +x /entrypoint.sh /healthcheck.sh

USER flux

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD ["/healthcheck.sh"]

ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
