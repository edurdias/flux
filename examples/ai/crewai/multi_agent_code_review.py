"""
Multi-Agent Code Review System using CrewAI + Ollama (Local LLM).

This example demonstrates a multi-agent code review system where CrewAI manages
role-based specialist agents with sequential process execution, while Flux provides
workflow durability, retries, scheduling, and execution tracing.

What CrewAI handles:
- Role-based specialist agents (security, performance, style, testing)
- Agent definitions with role, goal, and backstory
- Task definitions with descriptions and expected output formats
- Sequential process execution (each agent builds on prior context)

What Flux handles:
- Durable workflow execution with retries and timeouts
- Execution history and tracing
- Scheduling and worker coordination
- Report aggregation as a separate task

Compared to examples/ai/multi_agent_code_review_ollama.py (pure Flux + Graph DAG):
this variant delegates multi-agent orchestration to CrewAI's Crew with Process.sequential,
showing how CrewAI's role-based agents integrate with Flux workflow orchestration.

Dependencies:
    pip install crewai litellm

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a capable model: ollama pull llama3.2
    3. Start Ollama service: ollama serve
    4. Start Flux server: poetry run flux start server
    5. Start Flux worker: poetry run flux start worker worker-1

Usage:
    # Review a simple function
    flux workflow run multi_agent_code_review_crewai '{
        "code": "def unsafe_query(user_input):\\n    return db.execute(f\\"SELECT * FROM users WHERE name={user_input}\\")"
    }'

    # Review with specific model
    flux workflow run multi_agent_code_review_crewai '{
        "code": "def process(data):\\n    return [x*2 for x in data]",
        "model": "llama3.2"
    }'

    # Review with context and file path
    flux workflow run multi_agent_code_review_crewai '{
        "code": "...",
        "file_path": "api/auth.py",
        "context": "Authentication endpoint for user login"
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
            "agents_failed": 0,
            "execution_time": 15.2
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
import re
from datetime import datetime
from typing import Any

from crewai import Agent, Crew, LLM, Process, Task

from flux import ExecutionContext, task, workflow


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


# =============================================================================
# CrewAI Agent and Task Builders
# =============================================================================


def _create_agents(llm: LLM) -> dict[str, Agent]:
    return {
        "security": Agent(
            role="Security Code Reviewer",
            goal=(
                "Find security vulnerabilities including SQL injection, XSS, "
                "authentication issues, hardcoded secrets, input validation problems, "
                "command injection, path traversal, insecure cryptography, race conditions, "
                "and sensitive data exposure."
            ),
            backstory=(
                "You are an expert security auditor with years of experience in "
                "penetration testing and secure code review. You have found critical "
                "vulnerabilities in production systems and specialize in identifying "
                "subtle security flaws that automated tools miss."
            ),
            llm=llm,
            verbose=False,
        ),
        "performance": Agent(
            role="Performance Optimization Expert",
            goal=(
                "Identify performance issues including algorithm inefficiency, "
                "unnecessary loops, inefficient data structures, memory leaks, "
                "database query problems, missing caching, redundant computations, "
                "I/O bottlenecks, and blocking operations."
            ),
            backstory=(
                "You are a performance engineering specialist who has optimized "
                "high-traffic systems serving millions of requests. You focus on "
                "impactful optimizations rather than micro-optimizations and have "
                "deep expertise in algorithmic complexity and system bottlenecks."
            ),
            llm=llm,
            verbose=False,
        ),
        "style": Agent(
            role="Code Quality and Style Expert",
            goal=(
                "Review code for readability, naming conventions, organization, "
                "documentation, DRY violations, function complexity, magic numbers, "
                "error handling quality, type hints, and PEP 8 compliance."
            ),
            backstory=(
                "You are a senior software engineer known for writing clean, "
                "maintainable code. You have mentored dozens of developers and "
                "established coding standards for large engineering organizations. "
                "You focus on practical improvements that affect maintainability."
            ),
            llm=llm,
            verbose=False,
        ),
        "testing": Agent(
            role="Testing and Quality Assurance Expert",
            goal=(
                "Suggest critical test cases, edge cases, error conditions, "
                "integration test scenarios, mock requirements, and areas with "
                "missing test coverage."
            ),
            backstory=(
                "You are a QA architect who has built comprehensive test suites "
                "for complex systems. You excel at identifying edge cases and "
                "failure modes that developers overlook, and you prioritize tests "
                "that provide real value and catch likely bugs."
            ),
            llm=llm,
            verbose=False,
        ),
    }


def _create_tasks(
    agents: dict[str, Agent],
    code: str,
    context_str: str,
) -> list[Task]:
    security_task = Task(
        description=f"""{context_str}

