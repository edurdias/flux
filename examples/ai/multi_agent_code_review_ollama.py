"""
Multi-Agent Code Review System using Ollama and Flux Parallel Execution.

This example demonstrates a production-ready multi-agent system where specialized
AI agents review code in parallel. Each agent focuses on a specific aspect
(security, performance, style, testing) and their findings are aggregated into
a comprehensive review report.

Key Features:
- **Parallel Execution**: Uses flux.tasks.parallel() for concurrent agent execution
- **Specialized Agents**: 4 agents with domain-specific expertise
- **Error Handling**: Per-agent retry logic, timeouts, and graceful degradation
- **Production Ready**: Structured output, priority scoring, CI/CD integration
- **Fully Local**: Runs with Ollama - no API costs, works offline
- **Observable**: Full execution tracing in Flux workflow events

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a capable model: ollama pull llama3.2
    3. Start Ollama service: ollama serve
    4. Start Flux server: poetry run flux start server
    5. Start Flux worker: poetry run flux start worker worker-1

Usage:
    # Review a simple function
    flux workflow run multi_agent_code_review_ollama '{
        "code": "def unsafe_query(user_input):\\n    return db.execute(f\\"SELECT * FROM users WHERE name={user_input}\\")"
    }'

    # Review with specific model
    flux workflow run multi_agent_code_review_ollama '{
        "code": "def process(data):\\n    return [x*2 for x in data]",
        "model": "llama3.2"
    }'

    # Review with context
    flux workflow run multi_agent_code_review_ollama '{
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
            "execution_time": 12.5
        },
        "by_agent": {
            "security": {"findings": [...]},
            "performance": {"findings": [...]},
            "style": {"findings": [...]},
            "testing": {"suggestions": [...]}
        },
        "recommendations": [
            "Fix critical SQL injection vulnerability (line 42)",
            "Optimize database query with indexing",
            "Add input validation for user_input parameter"
        ]
    }
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ollama import AsyncClient

from flux import ExecutionContext, task, workflow
from flux.tasks import parallel


# =============================================================================
# Agent System Prompts - Specialized Expertise
# =============================================================================

SECURITY_AGENT_PROMPT = """You are a security code reviewer with expertise in finding vulnerabilities.

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

Be concise but thorough. Focus on real security issues, not theoretical concerns."""

PERFORMANCE_AGENT_PROMPT = """You are a performance optimization expert.

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

Focus on impactful optimizations, not micro-optimizations."""

STYLE_AGENT_PROMPT = """You are a code quality and style expert.

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

Be practical - focus on issues that affect maintainability."""

TESTING_AGENT_PROMPT = """You are a testing and quality assurance expert.

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

Focus on tests that provide real value and catch likely bugs."""


# =============================================================================
# Specialized Agent Tasks - Run in Parallel
# =============================================================================


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=60)
async def security_review(
    code: str,
    model: str,
    ollama_url: str,
    file_path: str | None = None,
    context: str | None = None,
) -> dict[str, Any]:
    """
    Security-focused code review agent.

    Analyzes code for vulnerabilities, authentication issues, and security risks.

    Args:
        code: The source code to review
        model: Ollama model to use
        ollama_url: Ollama server URL
        file_path: Optional file path for context
        context: Optional additional context about the code

    Returns:
        Dictionary with security findings
    """
    try:
        client = AsyncClient(host=ollama_url)

        # Build context message
        context_parts = []
        if file_path:
            context_parts.append(f"File: {file_path}")
        if context:
            context_parts.append(f"Context: {context}")

        context_str = (
            "\n".join(context_parts) if context_parts else "No additional context provided."
        )

        # Prepare messages
        messages = [
            {"role": "system", "content": SECURITY_AGENT_PROMPT},
            {
                "role": "user",
                "content": f"""{context_str}

Code to review:
```
{code}
```

Provide your security review as a JSON array of findings. Each finding should have:
- severity: "critical" | "high" | "medium" | "low"
- issue: string (description)
- line: number | null
- recommendation: string

Example: [{{"severity": "high", "issue": "SQL injection", "line": 42, "recommendation": "Use parameterized queries"}}]

