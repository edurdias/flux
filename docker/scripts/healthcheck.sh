#!/bin/sh
# Mode-aware healthcheck: only the server exposes HTTP. Worker and MCP modes
# run flux as the main process, so the container exiting already signals
# death; report healthy while the container is running.
if [ "$FLUX_MODE" != "worker" ] && [ "$FLUX_MODE" != "mcp" ]; then
    URL="http://127.0.0.1:${FLUX_PORT:-8000}/health"
    exec python -c "import sys, urllib.request; urllib.request.urlopen(sys.argv[1], timeout=5)" "$URL"
fi
exit 0
