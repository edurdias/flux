from __future__ import annotations

import json
from typing import Any

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Static, TextArea
from textual.containers import Vertical


def truncate_json(data: Any, max_length: int = 80) -> str:
    """Truncate JSON string representation to max_length."""
    try:
        text = json.dumps(data, default=str)
    except (TypeError, ValueError):
        text = str(data)
    if len(text) <= max_length:
        return text
    return text[:max_length] + "..."


def format_json(data: Any) -> str:
    """Pretty-print data as JSON."""
    try:
        return json.dumps(data, indent=2, default=str)
    except (TypeError, ValueError):
        return str(data)


class JsonViewerModal(ModalScreen):
    """Modal screen for viewing full JSON content."""

    DEFAULT_CSS = """
    JsonViewerModal {
        align: center middle;
    }
    JsonViewerModal #json-dialog {
        width: 80%;
        height: 80%;
        border: solid #30363d;
        background: #161b22;
        padding: 1;
    }
    JsonViewerModal #json-title {
        dock: top;
        height: 1;
        color: #8b949e;
        text-style: bold;
    }
    JsonViewerModal #json-content {
        width: 100%;
        height: 1fr;
    }
    """

    BINDINGS = [
        ("escape", "dismiss", "Close"),
    ]

    def __init__(self, title: str, data: Any, **kwargs):
        super().__init__(**kwargs)
        self.title_text = title
        self.data = data

    def compose(self) -> ComposeResult:
        with Vertical(id="json-dialog"):
            yield Static(f" {self.title_text} (Esc to close)", id="json-title")
            yield TextArea(
                format_json(self.data),
                read_only=True,
                id="json-content",
                language="json",
            )