Review this code for security vulnerabilities:
```
{code}
```

For each finding, provide severity (critical/high/medium/low), issue description,
line number if identifiable, and a specific recommendation to fix it.

Respond as a JSON array of findings. Each finding must have:
- severity: "critical" | "high" | "medium" | "low"
- issue: string
- line: number | null
- recommendation: string""",
        expected_output="A JSON array of security findings, each with severity, issue, line, and recommendation fields.",
        agent=agents["security"],
    )

    performance_task = Task(
        description=f"""{context_str}

Review this code for performance issues:
```
{code}
```

For each finding, provide severity (high/medium/low), issue description,
line number if identifiable, and a specific optimization recommendation.

Respond as a JSON array of findings. Each finding must have:
- severity: "high" | "medium" | "low"
- issue: string
- line: number | null
- recommendation: string""",
        expected_output="A JSON array of performance findings, each with severity, issue, line, and recommendation fields.",
        agent=agents["performance"],
    )

    style_task = Task(
        description=f"""{context_str}

Review this code for style and quality issues:
```
{code}
```

For each finding, provide severity (medium/low), issue description,
line number if identifiable, and a specific improvement recommendation.

Respond as a JSON array of findings. Each finding must have:
- severity: "medium" | "low"
- issue: string
- line: number | null
- recommendation: string""",
        expected_output="A JSON array of style findings, each with severity, issue, line, and recommendation fields.",
        agent=agents["style"],
    )

    testing_task = Task(
        description=f"""{context_str}

Analyze this code and suggest test cases:
```
{code}
```

For each suggestion, provide priority (high/medium/low), test case description,
what it verifies, and why it is important.

