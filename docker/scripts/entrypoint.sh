#!/bin/sh
# Entrypoint for the Flux image.
#
# An explicit command always wins — this is what makes one image serve every
# role: the docker runner starts the execution child with
#   docker run <image> python -m flux.runners.child
# and any flux CLI invocation works ad hoc:
#   docker run <image> flux workflow list
#   docker run <image> flux agent create ...
#
# With no command, FLUX_MODE selects a long-running role:
#   server (default) | worker | mcp
set -e

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

case "$FLUX_MODE" in
worker)
    if [ -n "$FLUX_WORKER_NAME" ]; then
        WORKER_CMD="flux start worker $FLUX_WORKER_NAME"
    else
        WORKER_CMD="flux start worker"
    fi
    if [ -n "$FLUX_SERVER_URL" ]; then
        WORKER_CMD="$WORKER_CMD --server-url $FLUX_SERVER_URL"
    fi
    exec $WORKER_CMD
    ;;
mcp)
    MCP_CMD="flux start mcp --host $FLUX_MCP_HOST --port $FLUX_MCP_PORT --transport $FLUX_MCP_TRANSPORT"
    if [ -n "$FLUX_MCP_NAME" ]; then
        MCP_CMD="$MCP_CMD --name $FLUX_MCP_NAME"
    fi
    if [ -n "$FLUX_SERVER_URL" ]; then
        MCP_CMD="$MCP_CMD --server-url $FLUX_SERVER_URL"
    fi
    exec $MCP_CMD
    ;;
*)
    exec flux start server --host "$FLUX_HOST" --port "$FLUX_PORT"
    ;;
esac
