"""
Multi-Agent Code Review System using Flux agent() and Graph.

This example demonstrates a multi-agent code review system where specialized
AI agents review code via a DAG-based Graph orchestration. Each agent is created
with the agent() primitive and focuses on a specific aspect (security, performance,
style, testing). Their findings are aggregated into a comprehensive review report.

Key Features:
- **Graph Orchestration**: Uses flux.tasks.Graph for DAG-based fan-out/fan-in
- **agent() Primitive**: Each reviewer is a Flux agent() task with retries and observability
- **Specialized Agents**: 4 agents with domain-specific system prompts
- **Production Ready**: Structured output, priority scoring, CI/CD integration
- **Observable**: Full execution tracing in Flux workflow events

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a capable model: ollama pull llama3.2
    3. Start Ollama service: ollama serve

Usage:
    # Review a simple function
    flux workflow run multi_agent_code_review_ollama '{
        "code": "def unsafe_query(user_input):\\n    return db.execute(f\\"SELECT * FROM users WHERE name={user_input}\\")"
    }'

    # Review with context
    flux workflow run multi_agent_code_review_ollama '{
        "code": "...",
        "file_path": "api/auth.py",
        "context": "Authentication endpoint for user login"
    }'
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from flux import ExecutionContext, task, workflow
from flux.tasks import Graph
from flux.tasks.ai import agent


REVIEW_OUTPUT_FORMAT = """
Provide your review as a JSON array of findings. Each finding should have:
- severity: "critical" | "high" | "medium" | "low"
- issue: string (description)
- line: number | null
- recommendation: string

Example: [{{"severity": "high", "issue": "SQL injection", "line": 42, "recommendation": "Use parameterized queries"}}]

Respond with ONLY the JSON array, no other text."""

TESTING_OUTPUT_FORMAT = """
Provide your suggestions as a JSON array. Each suggestion should have:
- priority: "high" | "medium" | "low"
- test_case: string (description)
- verifies: string (what it tests)
- importance: string (why it matters)

Respond with ONLY the JSON array, no other text."""


def parse_llm_json_response(content: str) -> list[dict[str, Any]]:
    """Parse JSON from LLM response with robust error handling."""
    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        if len(lines) > 2:
            content = "\n".join(lines[1:-1])
        elif len(lines) > 1:
            content = "\n".join(lines[1:])

    content = content.replace("```", "").strip()

    try:
        result = json.loads(content)
        return result if isinstance(result, list) else [result]
    except json.JSONDecodeError:
        pass

    content = content.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    try:
        result = json.loads(content)
        return result if isinstance(result, list) else [result]
    except json.JSONDecodeError:
        pass

    for match in re.findall(r"\[[\s\S]*\]|\{[\s\S]*\}", content):
        try:
            result = json.loads(match)
            return result if isinstance(result, list) else [result]
        except json.JSONDecodeError:
            continue

    raise json.JSONDecodeError(
        "Could not parse JSON from LLM response after multiple attempts",
        content,
        0,
    )


def _build_review_prompt(
    code: str,
    file_path: str | None,
    context: str | None,
    output_format: str,
) -> str:
    parts = []
    if file_path:
        parts.append(f"File: {file_path}")
    if context:
        parts.append(f"Context: {context}")

    context_str = "\n".join(parts) if parts else "No additional context provided."

    return f"""{context_str}

Code to review:
```
{code}
```

