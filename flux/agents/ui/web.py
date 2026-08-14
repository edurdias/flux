from __future__ import annotations

from pathlib import Path

from fastapi import Header
from fastapi.responses import HTMLResponse, PlainTextResponse
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
        allow_remote: bool = False,
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
            allow_remote=allow_remote,
        )
        self._install_host_guard()
        self._setup_web_routes()

    def _allowed_hosts(self) -> set[str]:
        """Host header values this console answers to, lowercased, without
        port (the port is already pinned by the bind)."""
        hosts = {"127.0.0.1", "localhost", "::1", self.host.strip("[]").lower()}
        return {host for host in hosts if host}

    def _install_host_guard(self) -> None:
        """Reject requests whose Host names something other than this
        console — the DNS-rebinding defense.

        Covers GETs too, unlike the Origin check: rebinding is a *read*
        attack (session lists, transcripts), so a state-changing-only gate
        would miss the point. Hand-rolled rather than Starlette's
        TrustedHostMiddleware because that one splits the header on ':',
        which mangles `[::1]:8080` — a bind the CLI's gate accepts.

        Not installed once the operator has deliberately exposed the
        console: rebinding exists to reach a loopback-only service through
        a victim's browser, and a reachable port needs no such trick.
        """
        if self.allow_remote:
            return

        allowed = self._allowed_hosts()

        @self.app.middleware("http")
        async def _guard_host(request, call_next):
            header = request.headers.get("host", "")
            host = header.rsplit(":", 1)[0] if not header.endswith("]") else header
            if host.strip("[]").lower() not in allowed:
                return PlainTextResponse("Invalid host header", status_code=400)
            return await call_next(request)

    def _startup_banner(self) -> list[str]:
        lines = [f"Console: http://{self.host}:{self.port}"]
        if self.allow_remote:
            lines += [
                "",
                "  WARNING: this console has no authentication of its own.",
                "  Anyone who can reach this port acts as the operator: every session,"
                " transcript, approval and spawn.",
                "  Put a proxy that authenticates in front of it, or bind loopback"
                " and use --mode api for remote access.",
                "",
            ]
        return lines

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
