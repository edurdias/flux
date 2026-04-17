from __future__ import annotations

from pathlib import Path

from fastapi import Header, HTTPException
from fastapi.responses import FileResponse

from flux.agents.ui.api import ApiUI


class WebUI(ApiUI):
    """Web serving mode.

    Binds a chat page at `/` and uses the operator's Flux token (set at
    process-start time) for all Flux calls. No per-request Bearer check —
    the agent process is expected to run on localhost as a single-operator
    chat UI.
    """

    def __init__(
        self,
        server_url: str,
        agent_name: str,
        operator_token: str | None = None,
        port: int = 8080,
    ) -> None:
        super().__init__(
            server_url=server_url,
            agent_name=agent_name,
            operator_token=operator_token,
            port=port,
        )
        self._setup_web_routes()

    def _get_token_dependency(self):
        """Override: use operator_token instead of requiring a request Bearer."""
        token = self.operator_token

        def _dep(authorization: str | None = Header(default=None)) -> str:  # noqa: ARG001
            if not token:
                raise HTTPException(
                    status_code=401,
                    detail="Agent process started without a token; "
                    "set FLUX_AUTH_TOKEN or run 'flux auth login'",
                )
            return token

        return _dep

    def _setup_web_routes(self) -> None:
        web_dir = Path(__file__).parent.parent / "web"

        @self.app.get("/")
        async def index() -> FileResponse:
            return FileResponse(web_dir / "index.html")
