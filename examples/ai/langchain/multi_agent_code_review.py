"""
Multi-Agent Code Review System using LangGraph + ChatOllama (Local LLM).

This example demonstrates a production-ready multi-agent code review system where
LangGraph's StateGraph orchestrates specialized review agents, while Flux provides
workflow durability, retries, scheduling, and execution tracing.

What LangGraph handles:
- StateGraph with typed state and reducers (parallel review accumulation)
- Conditional edge routing (skip_testing flag)
- Node fan-out to security, performance, style agents
- Conditional branch to testing agent

What Flux handles:
- Durable workflow execution with retries
- Execution history and tracing
- Scheduling and worker coordination
- Pause/resume capability

Compared to examples/ai/multi_agent_code_review_ollama.py (pure Flux + Graph DAG):
this variant delegates graph execution to LangGraph's StateGraph, showing how
LangGraph's conditional edges and state reducers integrate with Flux orchestration.

Dependencies:
    pip install langchain-core langchain-ollama langgraph

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a capable model: ollama pull llama3.2
    3. Start Ollama service: ollama serve
    4. Start Flux server: poetry run flux start server
    5. Start Flux worker: poetry run flux start worker worker-1

Usage:
    # Review a simple function
    flux workflow run multi_agent_code_review_langgraph '{
        "code": "def unsafe_query(user_input):\\n    return db.execute(f\\"SELECT * FROM users WHERE name={user_input}\\")"
    }'

    # Review with specific model
    flux workflow run multi_agent_code_review_langgraph '{
        "code": "def process(data):\\n    return [x*2 for x in data]",
        "model": "llama3.2"
    }'

    # Review with context and file path
    flux workflow run multi_agent_code_review_langgraph '{
        "code": "...",
        "file_path": "api/auth.py",
        "context": "Authentication endpoint for user login"
    }'

    # Skip testing agent (demonstrates conditional edge)
    flux workflow run multi_agent_code_review_langgraph '{
        "code": "...",
        "skip_testing": true
    }'

Example Output:
    {
        "summary": {
            "total_issues": 8,
            "critical": 1,
            "high": 2,
            "medium": 4,
            "low": 1,
            "agents_completed": 4,
            "agents_failed": 0
        },
        "by_agent": {
            "security": {"findings": [...]},
            "performance": {"findings": [...]},
            "style": {"findings": [...]},
            "testing": {"suggestions": [...]}
        },
        "recommendations": [
            "CRITICAL: SQL injection vulnerability (line 2)",
            "HIGH: Missing input validation"
        ]
    }
"""

from __future__ import annotations

import json
import operator
import re
from datetime import datetime
from typing import Annotated, Any, TypedDict

from langchain_ollama import ChatOllama
from langgraph.graph import END, START, StateGraph

from flux import ExecutionContext, task, workflow


# =============================================================================
# Typed State
# =============================================================================


class ReviewState(TypedDict):
    code: str
    file_path: str | None
    context: str | None
    model: str
    ollama_url: str
    skip_testing: bool
    reviews: Annotated[list[dict[str, Any]], operator.add]


# =============================================================================
# Agent System Prompts
# =============================================================================

REVIEW_PROMPTS: dict[str, str] = {
    "security": """You are a security code reviewer with expertise in finding vulnerabilities.

Analyze the provided code for:
- SQL injection vulnerabilities
- XSS (Cross-Site Scripting) vulnerabilities
- Authentication and authorization issues
- Hardcoded secrets, API keys, or credentials
- Input validation problems
- Command injection risks
- Path traversal vulnerabilities
- Insecure cryptography usage
- Race conditions and TOCTOU issues
- Sensitive data exposure

For each finding, provide:
1. Severity (critical/high/medium/low)
2. Issue description
3. Line number if identifiable
4. Specific recommendation to fix

Be concise but thorough. Focus on real security issues, not theoretical concerns.""",
    "performance": """You are a performance optimization expert.

Review the code for:
- Algorithm efficiency issues (e.g., O(n²) when O(n log n) is possible)
- Unnecessary loops or iterations
- Inefficient data structures
- Memory leaks or excessive memory usage
- Database query optimization opportunities
- Missing indexes or caching opportunities
- Redundant computations
- I/O bottlenecks
- Blocking operations that could be async

For each finding, provide:
1. Severity (high/medium/low based on performance impact)
2. Issue description
3. Line number if identifiable
4. Specific optimization recommendation

Focus on impactful optimizations, not micro-optimizations.""",
    "style": """You are a code quality and style expert.

Review the code for:
- Code readability and clarity
- Naming conventions (variables, functions, classes)
- Code organization and structure
- Documentation and comments
- DRY (Don't Repeat Yourself) violations
- Function length and complexity
- Magic numbers or strings
- Error handling quality
- Type hints and type safety
- PEP 8 compliance (for Python)

For each finding, provide:
1. Severity (medium/low - style issues are rarely critical)
2. Issue description
3. Line number if identifiable
4. Specific improvement recommendation

Be practical - focus on issues that affect maintainability.""",
    "testing": """You are a testing and quality assurance expert.

Analyze the code and suggest:
- Critical test cases that should be written
- Edge cases that need coverage
- Error conditions to test
- Integration test scenarios
- Mock or fixture requirements
- Test data needed
- Areas with missing test coverage
- Regression test recommendations

For each suggestion, provide:
1. Priority (high/medium/low)
2. Test case description
3. What it would verify
4. Why it's important

Focus on tests that provide real value and catch likely bugs.""",
}


