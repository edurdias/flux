"""Custom tools for agents — referenced via tools_file in the agent YAML.

Each @task function becomes a tool the agent can call. The function
docstring is used as the tool description in the LLM prompt.

Usage:
    flux agent create my-agent \
        --model anthropic/claude-sonnet-4-20250514 \
        --system-prompt "You are a helpful assistant." \
        --tools-file examples/agents/custom_tools.py
"""

from flux import task


@task
async def lookup_user(username: str) -> dict:
    """Look up a user by username and return their profile information."""
    return {
        "username": username,
        "email": f"{username}@example.com",
        "role": "developer",
        "active": True,
    }


@task
async def create_ticket(title: str, description: str, priority: str = "medium") -> dict:
    """Create a support ticket with the given title, description, and priority."""
    import uuid

    ticket_id = f"TICKET-{uuid.uuid4().hex[:6].upper()}"
    return {
        "id": ticket_id,
        "title": title,
        "description": description,
        "priority": priority,
        "status": "open",
    }


@task
async def search_knowledge_base(query: str, max_results: int = 5) -> list[dict]:
    """Search the internal knowledge base for articles matching the query."""
    return [
        {"title": f"Article about {query}", "relevance": 0.95, "url": f"/kb/{i}"}
        for i in range(min(max_results, 3))
    ]
