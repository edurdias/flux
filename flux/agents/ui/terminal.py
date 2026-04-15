from __future__ import annotations

import asyncio
import webbrowser

from flux.agents.types import SessionEndOutput
from flux.agents.ui import UI


class TerminalUI(UI):
    async def display_response(self, content: str | None) -> None:
        if content is not None:
            print(f"\n{content}\n")

    async def display_tool_start(self, name: str, args: dict) -> None:
        args_str = ", ".join(f'{k}="{v}"' for k, v in args.items())
        print(f"\nCalling {name}({args_str})... ", end="", flush=True)

    async def display_tool_done(self, name: str, status: str) -> None:
        if status == "success":
            print("Done.")
        else:
            print("Error.")

    async def display_token(self, text: str) -> None:
        print(text, end="", flush=True)

    async def display_elicitation(self, request: dict) -> dict:
        server_name = request.get("server_name", "unknown")
        message = request.get("message", "Authorization required")
        url = request.get("url", "")
        elicitation_id = request.get("elicitation_id", "")

        print(f"\n[{server_name}] {message}")
        answer = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: input("Open browser to authorize? [Y/n]: "),
        )

        if answer.strip().lower() in ("", "y", "yes"):
            webbrowser.open(url)
            print("Opening browser... waiting for authorization.")
            return {
                "elicitation_response": {
                    "elicitation_id": elicitation_id,
                    "action": "accept",
                },
            }
        else:
            return {
                "elicitation_response": {
                    "elicitation_id": elicitation_id,
                    "action": "decline",
                },
            }

    async def prompt_user(self) -> str:
        line = await asyncio.get_event_loop().run_in_executor(None, lambda: input("> "))
        return line

    async def display_session_info(self, session_id: str, agent_name: str) -> None:
        print(f"\nFlux Agent — {agent_name}")
        print(f"Session: {session_id}")
        print("Type /help for commands, Ctrl+D to exit.\n")

    async def display_session_end(self, output: SessionEndOutput) -> None:
        print(f"\nSession ended: {output.reason} ({output.turns} turns)")
