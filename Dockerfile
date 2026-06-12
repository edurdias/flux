# Pinned to the 3.12 minor version. For reproducible production builds, pin to a
# digest instead, e.g. PYTHON_IMAGE_VERSION=3.12-slim@sha256:<digest>
# (resolve with: docker buildx imagetools inspect python:3.12-slim)
ARG PYTHON_IMAGE_VERSION=3.12-slim
ARG FLUX_VERSION=latest
# Extra packages to install (e.g. "flux-core[observability]" for OpenTelemetry support)
ARG EXTRA_PACKAGES=""

FROM python:${PYTHON_IMAGE_VERSION} AS runtime

ARG FLUX_VERSION
ARG EXTRA_PACKAGES

WORKDIR /app

# Install setuptools to provide distutils for Python 3.12+ compatibility
RUN pip install --no-cache-dir setuptools

# Install flux-core from PyPI
RUN if [ "$FLUX_VERSION" = "latest" ]; then \
        pip install --no-cache-dir flux-core; \
    else \
        pip install --no-cache-dir flux-core==${FLUX_VERSION}; \
    fi

# Install any extra packages if specified
RUN if [ -n "${EXTRA_PACKAGES}" ]; then \
        pip install --no-cache-dir ${EXTRA_PACKAGES}; \
    fi

# Create non-root user
RUN useradd --create-home --uid 1000 flux

# Copy configuration file
COPY flux.toml ./

# Create flux directories (home, cache and local storage are relative to /app)
# and hand the working directory to the non-root user
RUN mkdir -p .flux/.workflows .flux/.cache .flux/.storage && \
    touch .flux/.workflows/__init__.py && \
    chown -R flux:flux /app

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

# Create entrypoint script
RUN echo '#!/bin/sh\n\
if [ "$FLUX_MODE" = "worker" ]; then\n\
    if [ -n "$FLUX_WORKER_NAME" ]; then\n\
        WORKER_CMD="flux start worker $FLUX_WORKER_NAME"\n\
    else\n\
        WORKER_CMD="flux start worker"\n\
    fi\n\
    if [ -n "$FLUX_SERVER_URL" ]; then\n\
        WORKER_CMD="$WORKER_CMD --server-url $FLUX_SERVER_URL"\n\
    fi\n\
    exec $WORKER_CMD\n\
elif [ "$FLUX_MODE" = "mcp" ]; then\n\
    MCP_CMD="flux start mcp --host $FLUX_MCP_HOST --port $FLUX_MCP_PORT --transport $FLUX_MCP_TRANSPORT"\n\
    if [ -n "$FLUX_MCP_NAME" ]; then\n\
        MCP_CMD="$MCP_CMD --name $FLUX_MCP_NAME"\n\
    fi\n\
    if [ -n "$FLUX_SERVER_URL" ]; then\n\
        MCP_CMD="$MCP_CMD --server-url $FLUX_SERVER_URL"\n\
    fi\n\
    exec $MCP_CMD\n\
else\n\
    exec flux start server --host $FLUX_HOST --port $FLUX_PORT\n\
fi' > /entrypoint.sh && chmod +x /entrypoint.sh

# Create healthcheck script (mode-aware: only the server exposes HTTP).
# Worker and MCP modes run flux as PID 1, so the container exiting already
# signals process death; report healthy while the container is running.
RUN echo '#!/bin/sh\n\
if [ "$FLUX_MODE" != "worker" ] && [ "$FLUX_MODE" != "mcp" ]; then\n\
    URL="http://127.0.0.1:${FLUX_PORT:-8000}/health"\n\
    exec python -c "import sys, urllib.request; urllib.request.urlopen(sys.argv[1], timeout=5)" "$URL"\n\
fi\n\
exit 0' > /healthcheck.sh && chmod +x /healthcheck.sh

USER flux

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD ["/healthcheck.sh"]

ENTRYPOINT ["/entrypoint.sh"]
