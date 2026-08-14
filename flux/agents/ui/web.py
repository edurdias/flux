from __future__ import annotations

from pathlib import Path

from fastapi import Header
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

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
        agent_name: str | None,
        operator_token: str | None = None,
        port: int = 8080,
        workflow_name: str = "agent_chat",
        host: str = "127.0.0.1",
        allowed_origins: tuple[str, ...] = (),
        session_id: str | None = None,
    ) -> None:
        super().__init__(
            server_url=server_url,
            agent_name=agent_name,
            operator_token=operator_token,
            port=port,
            workflow_name=workflow_name,
            host=host,
            allowed_origins=allowed_origins,
            session_id=session_id,
        )
        self._setup_web_routes()

    def _get_token_dependency(self):
        """Override: use operator_token instead of requiring a request Bearer.

        When no token was provided (auth disabled), requests pass through
        without authentication — the Flux server treats them as anonymous.
        """
        token = self.operator_token

        def _dep(authorization: str | None = Header(default=None)) -> str | None:  # noqa: ARG001
            return token

        return _dep

    def _setup_web_routes(self) -> None:
        web_dir = Path(__file__).parent.parent / "web"

        # console.html pulls console.css/console.js from here; the bundle
        # references no other origin.
        self.app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")

        @self.app.get("/")
        async def index() -> HTMLResponse:
            # Served verbatim. The console learns the bound agent (and
            # everything else) from GET /console/state, so nothing is
            # templated into the shell -- there is no injection surface here
            # to escape, unlike the retired single-agent page.
            return HTMLResponse((web_dir / "console.html").read_text())