Respond as a JSON array of suggestions. Each suggestion must have:
- priority: "high" | "medium" | "low"
- test_case: string
- verifies: string
- importance: string""",
        expected_output="A JSON array of testing suggestions, each with priority, test_case, verifies, and importance fields.",
        agent=agents["testing"],
    )

    return [security_task, performance_task, style_task, testing_task]


# =============================================================================
# Flux Tasks
# =============================================================================


def _parse_crew_output(raw_output: str) -> list[dict[str, Any]]:
    """
    Parse the sequential crew output into per-agent review results.

    CrewAI sequential output contains results from each agent in order.
    We extract JSON arrays from the final output and map them to agents.
    """
    agent_names = ["security", "performance", "style", "testing"]
    results: list[dict[str, Any]] = []

    try:
        findings = parse_json_response(raw_output)

        if findings and all("priority" in f for f in findings):
            results.append(
                {
                    "agent": "testing",
                    "status": "success",
                    "suggestions": findings,
                },
            )
        elif findings and all("test_case" in f for f in findings):
            results.append(
                {
                    "agent": "testing",
                    "status": "success",
                    "suggestions": findings,
                },
            )
        else:
            results.append(
                {
                    "agent": agent_names[0],
                    "status": "success",
                    "findings": findings,
                },
            )
    except json.JSONDecodeError:
        pass

    return results


def _extract_task_results(
    crew_output: Any,
    agent_names: list[str],
) -> list[dict[str, Any]]:
    """
    Extract per-task results from CrewAI crew output.

    Uses tasks_output attribute when available, falling back to parsing
    the raw string output.
    """
    results: list[dict[str, Any]] = []

    tasks_output = getattr(crew_output, "tasks_output", None)
    if tasks_output and len(tasks_output) == len(agent_names):
        for agent_name, task_output in zip(agent_names, tasks_output):
            raw = str(task_output)
            try:
                parsed = parse_json_response(raw)
                if agent_name == "testing":
                    results.append(
                        {
                            "agent": agent_name,
                            "status": "success",
                            "suggestions": parsed,
                        },
                    )
                else:
                    results.append(
                        {
                            "agent": agent_name,
                            "status": "success",
                            "findings": parsed,
                        },
                    )
            except json.JSONDecodeError:
                entry: dict[str, Any] = {
                    "agent": agent_name,
                    "status": "parse_error",
                    "error": f"Failed to parse {agent_name} output as JSON",
                }
                if agent_name == "testing":
                    entry["suggestions"] = []
                else:
                    entry["findings"] = []
                results.append(entry)
        return results

    raw_output = str(crew_output)
    parsed_results = _parse_crew_output(raw_output)
    if parsed_results:
        return parsed_results

    for agent_name in agent_names:
        entry = {
            "agent": agent_name,
            "status": "parse_error",
            "error": "Could not extract results from crew output",
        }
        if agent_name == "testing":
            entry["suggestions"] = []
        else:
            entry["findings"] = []
        results.append(entry)

    return results


@task.with_options(retry_max_attempts=2, timeout=300)
async def run_crewai_review(
    code: str,
    model: str,
    ollama_url: str,
    file_path: str | None = None,
    context: str | None = None,
) -> list[dict[str, Any]]:
    """
    Build and execute the CrewAI Crew for multi-agent code review.

    Creates four specialist agents (security, performance, style, testing) and
    runs them sequentially via CrewAI's Process.sequential. Each agent reviews
    the code from its area of expertise.

    Args:
        code: Source code to review
        model: Ollama model name
        ollama_url: Ollama server URL
        file_path: Optional file path for context
        context: Optional additional context

    Returns:
        List of review result dicts from all agents
    """
    try:
        llm = LLM(model=f"ollama/{model}", base_url=ollama_url)
        agents = _create_agents(llm)
        context_str = _build_context_str(file_path, context)
        tasks = _create_tasks(agents, code, context_str)

        crew = Crew(
            agents=list(agents.values()),
            tasks=tasks,
            process=Process.sequential,
            verbose=False,
        )

        crew_output = crew.kickoff()

        agent_names = ["security", "performance", "style", "testing"]
        return _extract_task_results(crew_output, agent_names)

    except Exception as e:
        error_msg = str(e)
        if "connection" in error_msg.lower() or "refused" in error_msg.lower():
            raise RuntimeError(
                f"CrewAI review failed: {error_msg}. "
                "Make sure Ollama is running (ollama serve) and the model is "
                "available (ollama pull llama3.2).",
            ) from e
        raise RuntimeError(
            f"CrewAI review failed: {error_msg}. "
            "Make sure Ollama is running and crewai is installed (pip install crewai).",
        ) from e


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
        reviews: List of agent review results from run_crewai_review
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
async def multi_agent_code_review_crewai(ctx: ExecutionContext[dict[str, Any]]):
    """
    Multi-agent code review system using CrewAI + Flux orchestration.

    CrewAI manages role-based specialist agents with sequential process execution.
    Flux wraps the crew as a durable, retriable task with execution tracing.

    Four specialist agents run sequentially, each building on prior context:
    1. Security: vulnerabilities, injection, authentication flaws
    2. Performance: algorithm efficiency, bottlenecks, optimization
    3. Style: readability, naming, PEP 8 compliance
    4. Testing: test case suggestions, edge cases, coverage gaps

    Input format:
    {
        "code": "Source code as string (required)",
        "file_path": "Optional file path for context",
        "context": "Optional additional context",
        "model": "llama3.2",                    # Optional, default: llama3.2
        "ollama_url": "http://localhost:11434"   # Optional
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

    reviews = await run_crewai_review(
        code,
        model,
        ollama_url,
        file_path,
        context,
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
    print("Multi-Agent Code Review Demo - CrewAI + Flux")
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
        print("Reviewing code with CrewAI agents (security, performance, style, testing)...\n")
        result = multi_agent_code_review_crewai.run(
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
        print(f"  Agents Completed: {summary.get('agents_completed', 0)}/4")
        print(f"  Execution Time: {summary.get('execution_time', 0):.1f}s\n")

        print("Top Recommendations:")
        for i, rec in enumerate(output.get("recommendations", [])[:5], 1):
            print(f"  {i}. {rec}")

        print(f"\n  Test Suggestions: {output.get('test_suggestions_count', 0)}")

        print("\n" + "=" * 80)
        print("Multi-agent CrewAI code review completed successfully!")
        print("CrewAI managed role-based agents with sequential execution!")
        print("Flux provided durable execution with retry/timeout!")
        print("=" * 80)

    except Exception as e:
        print(f"Error: {e}")
        print("\nMake sure:")
        print("1. Ollama is running: ollama serve")
        print("2. Model is available: ollama pull llama3.2")
        print("3. Flux server is running: poetry run flux start server")
        print("4. Flux worker is running: poetry run flux start worker worker-1")
        print("5. Dependencies installed: pip install crewai")