{output_format}"""


def _parse_review(agent_name: str, raw_output: str, key: str = "findings") -> dict[str, Any]:
    try:
        items = parse_llm_json_response(raw_output)
        return {"agent": agent_name, "status": "success", key: items}
    except json.JSONDecodeError as e:
        return {"agent": agent_name, "status": "parse_error", "error": str(e), key: []}


@task
async def run_security(input_data: dict[str, Any]) -> dict[str, Any]:
    """Graph node: run security review agent."""
    security_reviewer = await agent(
        "You are a security code reviewer with expertise in finding vulnerabilities. "
        "Analyze code for: SQL injection, XSS, authentication issues, hardcoded secrets, "
        "input validation, command injection, path traversal, insecure cryptography, "
        "race conditions, and sensitive data exposure.",
        model="ollama/llama3.2",
        name="security_review",
    )
    prompt = _build_review_prompt(
        input_data["code"],
        input_data.get("file_path"),
        input_data.get("context"),
        REVIEW_OUTPUT_FORMAT,
    )
    raw = await security_reviewer(prompt)
    return _parse_review("security", raw)


@task
async def run_performance(input_data: dict[str, Any]) -> dict[str, Any]:
    """Graph node: run performance review agent."""
    performance_reviewer = await agent(
        "You are a performance optimization expert. "
        "Review code for: algorithm efficiency, unnecessary loops, inefficient data structures, "
        "memory leaks, database query optimization, missing caching, redundant computations, "
        "I/O bottlenecks, and blocking operations.",
        model="ollama/llama3.2",
        name="performance_review",
    )
    prompt = _build_review_prompt(
        input_data["code"],
        input_data.get("file_path"),
        input_data.get("context"),
        REVIEW_OUTPUT_FORMAT,
    )
    raw = await performance_reviewer(prompt)
    return _parse_review("performance", raw)


@task
async def run_style(input_data: dict[str, Any]) -> dict[str, Any]:
    """Graph node: run style review agent."""
    style_reviewer = await agent(
        "You are a code quality and style expert. "
        "Review code for: readability, naming conventions, organization, documentation, "
        "DRY violations, function complexity, magic numbers, error handling, type hints, "
        "and PEP 8 compliance.",
        model="ollama/llama3.2",
        name="style_review",
    )
    prompt = _build_review_prompt(
        input_data["code"],
        input_data.get("file_path"),
        input_data.get("context"),
        REVIEW_OUTPUT_FORMAT,
    )
    raw = await style_reviewer(prompt)
    return _parse_review("style", raw)


@task
async def run_testing(input_data: dict[str, Any]) -> dict[str, Any]:
    """Graph node: run testing review agent."""
    testing_reviewer = await agent(
        "You are a testing and quality assurance expert. "
        "Suggest: critical test cases, edge cases, error conditions, integration tests, "
        "mock requirements, test data, missing coverage, and regression tests.",
        model="ollama/llama3.2",
        name="testing_review",
    )
    prompt = _build_review_prompt(
        input_data["code"],
        input_data.get("file_path"),
        input_data.get("context"),
        TESTING_OUTPUT_FORMAT,
    )
    raw = await testing_reviewer(prompt)
    return _parse_review("testing", raw, key="suggestions")


@task
async def collect_reviews(*review_outputs: dict[str, Any]) -> dict[str, Any]:
    """Graph fan-in node: aggregate all review results."""
    reviews = list(review_outputs)
    by_agent: dict[str, Any] = {}
    all_findings: list[dict[str, Any]] = []
    all_suggestions: list[dict[str, Any]] = []
    counts: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    agents_completed = 0
    agents_failed = 0

    for review in reviews:
        agent_name = review.get("agent", "unknown")
        status = review.get("status", "unknown")
        by_agent[agent_name] = review

        if status == "success":
            agents_completed += 1
            if "findings" in review:
                for finding in review["findings"]:
                    severity = finding.get("severity", "low")
                    counts[severity] = counts.get(severity, 0) + 1
                    all_findings.append({"agent": agent_name, **finding})
            if "suggestions" in review:
                for suggestion in review["suggestions"]:
                    all_suggestions.append({"agent": agent_name, **suggestion})
        else:
            agents_failed += 1

    return {
        "by_agent": by_agent,
        "all_findings": all_findings,
        "all_suggestions": all_suggestions,
        "counts": counts,
        "agents_completed": agents_completed,
        "agents_failed": agents_failed,
    }


@task
async def generate_summary_report(aggregated: dict[str, Any]) -> dict[str, Any]:
    """Generate final summary report from aggregated findings."""
    counts = aggregated["counts"]
    total_issues = sum(counts.values())

    recommendations = []
    for finding in aggregated["all_findings"]:
        severity = finding.get("severity", "low")
        if severity in ["critical", "high"]:
            issue = finding.get("issue", "Unknown issue")
            line = finding.get("line")
            line_info = f" (line {line})" if line else ""
            recommendations.append(f"{severity.upper()}: {issue}{line_info}")

    return {
        "summary": {
            "total_issues": total_issues,
            "critical": counts.get("critical", 0),
            "high": counts.get("high", 0),
            "medium": counts.get("medium", 0),
            "low": counts.get("low", 0),
            "agents_completed": aggregated["agents_completed"],
            "agents_failed": aggregated["agents_failed"],
        },
        "by_agent": aggregated["by_agent"],
        "recommendations": recommendations[:10],
        "test_suggestions_count": len(aggregated["all_suggestions"]),
    }


@workflow
async def multi_agent_code_review_ollama(ctx: ExecutionContext[dict[str, Any]]):
    """
    Multi-agent code review system using Flux agent() and Graph.

    Four agent() tasks review code via a Graph DAG:
    - security_review: Finds vulnerabilities and security issues
    - performance_review: Identifies optimization opportunities
    - style_review: Reviews code quality and maintainability
    - testing_review: Suggests test cases and coverage improvements

    Graph executes nodes sequentially following the DAG topology.

    Input format:
    {
        "code": "Source code as string (required)",
        "file_path": "Optional file path for context",
        "context": "Optional additional context",
        "model": "llama3.2",
        "ollama_url": "http://localhost:11434"
    }

    Returns:
        Comprehensive review report with findings from all agents
    """
    start_time = datetime.now()

    raw_input = ctx.input or {}
    if not raw_input.get("code"):
        return {"error": "No code provided", "execution_id": ctx.execution_id}

    graph = (
        Graph("code_review")
        .add_node("security", run_security)
        .add_node("performance", run_performance)
        .add_node("style", run_style)
        .add_node("testing", run_testing)
        .add_node("aggregate", collect_reviews)
        .add_node("report", generate_summary_report)
        .start_with("security")
        .start_with("performance")
        .start_with("style")
        .start_with("testing")
        .add_edge("security", "aggregate")
        .add_edge("performance", "aggregate")
        .add_edge("style", "aggregate")
        .add_edge("testing", "aggregate")
        .add_edge("aggregate", "report")
        .end_with("report")
    )

    report = await graph(raw_input)

    end_time = datetime.now()
    execution_time = (end_time - start_time).total_seconds()

    report["metadata"] = {
        "execution_id": ctx.execution_id,
        "execution_time": execution_time,
        "model_used": raw_input.get("model", "llama3.2"),
        "code_length": len(raw_input["code"]),
        "file_path": raw_input.get("file_path"),
    }

    return report


if __name__ == "__main__":  # pragma: no cover
    print("=" * 80)
    print("Multi-Agent Code Review Demo - Security Focus")
    print("=" * 80 + "\n")

    test_code = '''
def login_user(username, password):
    """Authenticate user - INSECURE EXAMPLE"""
    query = f"SELECT * FROM users WHERE username='{username}' AND password='{password}'"
    result = db.execute(query)
    return result.fetchone()

def process_file(user_input):
    """Process uploaded file - INSECURE EXAMPLE"""
    file_path = f"/uploads/{user_input}"
    with open(file_path, 'r') as f:
        return f.read()
'''

    try:
        print("Reviewing code with known security issues...\n")
        result = multi_agent_code_review_ollama.run(
            {
                "code": test_code,
                "file_path": "auth.py",
                "context": "User authentication and file processing module",
                "model": "llama3.2",
            },
        )

        if result.has_failed:
            raise Exception(f"Review failed: {result.output}")

        output = result.output
        summary = output.get("summary", {})

        print("Review Summary:")
        print(f"  Total Issues: {summary.get('total_issues', 0)}")
        print(f"    Critical: {summary.get('critical', 0)}")
        print(f"    High: {summary.get('high', 0)}")
        print(f"    Medium: {summary.get('medium', 0)}")
        print(f"    Low: {summary.get('low', 0)}")
        print(f"  Agents Completed: {summary.get('agents_completed', 0)}/4\n")

        print("Top Recommendations:")
        for i, rec in enumerate(output.get("recommendations", [])[:5], 1):
            print(f"  {i}. {rec}")

        print("\n" + "=" * 80)
        print("Multi-agent code review completed!")
        print("=" * 80)

    except Exception as e:
        print(f"Error: {e}")
        print("\nMake sure:")
        print("1. Ollama is running: ollama serve")
        print("2. Model is available: ollama pull llama3.2")