# =============================================================================
# Helper Functions
# =============================================================================


def parse_json_response(content: str) -> list[dict[str, Any]]:
    """
    Parse JSON from LLM response with robust error handling.

    Handles common issues:
    - Markdown code fences
    - Invalid control characters
    - Extra whitespace
    - Partial responses

    Args:
        content: Raw LLM response content

    Returns:
        Parsed JSON as list of dictionaries

    Raises:
        json.JSONDecodeError: If JSON cannot be parsed after cleanup attempts
    """
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

    json_pattern = r"\[[\s\S]*\]|\{[\s\S]*\}"
    matches = re.findall(json_pattern, content)

    for match in matches:
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


def _build_context_str(file_path: str | None, context: str | None) -> str:
    parts = []
    if file_path:
        parts.append(f"File: {file_path}")
    if context:
        parts.append(f"Context: {context}")
    return "\n".join(parts) if parts else "No additional context provided."


def _build_user_prompt(agent_name: str, context_str: str, code: str) -> str:
    if agent_name == "testing":
        return f"""{context_str}

Code to review:
```
{code}
```

Provide your testing suggestions as a JSON array. Each suggestion should have:
- priority: "high" | "medium" | "low"
- test_case: string (description)
- verifies: string (what it tests)
- importance: string (why it matters)

Respond with ONLY the JSON array, no other text."""
    severity_hint = {
        "security": '"critical" | "high" | "medium" | "low"',
        "performance": '"high" | "medium" | "low"',
        "style": '"medium" | "low"',
    }[agent_name]
    return f"""{context_str}

Code to review:
```
{code}
```

Provide your {agent_name} review as a JSON array of findings. Each finding should have:
- severity: {severity_hint}
- issue: string (description)
- line: number | null
- recommendation: string

Respond with ONLY the JSON array, no other text."""


# =============================================================================
# Reviewer Factory
# =============================================================================


def make_reviewer(agent_name: str):
    """
    Create a LangGraph node function for a named review agent.

    Returns a callable that takes ReviewState and returns a partial state update
    with the agent's review appended to `reviews`.
    """

    async def reviewer(state: ReviewState) -> dict[str, Any]:
        try:
            llm = ChatOllama(model=state["model"], base_url=state["ollama_url"])
            context_str = _build_context_str(state.get("file_path"), state.get("context"))
            user_prompt = _build_user_prompt(agent_name, context_str, state["code"])

            messages = [
                ("system", REVIEW_PROMPTS[agent_name]),
                ("human", user_prompt),
            ]

            response = await llm.ainvoke(messages)
            content = response.content.strip()

            if agent_name == "testing":
                suggestions = parse_json_response(content)
                return {
                    "reviews": [
                        {
                            "agent": agent_name,
                            "status": "success",
                            "suggestions": suggestions,
                            "model_used": state["model"],
                        },
                    ],
                }
            else:
                findings = parse_json_response(content)
                return {
                    "reviews": [
                        {
                            "agent": agent_name,
                            "status": "success",
                            "findings": findings,
                            "model_used": state["model"],
                        },
                    ],
                }

        except json.JSONDecodeError as e:
            result: dict[str, Any] = {
                "agent": agent_name,
                "status": "parse_error",
                "error": f"Failed to parse LLM response as JSON: {str(e)}",
            }
            if agent_name == "testing":
                result["suggestions"] = []
            else:
                result["findings"] = []
            return {"reviews": [result]}

        except Exception as e:
            raise RuntimeError(
                f"{agent_name.capitalize()} review failed: {str(e)}. "
                "Make sure Ollama is running (ollama serve) and the model is available.",
            ) from e

    reviewer.__name__ = f"{agent_name}_reviewer"
    return reviewer