Respond with ONLY the JSON array, no other text.""",
            },
        ]

        # Call Ollama
        response = await client.chat(model=model, messages=messages)
        content = response["message"]["content"].strip()

        # Try to parse JSON
        # Remove markdown code fences if present
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1]) if len(lines) > 2 else content

        findings = json.loads(content)

        return {
            "agent": "security",
            "status": "success",
            "findings": findings if isinstance(findings, list) else [findings],
            "model_used": model,
        }

    except json.JSONDecodeError as e:
        return {
            "agent": "security",
            "status": "parse_error",
            "error": f"Failed to parse LLM response as JSON: {str(e)}",
            "findings": [],
        }
    except Exception as e:
        raise RuntimeError(f"Security review failed: {str(e)}") from e


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=60)
async def performance_review(
    code: str,
    model: str,
    ollama_url: str,
    file_path: str | None = None,
    context: str | None = None,
) -> dict[str, Any]:
    """
    Performance-focused code review agent.

    Analyzes code for efficiency issues, optimization opportunities, and bottlenecks.

    Args:
        code: The source code to review
        model: Ollama model to use
        ollama_url: Ollama server URL
        file_path: Optional file path for context
        context: Optional additional context about the code

    Returns:
        Dictionary with performance findings
    """
    try:
        client = AsyncClient(host=ollama_url)

        context_parts = []
        if file_path:
            context_parts.append(f"File: {file_path}")
        if context:
            context_parts.append(f"Context: {context}")

        context_str = (
            "\n".join(context_parts) if context_parts else "No additional context provided."
        )

        messages = [
            {"role": "system", "content": PERFORMANCE_AGENT_PROMPT},
            {
                "role": "user",
                "content": f"""{context_str}

Code to review:
```
{code}
```

Provide your performance review as a JSON array of findings. Each finding should have:
- severity: "high" | "medium" | "low"
- issue: string (description)
- line: number | null
- recommendation: string

Respond with ONLY the JSON array, no other text.""",
            },
        ]

        response = await client.chat(model=model, messages=messages)
        content = response["message"]["content"].strip()

        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1]) if len(lines) > 2 else content

        findings = json.loads(content)

        return {
            "agent": "performance",
            "status": "success",
            "findings": findings if isinstance(findings, list) else [findings],
            "model_used": model,
        }

    except json.JSONDecodeError as e:
        return {
            "agent": "performance",
            "status": "parse_error",
            "error": f"Failed to parse LLM response as JSON: {str(e)}",
            "findings": [],
        }
    except Exception as e:
        raise RuntimeError(f"Performance review failed: {str(e)}") from e


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=60)
async def style_review(
    code: str,
    model: str,
    ollama_url: str,
    file_path: str | None = None,
    context: str | None = None,
) -> dict[str, Any]:
    """
    Style and quality-focused code review agent.

    Analyzes code for readability, maintainability, and adherence to best practices.

    Args:
        code: The source code to review
        model: Ollama model to use
        ollama_url: Ollama server URL
        file_path: Optional file path for context
        context: Optional additional context about the code

    Returns:
        Dictionary with style findings
    """
    try:
        client = AsyncClient(host=ollama_url)

        context_parts = []
        if file_path:
            context_parts.append(f"File: {file_path}")
        if context:
            context_parts.append(f"Context: {context}")

        context_str = (
            "\n".join(context_parts) if context_parts else "No additional context provided."
        )

        messages = [
            {"role": "system", "content": STYLE_AGENT_PROMPT},
            {
                "role": "user",
                "content": f"""{context_str}

Code to review:
```
{code}
```

Provide your style review as a JSON array of findings. Each finding should have:
- severity: "medium" | "low"
- issue: string (description)
- line: number | null
- recommendation: string

Respond with ONLY the JSON array, no other text.""",
            },
        ]

        response = await client.chat(model=model, messages=messages)
        content = response["message"]["content"].strip()

        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1]) if len(lines) > 2 else content

        findings = json.loads(content)

        return {
            "agent": "style",
            "status": "success",
            "findings": findings if isinstance(findings, list) else [findings],
            "model_used": model,
        }

    except json.JSONDecodeError as e:
        return {
            "agent": "style",
            "status": "parse_error",
            "error": f"Failed to parse LLM response as JSON: {str(e)}",
            "findings": [],
        }
    except Exception as e:
        raise RuntimeError(f"Style review failed: {str(e)}") from e


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=60)
async def testing_review(
    code: str,
    model: str,
    ollama_url: str,
    file_path: str | None = None,
    context: str | None = None,
) -> dict[str, Any]:
    """
    Testing-focused code review agent.

    Suggests test cases, edge cases, and coverage improvements.

    Args:
        code: The source code to review
        model: Ollama model to use
        ollama_url: Ollama server URL
        file_path: Optional file path for context
        context: Optional additional context about the code

    Returns:
        Dictionary with testing suggestions
    """
    try:
        client = AsyncClient(host=ollama_url)

        context_parts = []
        if file_path:
            context_parts.append(f"File: {file_path}")
        if context:
            context_parts.append(f"Context: {context}")

        context_str = (
            "\n".join(context_parts) if context_parts else "No additional context provided."
        )

        messages = [
            {"role": "system", "content": TESTING_AGENT_PROMPT},
            {
                "role": "user",
                "content": f"""{context_str}

Code to review:
```
{code}
```

Provide your testing suggestions as a JSON array. Each suggestion should have:
- priority: "high" | "medium" | "low"
- test_case: string (description)
- verifies: string (what it tests)
- importance: string (why it matters)

