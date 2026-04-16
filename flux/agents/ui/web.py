from __future__ import annotations

from pathlib import Path

from fastapi.responses import FileResponse

from flux.agents.ui.api import ApiUI


class WebUI(ApiUI):
    def __init__(self, port: int | None = None):
        super().__init__(port=port or 8080)  # type: ignore[call-arg]  # TODO(task-7): WebUI constructor rewrite lands in Task 7
        self._setup_web_routes()

    def _setup_web_routes(self):
        web_dir = Path(__file__).parent.parent / "web"

        @self.app.get("/")
        async def index():
            return FileResponse(web_dir / "index.html")