# =============================================================================
# Conditional Edge
# =============================================================================


def should_run_testing(state: ReviewState) -> str:
    """Route from START: run testing agent or skip it based on skip_testing flag."""
    return "skip" if state.get("skip_testing", False) else "run"


# =============================================================================
# Flux Tasks
# =============================================================================


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=300)
async def run_langgraph_review(
    code: str,
    model: str,
    ollama_url: str,
    file_path: str | None = None,
    context: str | None = None,
    skip_testing: bool = False,
) -> list[dict[str, Any]]:
    """
    Build and execute the LangGraph StateGraph for multi-agent code review.

    Constructs a graph with four specialized reviewer nodes. Security, performance,
    and style always run. Testing runs conditionally based on skip_testing.

    Args:
        code: Source code to review
        model: Ollama model name
        ollama_url: Ollama server URL
        file_path: Optional file path for context
        context: Optional additional context
        skip_testing: When True, the conditional edge routes to END instead of testing

    Returns:
        List of review result dicts from all agents that ran
    """
    builder = StateGraph(ReviewState)

    builder.add_node("security", make_reviewer("security"))
    builder.add_node("performance", make_reviewer("performance"))
    builder.add_node("style", make_reviewer("style"))
    builder.add_node("testing", make_reviewer("testing"))

    builder.add_edge(START, "security")
    builder.add_edge(START, "performance")
    builder.add_edge(START, "style")
    builder.add_conditional_edges(START, should_run_testing, {"run": "testing", "skip": END})

    builder.add_edge("security", END)
    builder.add_edge("performance", END)
    builder.add_edge("style", END)
    builder.add_edge("testing", END)

    graph = builder.compile()

    initial_state: ReviewState = {
        "code": code,
        "file_path": file_path,
        "context": context,
        "model": model,
        "ollama_url": ollama_url,
        "skip_testing": skip_testing,
        "reviews": [],
    }

    final_state = await graph.ainvoke(initial_state)
    return final_state["reviews"]


@task
async def build_report(
    reviews: list[dict[str, Any]],
    execution_id: str,
    execution_time: float,
    model: str,
    code_length: int,
    file_path: str | None,
) -> dict[str, Any]:
    """
    Aggregate reviews from all agents into a structured summary report.

    Counts findings by severity, extracts top recommendations from critical
    and high severity issues, and builds the by_agent breakdown.

    Args:
        reviews: List of agent review results from run_langgraph_review
        execution_id: Flux execution ID for traceability
        execution_time: Total review time in seconds
        model: Ollama model used
        code_length: Character count of reviewed code
        file_path: Optional file path that was reviewed

    Returns:
        Structured review report with summary, by_agent breakdown, and recommendations
    """
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
            for finding in review.get("findings", []):
                severity = finding.get("severity", "low")
                counts[severity] = counts.get(severity, 0) + 1
                all_findings.append({"agent": agent_name, **finding})
            for suggestion in review.get("suggestions", []):
                all_suggestions.append({"agent": agent_name, **suggestion})
        else:
            agents_failed += 1

    recommendations = []
    for finding in all_findings:
        severity = finding.get("severity", "low")
        if severity in ("critical", "high"):
            issue = finding.get("issue", "Unknown issue")
            line = finding.get("line")
            line_info = f" (line {line})" if line else ""
            recommendations.append(f"{severity.upper()}: {issue}{line_info}")

    return {
        "summary": {
            "total_issues": sum(counts.values()),
            "critical": counts.get("critical", 0),
            "high": counts.get("high", 0),
            "medium": counts.get("medium", 0),
            "low": counts.get("low", 0),
            "agents_completed": agents_completed,
            "agents_failed": agents_failed,
            "execution_time": execution_time,
        },
        "by_agent": by_agent,
        "recommendations": recommendations[:10],
        "test_suggestions_count": len(all_suggestions),
        "metadata": {
            "execution_id": execution_id,
            "model_used": model,
            "code_length": code_length,
            "file_path": file_path,
        },
    }


# =============================================================================
# Main Workflow
# =============================================================================


