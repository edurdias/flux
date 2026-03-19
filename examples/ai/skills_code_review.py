"""
Code Review Agent with Python-Defined Skills.

This example demonstrates how to define skills directly in Python (without
SKILL.md files) and use them with the agent() task primitive. The agent
has two skills — security reviewer and performance reviewer — and chooses
which to activate based on the user's request.

This complements skills_agent.py which uses SKILL.md files for skill definitions.
Both approaches can be mixed: SkillCatalog supports directory discovery and
explicit registration in the same catalog.

Key Features:
- Python-defined skills (no SKILL.md files needed)
- Mixed catalog (directory discovery + explicit registration)
- Multiple specialized review skills
- LLM selects the right skill based on the code review request

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a model: ollama pull llama3.2
    3. Start Ollama service: ollama serve

Usage:
    # Run directly
    python examples/ai/skills_code_review.py

    # Register and run via CLI
    flux workflow register examples/ai/skills_code_review.py
    flux workflow run skills_code_review_ollama '{"code": "def login(user, pwd): return db.query(f\\\"SELECT * FROM users WHERE name='{user}' AND pass='{pwd}'\\\")"}'
"""

from __future__ import annotations

from typing import Any

from flux import ExecutionContext, task, workflow
from flux.tasks.ai import Skill, SkillCatalog, agent

security_skill = Skill(
    name="security-reviewer",
    description="Reviews code for security vulnerabilities including injection, XSS, "
    "authentication flaws, and OWASP Top 10 issues. Use when the user asks for a "
    "security review or mentions vulnerabilities.",
    instructions=(
        "You are a security expert. Review the provided code for security issues.\n\n"
        "Check for:\n"
        "1. SQL injection vulnerabilities\n"
        "2. Cross-site scripting (XSS)\n"
        "3. Authentication and authorization flaws\n"
        "4. Sensitive data exposure\n"
        "5. Input validation issues\n\n"
        "For each issue found, provide:\n"
        "- Severity (Critical/High/Medium/Low)\n"
        "- Description of the vulnerability\n"
        "- A concrete fix with code\n"
    ),
)

performance_skill = Skill(
    name="performance-reviewer",
    description="Reviews code for performance issues including algorithmic complexity, "
    "memory leaks, unnecessary allocations, and database query optimization. "
    "Use when the user asks for a performance review or mentions optimization.",
    instructions=(
        "You are a performance engineering expert. Review the provided code for "
        "performance issues.\n\n"
        "Check for:\n"
        "1. Algorithmic complexity (O(n^2) or worse)\n"
        "2. Unnecessary memory allocations\n"
        "3. N+1 query patterns\n"
        "4. Missing caching opportunities\n"
        "5. Blocking I/O in async contexts\n\n"
        "For each issue found, provide:\n"
        "- Impact (High/Medium/Low)\n"
        "- Description of the bottleneck\n"
        "- An optimized alternative with code\n"
    ),
)

catalog = SkillCatalog([security_skill, performance_skill])


@task
async def run_linter(code: str) -> str:
    """Run static analysis on the provided code and return findings."""
    issues = []
    if "eval(" in code:
        issues.append("WARNING: Use of eval() detected — potential code injection.")
    if "SELECT" in code and ("f'" in code or 'f"' in code):
        issues.append("WARNING: Possible SQL injection — string formatting in query.")
    if "password" in code.lower() and "hash" not in code.lower():
        issues.append("WARNING: Password handling without hashing detected.")
    if not issues:
        issues.append("No issues detected by static analysis.")
    return "\n".join(issues)


reviewer = agent(
    "You are a code review assistant. Use the appropriate skill for the type of "
    "review requested. If the user asks for a general review, use both skills.",
    model="ollama/llama3.2",
    name="code-reviewer",
    tools=[run_linter],
    skills=catalog,
).with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=120)


@workflow
async def skills_code_review_ollama(ctx: ExecutionContext[dict[str, Any]]):
    """
    A code review agent that activates specialized review skills.

    Input format:
    {
        "code": "def my_function(): ...",
        "review_type": "security"    // optional: "security", "performance", or omit for general
    }

    Returns:
        Dictionary with the review results and execution metadata.
    """
    input_data = ctx.input or {}

    code = input_data.get("code")
    if not code:
        return {
            "error": "Missing required parameter 'code'",
            "execution_id": ctx.execution_id,
        }

    review_type = input_data.get("review_type", "general")
    instruction = f"Review this code ({review_type} review):\n\n```\n{code}\n```"

    review = await reviewer(instruction)

    return {
        "review_type": review_type,
        "review": review,
        "skills_available": [s.name for s in catalog.list()],
        "execution_id": ctx.execution_id,
    }


if __name__ == "__main__":  # pragma: no cover
    sample_code = """
def login(username, password):
    query = f"SELECT * FROM users WHERE name='{username}' AND pass='{password}'"
    result = db.execute(query)
    if result:
        return {"token": generate_token(), "user": result[0]}
    return None
"""

    try:
        print("=" * 80)
        print("Code Review Agent Demo (Python-Defined Skills + Ollama)")
        print(f"Skills: {[s.name for s in catalog.list()]}")
        print("=" * 80 + "\n")

        print("Requesting security review...\n")
        result = skills_code_review_ollama.run(
            {
                "code": sample_code,
                "review_type": "security",
            },
        )

        if result.has_failed:
            raise Exception(f"Workflow failed: {result.output}")

        output = result.output
        print(f"Review type: {output.get('review_type')}")
        print(f"Execution ID: {output.get('execution_id')}\n")
        print("-" * 80)
        print(output.get("review", ""))
        print("-" * 80)

    except Exception as e:
        print(f"Error: {e}")
        print("\nMake sure:")
        print("1. Ollama is running: ollama serve")
        print("2. Model is pulled: ollama pull llama3.2")
