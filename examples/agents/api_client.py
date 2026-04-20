"""Example: programmatic interaction with an agent in API mode.

Start the agent in API mode:
    flux agent start assistant --mode api --port 9100

Then run this script to have a conversation:
    python examples/agents/api_client.py
"""

import httpx

AGENT_API = "http://localhost:9100"
TOKEN = "your-flux-token"


def chat(message: str | None = None, session_id: str | None = None):
    """Send a message and stream the response."""
    url = f"{AGENT_API}/chat"
    params = {}
    if session_id:
        params["session"] = session_id

    body = {}
    if message:
        body["message"] = message

    with httpx.stream(
        "POST",
        url,
        params=params,
        json=body,
        headers={"Authorization": f"Bearer {TOKEN}"},
        timeout=120,
    ) as response:
        response.raise_for_status()
        current_session = session_id
        full_response = []

        for line in response.iter_lines():
            if line.startswith("data: "):
                import json

                data = json.loads(line[6:])
                event_type = data.get("type", "")

                if event_type == "session_id":
                    current_session = data["id"]
                    print(f"Session: {current_session}")
                elif event_type == "token":
                    print(data["text"], end="", flush=True)
                    full_response.append(data["text"])
                elif event_type == "tool_start":
                    print(f"\n[Tool] {data['name']}({data.get('args', {})})")
                elif event_type == "tool_done":
                    print(f"[Tool] {data['name']} -> {data['status']}")
                elif event_type == "chat_response":
                    if data.get("content") and not full_response:
                        print(data["content"])
                    print()

        return current_session


def main():
    print("Starting new session...")
    session = chat()

    while True:
        try:
            message = input("\n> ")
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if message.strip().lower() in ("/quit", "exit", "bye"):
            break

        session = chat(message, session)


if __name__ == "__main__":
    main()