@workflow
async def multi_agent_code_review_langgraph(ctx: ExecutionContext[dict[str, Any]]):
    """
    Multi-agent code review system using LangGraph StateGraph + Flux orchestration.

    LangGraph manages the graph execution with typed state and conditional edges.
    Flux wraps the entire graph as a durable, retriable task with execution tracing.

    The conditional edge on `skip_testing` demonstrates LangGraph's routing capabilities:
    - False (default): security + performance + style + testing agents all run
    - True: only security + performance + style agents run

    Input format:
    {
        "code": "Source code as string (required)",
        "file_path": "Optional file path for context",
        "context": "Optional additional context",
        "model": "llama3.2",                    # Optional, default: llama3.2
        "ollama_url": "http://localhost:11434",  # Optional
        "skip_testing": false                   # Optional, default: false
    }

    Returns:
        Comprehensive review report with findings from all agents
    """
    start_time = datetime.now()
    input_data = ctx.input or {}

    code = input_data.get("code")
    if not code:
        return {"error": "No code provided", "execution_id": ctx.execution_id}

    model = input_data.get("model", "llama3.2")
    ollama_url = input_data.get("ollama_url", "http://localhost:11434")
    file_path = input_data.get("file_path")
    context = input_data.get("context")
    skip_testing = input_data.get("skip_testing", False)

    reviews = await run_langgraph_review(
        code,
        model,
        ollama_url,
        file_path,
        context,
        skip_testing,
    )

    end_time = datetime.now()
    execution_time = (end_time - start_time).total_seconds()

    return await build_report(
        reviews,
        ctx.execution_id,
        execution_time,
        model,
        len(code),
        file_path,
    )


if __name__ == "__main__":  # pragma: no cover
    print("=" * 80)
    print("Multi-Agent Code Review Demo - LangGraph + Flux")
    print("=" * 80 + "\n")

    test_code = '''
def login_user(username, password):
    """Authenticate user - INSECURE EXAMPLE"""
    query = f"SELECT * FROM users WHERE username=\'{username}\' AND password=\'{password}\'"
    result = db.execute(query)
    return result.fetchone()

def process_file(user_input):
    """Process uploaded file - INSECURE EXAMPLE"""
    file_path = f"/uploads/{user_input}"
    with open(file_path, \'r\') as f:
        return f.read()
'''

    try:
        print("Reviewing code with all agents (security, performance, style, testing)...\n")
        result = multi_agent_code_review_langgraph.run(
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

        print("Review Summary (all 4 agents):")
        print(f"  Total Issues: {summary.get('total_issues', 0)}")
        print(f"    Critical: {summary.get('critical', 0)}")
        print(f"    High: {summary.get('high', 0)}")
        print(f"    Medium: {summary.get('medium', 0)}")
        print(f"    Low: {summary.get('low', 0)}")
        print(f"  Agents Completed: {summary.get('agents_completed', 0)}")
        print(f"  Execution Time: {summary.get('execution_time', 0):.1f}s\n")

        print("Top Recommendations:")
        for i, rec in enumerate(output.get("recommendations", [])[:5], 1):
            print(f"  {i}. {rec}")

        print("\n" + "-" * 80)
        print("Now running with skip_testing=True (conditional edge demo)...\n")

        result_no_test = multi_agent_code_review_langgraph.run(
            {
                "code": test_code,
                "file_path": "auth.py",
                "model": "llama3.2",
                "skip_testing": True,
            },
        )

        if result_no_test.has_failed:
            raise Exception(f"Review failed: {result_no_test.output}")

        output_no_test = result_no_test.output
        summary_no_test = output_no_test.get("summary", {})

        print("Review Summary (3 agents, testing skipped):")
        print(f"  Total Issues: {summary_no_test.get('total_issues', 0)}")
        print(f"  Agents Completed: {summary_no_test.get('agents_completed', 0)}")
        agents_run = list(output_no_test.get("by_agent", {}).keys())
        print(f"  Agents Run: {agents_run}")

        print("\n" + "=" * 80)
        print("Multi-agent LangGraph code review completed successfully!")
        print("LangGraph StateGraph managed conditional edge routing!")
        print("Flux provided durable execution with retry/timeout!")
        print("=" * 80)

    except Exception as e:
        print(f"Error: {e}")
        print("\nMake sure:")
        print("1. Ollama is running: ollama serve")
        print("2. Model is available: ollama pull llama3.2")
        print("3. Flux server is running: poetry run flux start server")
        print("4. Flux worker is running: poetry run flux start worker worker-1")
        print("5. Dependencies installed: pip install langchain-core langchain-ollama langgraph")
