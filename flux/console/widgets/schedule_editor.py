from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static, TabbedContent, TabPane


class ScheduleEditorModal(ModalScreen[dict | None]):
    """Modal for editing schedule configuration."""

    DEFAULT_CSS = """
    ScheduleEditorModal {
        align: center middle;
    }
    ScheduleEditorModal #editor-dialog {
        width: 70%;
        height: 70%;
        border: solid #30363d;
        background: #161b22;
        padding: 1;
    }
    ScheduleEditorModal #editor-title {
        height: 1;
        color: #8b949e;
        text-style: bold;
        margin-bottom: 1;
    }
    ScheduleEditorModal .field-row {
        layout: horizontal;
        height: 3;
        margin-bottom: 1;
    }
    ScheduleEditorModal .field-label {
        width: 12;
        color: #8b949e;
        padding-top: 1;
    }
    ScheduleEditorModal .field-input {
        width: 1fr;
    }
    ScheduleEditorModal #editor-buttons {
        layout: horizontal;
        height: 3;
        dock: bottom;
        margin-top: 1;
    }
    ScheduleEditorModal #editor-buttons Button {
        margin: 0 1;
    }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, schedule: dict[str, Any], **kwargs):
        super().__init__(**kwargs)
        self.schedule = schedule
        self._expression = schedule.get("expression", "* * * * *")

    def compose(self) -> ComposeResult:
        name = self.schedule.get("name", "Schedule")
        with Vertical(id="editor-dialog"):
            yield Static(f" Edit Schedule: {name}", id="editor-title")

            with TabbedContent("Fields", "Expression"):
                with TabPane("Fields", id="fields-tab"):
                    # Parse cron expression into fields
                    parts = self._expression.split()
                    while len(parts) < 5:
                        parts.append("*")

                    for i, (label, value) in enumerate(
                        [
                            ("Minute", parts[0]),
                            ("Hour", parts[1]),
                            ("Day", parts[2]),
                            ("Month", parts[3]),
                            ("Weekday", parts[4]),
                        ]
                    ):
                        with Horizontal(classes="field-row"):
                            yield Static(label, classes="field-label")
                            yield Input(value=value, id=f"cron-field-{i}", classes="field-input")

                with TabPane("Expression", id="expr-tab"):
                    with Horizontal(classes="field-row"):
                        yield Static("Cron", classes="field-label")
                        yield Input(
                            value=self._expression,
                            id="cron-expression",
                            classes="field-input",
                        )
                    yield Static(
                        "[#484f58]Format: minute hour day month weekday[/]\n"
                        "[#484f58]Examples: */5 * * * *  (every 5 min)"
                        "  |  0 9 * * 1-5  (weekdays 9am)[/]",
                    )

            with Horizontal(id="editor-buttons"):
                yield Button("Save", variant="primary", id="save-btn")
                yield Button("Cancel", variant="default", id="cancel-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            # Gather fields from the Fields tab
            try:
                parts = []
                for i in range(5):
                    field = self.query_one(f"#cron-field-{i}", Input)
                    parts.append(field.value.strip() or "*")
                expression = " ".join(parts)
            except Exception:
                expression = self._expression

            result = {
                "expression": expression,
                "schedule_id": self.schedule.get("id"),
            }
            self.dismiss(result)
        elif event.button.id == "cancel-btn":
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
