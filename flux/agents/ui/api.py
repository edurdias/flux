from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException

from flux.agents.console.app import mount_console_routes

_DEFAULT_PORTS = (("http", 80), ("https", 443))


def _authority(host: str) -> str:
    """Host as a browser writes it in an Origin: IPv6 literals bracketed."""
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


class ApiUI:
    """HTTP/SSE agent API.

    Every request requires a Bearer token; that token is passed through to
    the Flux server on a per-request basis. The operator_token (set at
    process-start time) is only used by WebUI, which overrides the auth
    dependency.
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
        self.server_url = server_url
        self.agent_name = agent_name
        self.operator_token = operator_token
        self.session_id = session_id
        self.host = host
        self.port = port
        self.workflow_name = workflow_name
        self.allowed_origins = allowed_origins
        self.allow_remote = allow_remote
        self.app = FastAPI(title="Flux Agent API")
        self._setup_routes()
        mount_console_routes(
            self.app,
            server_url=self.server_url,
            agent_name=self.agent_name,
            token_dependency=self._get_token_dependency(),
            csrf_dependency=self._csrf_dependency(),
            session_id=self.session_id,
            workflow_name=self.workflow_name,
        )

    def _extract_token(self, authorization: str | None) -> str:
        """API auth: require a Bearer token on every request."""
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(
                status_code=401,
                detail="Missing or invalid Authorization header",
            )
        token = authorization.split(" ", 1)[1].strip()
        if not token:
            raise HTTPException(status_code=401, detail="Empty Bearer token")
        return token

    def _get_token_dependency(self):
        """Overridable hook for subclasses (WebUI) to change auth behavior."""

        def _dep(authorization: str | None = Header(default=None)) -> str:
            return self._extract_token(authorization)

        return _dep

    def _origin_allowlist(self) -> set[str]:
        """Origins the console's own frontend can legitimately present.

        Built from the bind host/port (not hardcoded) so a non-default
        --host still gets a working allowlist; 127.0.0.1/localhost are
        always included since browsers treat a loopback server as
        reachable under either name interchangeably. Each host is admitted
        under both schemes -- an Origin header cannot be forged, so the
        https twin of the console's own origin adds no reachable attacker
        while covering a TLS-terminating proxy in front of api mode.
        Wildcard binds
        (0.0.0.0 / ::) never appear in a browser's Origin header, so they
        contribute nothing — an externally exposed console must name the
        origins operators will actually use via ``allowed_origins``
        (`flux agent start --allow-origin`).
        """
        hosts = {self.host, "127.0.0.1", "localhost"} - {"0.0.0.0", "::", "[::]"}
        origins: set[str] = set()
        for host in hosts:
            authority = _authority(host)
            for scheme, default_port in _DEFAULT_PORTS:
                origins.add(f"{scheme}://{authority}:{self.port}")
                # A browser elides the port when it is the scheme's default,
                # so a console on :80 only ever sees "http://localhost".
                if self.port == default_port:
                    origins.add(f"{scheme}://{authority}")
        origins.update(origin.rstrip("/") for origin in self.allowed_origins)
        return origins

    def _csrf_dependency(self):
        """Origin/CSRF defense for state-changing routes.

        Requires the custom `X-Flux-Console` header (forces a CORS
        preflight a third-party site cannot silently pass) and, only when
        the browser actually sent an Origin header, a match against this
        console's own origin. Applied to every POST/PUT route -- including
        the pre-existing /chat, /approval, /elicitation, which were
        drive-by-POSTable from any website before this hardening.
        """
        allowlist = self._origin_allowlist()

        def _dep(
            x_flux_console: str | None = Header(default=None, alias="X-Flux-Console"),
            origin: str | None = Header(default=None),
        ) -> None:
            if x_flux_console != "1":
                raise HTTPException(
                    status_code=403,
                    detail="Missing required 'X-Flux-Console' header",
                )
            if origin is not None and origin not in allowlist:
                raise HTTPException(status_code=403, detail="Origin not allowed")

        return _dep

    def _setup_routes(self) -> None:
        @self.app.get("/health")
        async def health() -> dict:
            return {"status": "ok"}

        # The console's /console/* routes (mounted in __init__) are the only
        # agent API: same contract in web and api mode, per-session rather
        # than bound to one process-level agent name.

    def _startup_banner(self) -> list[str]:
        """Lines printed before the server binds. Nothing was printed
        before, so operators had to guess the URL."""
        return [f"Agent API: http://{self.host}:{self.port}"]

    async def serve(self) -> None:
        import uvicorn

        for line in self._startup_banner():
            print(line)

        config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()