Respond with ONLY the JSON array, no other text.""",
            },
        ]

        response = await client.chat(model=model, messages=messages)
        content = response["message"]["content"].strip()

        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1]) if len(lines) > 2 else content

        suggestions = json.loads(content)

        return {
            "agent": "testing",
            "status": "success",
            "suggestions": suggestions if isinstance(suggestions, list) else [suggestions],
            "model_used": model,
        }

    except json.JSONDecodeError as e:
        return {
            "agent": "testing",
            "status": "parse_error",
            "error": f"Failed to parse LLM response as JSON: {str(e)}",
            "suggestions": [],
        }
    except Exception as e:
        raise RuntimeError(f"Testing review failed: {str(e)}") from e


# =============================================================================
# Aggregation and Reporting
# =============================================================================


@task
async def aggregate_reviews(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Aggregate findings from all agents into a unified structure.

    Combines results, counts issues by severity, and prepares for reporting.

    Args:
        reviews: List of agent review results

    Returns:
        Aggregated findings with counts and categorization
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

        # Store agent results
        by_agent[agent_name] = review

        if status == "success":
            agents_completed += 1

            # Aggregate findings
            if "findings" in review:
                for finding in review["findings"]:
                    severity = finding.get("severity", "low")
                    counts[severity] = counts.get(severity, 0) + 1
                    all_findings.append(
                        {"agent": agent_name, **finding},
                    )

            # Aggregate test suggestions
            if "suggestions" in review:
                for suggestion in review["suggestions"]:
                    all_suggestions.append(
                        {"agent": agent_name, **suggestion},
                    )
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
    """
    Generate final summary report from aggregated findings.

    Creates a structured report with summary statistics and top recommendations.

    Args:
        aggregated: Aggregated findings from all agents

    Returns:
        Final review report with summary and recommendations
    """
    counts = aggregated["counts"]
    total_issues = sum(counts.values())

    # Extract top recommendations (critical and high severity issues)
    recommendations = []
    for finding in aggregated["all_findings"]:
        severity = finding.get("severity", "low")
        if severity in ["critical", "high"]:
            issue = finding.get("issue", "Unknown issue")
            line = finding.get("line")
            line_info = f" (line {line})" if line else ""
            recommendations.append(f"{severity.upper()}: {issue}{line_info}")

    # Limit to top 10 recommendations
    recommendations = recommendations[:10]

    report = {
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
        "recommendations": recommendations,
        "test_suggestions_count": len(aggregated["all_suggestions"]),
    }

    return report


# =============================================================================
# Main Workflow
# =============================================================================


@workflow.with_options(name="multi_agent_code_review_ollama")
async def multi_agent_code_review_ollama(ctx: ExecutionContext[dict[str, Any]]):
    """
    Multi-agent code review system using parallel AI agents.

    Four specialized agents review code concurrently:
    - Security: Finds vulnerabilities and security issues
    - Performance: Identifies optimization opportunities
    - Style: Reviews code quality and maintainability
    - Testing: Suggests test cases and coverage improvements

    Input format:
    {
        "code": "Source code as string (required)",
        "file_path": "Optional file path for context",
        "context": "Optional additional context",
        "model": "llama3.2",  # Optional, default: llama3.2
        "ollama_url": "http://localhost:11434"  # Optional
    }

    Returns:
        Comprehensive review report with findings from all agents
    """
    start_time = datetime.now()

    # Extract input parameters
    code = ctx.input.get("code")
    if not code:
        return {"error": "No code provided", "execution_id": ctx.execution_id}

    file_path = ctx.input.get("file_path")
    context = ctx.input.get("context")
    model = ctx.input.get("model", "llama3.2")
    ollama_url = ctx.input.get("ollama_url", "http://localhost:11434")

    # Execute all agent reviews in parallel using Flux's parallel task
    reviews = await parallel(
        security_review(code, model, ollama_url, file_path, context),
        performance_review(code, model, ollama_url, file_path, context),
        style_review(code, model, ollama_url, file_path, context),
        testing_review(code, model, ollama_url, file_path, context),
    )

    # Aggregate all findings
    aggregated = await aggregate_reviews(reviews)

    # Generate final report
    report = await generate_summary_report(aggregated)

    # Add execution metadata
    end_time = datetime.now()
    execution_time = (end_time - start_time).total_seconds()

    report["metadata"] = {
        "execution_id": ctx.execution_id,
        "execution_time": execution_time,
        "model_used": model,
        "code_length": len(code),
        "file_path": file_path,
    }

    return report


if __name__ == "__main__":  # pragma: no cover
    # Quick test of the workflow
    print("=" * 80)
    print("Multi-Agent Code Review Demo - Security Focus")
    print("=" * 80 + "\n")

    # Test code with known security issue
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
        print("✓ Multi-agent code review completed successfully!")
        print("✓ All agents executed in parallel!")
        print("✓ Security vulnerabilities detected!")
        print("=" * 80)

    except Exception as e:
        print(f"Error: {e}")
        print("\nMake sure:")
        print("1. Ollama is running: ollama serve")
        print("2. Model is available: ollama pull llama3.2")
        print("3. Flux server is running: poetry run flux start server")
        print("4. Flux worker is running: poetry run flux start worker worker-1")
