from __future__ import annotations

from pathlib import Path

from fastapi import Header
from fastapi.responses import HTMLResponse

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
        workflow_name: str = "agent_chat",
    ) -> None:
        super().__init__(
            server_url=server_url,
            agent_name=agent_name,
            operator_token=operator_token,
            port=port,
            workflow_name=workflow_name,
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
        agent_name = self.agent_name

        @self.app.get("/")
        async def index() -> HTMLResponse:
            html = (web_dir / "index.html").read_text()
            html = html.replace("{{AGENT_NAME}}", agent_name)
            return HTMLResponse(html)
