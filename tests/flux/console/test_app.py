from __future__ import annotations

from flux.console.app import FluxConsoleApp


class TestFluxConsoleAppInit:
    def test_creates_with_server_url(self):
        app = FluxConsoleApp(server_url="http://localhost:9000")
        assert app.server_url == "http://localhost:9000"

    def test_has_title(self):
        app = FluxConsoleApp(server_url="http://localhost:8000")
        assert "Flux" in app.TITLE

    def test_has_six_tab_names(self):
        app = FluxConsoleApp(server_url="http://localhost:8000")
        assert len(app.TAB_NAMES) == 6
