"""Example: programmatic interaction with an agent in API mode.

Start the agent in API mode:
    flux agent start --mode api --port 9100

Then run this script to have a conversation:
    python examples/agents/api_client.py

API mode serves the console's `/console/*` surface: sessions are created
explicitly, and each turn streams from the session's own send endpoint. Every
request carries a Bearer token; state-changing ones also carry
`X-Flux-Console: 1`, which a cross-origin page cannot set without a preflight
the console never grants.
"""

import json

import httpx

AGENT_API = "http://localhost:9100"
TOKEN = "your-flux-token"
AGENT = "assistant"

HEADERS = {"Authorization": f"Bearer {TOKEN}", "X-Flux-Console": "1"}


def start_session(name: str | None = None) -> str:
    """Create a session for AGENT and return its execution id."""
    response = httpx.post(
        f"{AGENT_API}/console/sessions",
        json={"agent": AGENT, "name": name},
        headers=HEADERS,
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["execution_id"]


def send(session_id: str, message: str) -> None:
    """Stream one turn. The turn always ends with a `log_delta` frame
    carrying a fresh read of the execution log."""
    with httpx.stream(
        "POST",
        f"{AGENT_API}/console/sessions/{session_id}/send",
        json={"text": message},
        headers=HEADERS,
        timeout=300,
    ) as response:
        response.raise_for_status()
        streamed = False

        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue

            frame = json.loads(line[6:])
            kind, data = frame.get("kind"), frame.get("data", {})

            if kind == "token":
                print(data.get("text", ""), end="", flush=True)
                streamed = True
            elif kind == "tool_start":
                print(f"\n[tool] {data.get('name')}({data.get('args', {})})")
            elif kind == "tool_done":
                print(f"[tool] {data.get('name')} -> {data.get('status')}")
            elif kind == "chat_response":
                if data.get("content") and not streamed:
                    print(data["content"])
                print()
            elif kind == "elicitation":
                print(f"\n[auth] {data.get('server_name')}: {data.get('url')}")
                print("      respond with POST /console/sessions/{id}/elicitation")
            elif kind == "error":
                print(f"\n[error] {data.get('message')}")


def main():
    session = start_session()
    print(f"Session: {session}")

    while True:
        try:
            message = input("\n> ")
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if message.strip().lower() in ("/quit", "exit", "bye"):
            break

        send(session, message)


if __name__ == "__main__":
    main()
