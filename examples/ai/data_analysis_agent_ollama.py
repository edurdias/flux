"""
Data Analysis Agent using Ollama (Local LLM) with Pandas.

This example demonstrates how to build an AI agent that can analyze structured data
(CSV, JSON) using pandas for data manipulation and Ollama for natural language insights.

Use cases:
- Business analytics and reporting
- Data exploration and discovery
- Automated insights generation
- Interactive data Q&A

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a model: ollama pull llama3.2
    3. Start Ollama service: ollama serve
    4. Install pandas: pip install pandas

Usage:
    # Analyze sample sales data (included)
    flux workflow run data_analysis_agent_ollama '{"file_path": "examples/ai/sample_data/sales_data.csv", "question": "What are the top 5 products by revenue?"}'

    # Ask follow-up questions
    flux workflow resume data_analysis_agent_ollama <execution_id> '{"question": "Which products have declining sales trends?"}'

    # Analyze your own data
    flux workflow run data_analysis_agent_ollama '{"file_path": "/path/to/your/data.csv", "question": "Show me a summary of this data"}'

    # Use a different model
    flux workflow run data_analysis_agent_ollama '{"file_path": "examples/ai/sample_data/sales_data.csv", "question": "Analyze seasonal trends", "model": "qwen2.5:3b"}'
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
from ollama import AsyncClient

from flux import ExecutionContext, task, workflow
from flux.tasks import pause


@task
async def load_data(file_path: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    Load data from CSV or JSON file and generate metadata.

    Args:
        file_path: Path to the data file (CSV or JSON)

    Returns:
        Tuple of (DataFrame, metadata dict with statistics)
    """
    try:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # Load based on file extension
        if path.suffix.lower() == ".csv":
            df = pd.read_csv(file_path)
        elif path.suffix.lower() == ".json":
            df = pd.read_json(file_path)
        else:
            raise ValueError(f"Unsupported file format: {path.suffix}. Use .csv or .json")

        # Generate metadata about the dataset
        metadata = {
            "file_name": path.name,
            "row_count": len(df),
            "column_count": len(df.columns),
            "columns": list(df.columns),
            "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
            "missing_values": df.isnull().sum().to_dict(),
            "sample_rows": df.head(3).to_dict(orient="records"),
        }

        # Add basic statistics for numeric columns
        numeric_cols = df.select_dtypes(include=["number"]).columns
        if len(numeric_cols) > 0:
            metadata["statistics"] = df[numeric_cols].describe().to_dict()

        return df, metadata

    except Exception as e:
        raise RuntimeError(f"Failed to load data: {str(e)}") from e


@task
async def analyze_data(df: pd.DataFrame, question: str) -> str:
    """
    Perform data analysis based on the question.

    This task uses pandas to compute statistics, trends, and insights
    that will be provided to the LLM for natural language interpretation.

    Args:
        df: DataFrame to analyze
        question: User's question about the data

    Returns:
        JSON string with analysis results
    """
    try:
        analysis_results = {}

        # Basic statistics
        numeric_cols = df.select_dtypes(include=["number"]).columns
        if len(numeric_cols) > 0:
            analysis_results["basic_stats"] = df[numeric_cols].describe().to_dict()

        # Top/bottom records by numeric columns
        for col in numeric_cols[:3]:  # Limit to first 3 numeric columns
            analysis_results[f"top_5_by_{col}"] = df.nlargest(5, col)[
                [col] + [c for c in df.columns if c != col][:2]
            ].to_dict(orient="records")

        # Value counts for categorical columns (top 10)
        categorical_cols = df.select_dtypes(include=["object"]).columns
        for col in categorical_cols[:3]:  # Limit to first 3 categorical columns
            value_counts = df[col].value_counts().head(10).to_dict()
            analysis_results[f"{col}_distribution"] = value_counts

        # Correlation matrix for numeric columns
        if len(numeric_cols) > 1:
            corr_matrix = df[numeric_cols].corr().to_dict()
            analysis_results["correlations"] = corr_matrix

        return json.dumps(analysis_results, indent=2, default=str)

    except Exception as e:
        raise RuntimeError(f"Failed to analyze data: {str(e)}") from e


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=60)
async def call_ollama_for_insights(
    question: str,
    metadata: dict[str, Any],
    analysis_results: str,
    conversation_history: list[dict[str, str]],
    model: str,
    ollama_url: str,
) -> str:
    """
    Call Ollama to generate natural language insights from the data analysis.

    Args:
        question: User's question
        metadata: Dataset metadata
        analysis_results: JSON string with analysis results
        conversation_history: Previous conversation turns
        model: Ollama model to use
        ollama_url: Ollama server URL

    Returns:
        LLM response with insights
    """
    try:
        client = AsyncClient(host=ollama_url)

        # Build context about the data
        data_context = f"""
You are a data analyst AI assistant. You have access to a dataset with the following characteristics:

Dataset Information:
- File: {metadata['file_name']}
- Rows: {metadata['row_count']:,}
- Columns: {metadata['column_count']}
- Column names: {', '.join(metadata['columns'])}
- Data types: {json.dumps(metadata['dtypes'], indent=2)}

Sample Data (first 3 rows):
{json.dumps(metadata['sample_rows'], indent=2)}

Analysis Results:
{analysis_results}

Your task is to answer the user's question based on this data analysis. Be specific, cite numbers, and provide actionable insights. If the data doesn't fully answer the question, acknowledge limitations.
"""

        # Build messages
        messages = [{"role": "system", "content": data_context}]
        messages.extend(conversation_history)
        messages.append({"role": "user", "content": question})

        # Call Ollama
        response = await client.chat(model=model, messages=messages)

        return response["message"]["content"]

    except Exception as e:
        raise RuntimeError(
            f"Failed to call Ollama API: {str(e)}. "
            "Make sure Ollama is running (ollama serve) and the model is available.",
        ) from e


@task
async def process_question(
    df: pd.DataFrame,
    metadata: dict[str, Any],
    question: str,
    conversation_history: list[dict[str, str]],
    model: str,
    ollama_url: str,
) -> tuple[list[dict[str, str]], str]:
    """
    Process a single question about the data.

    Args:
        df: DataFrame to analyze
        metadata: Dataset metadata
        question: User's question
        conversation_history: Previous conversation
        model: Ollama model
        ollama_url: Ollama server URL

    Returns:
        Tuple of (updated conversation history, assistant response)
    """
    # Analyze the data
    analysis_results = await analyze_data(df, question)

    # Get LLM insights
    assistant_response = await call_ollama_for_insights(
        question=question,
        metadata=metadata,
        analysis_results=analysis_results,
        conversation_history=conversation_history,
        model=model,
        ollama_url=ollama_url,
    )

    # Update conversation history
    conversation_history.append({"role": "user", "content": question})
    conversation_history.append({"role": "assistant", "content": assistant_response})

    return conversation_history, assistant_response


@workflow.with_options(name="data_analysis_agent_ollama")
async def data_analysis_agent_ollama(ctx: ExecutionContext[dict[str, Any]]):
    """
    An AI agent that analyzes structured data using pandas and Ollama.

    This workflow loads a CSV or JSON file, performs statistical analysis,
    and answers natural language questions about the data. It supports
    multi-turn conversations for interactive data exploration.

    Initial Input format:
    {
        "file_path": "path/to/data.csv",  # Required: CSV or JSON file
        "question": "What are the key insights?",  # Required: initial question
        "model": "llama3.2",  # Optional: Ollama model (default: llama3.2)
        "max_turns": 10,  # Optional: max conversation turns
        "ollama_url": "http://localhost:11434"  # Optional: Ollama server URL
    }

    Resume Input format:
    {
        "question": "Tell me more about..."  # Next question
    }

    Returns:
    {
        "status": "success" | "conversation_ended",
        "data_summary": {...},  # Dataset metadata
        "conversation_history": [...],  # Full conversation
        "turn_count": 3,
        "execution_id": "..."
    }
    """
    # Get initial configuration
    initial_input = ctx.input or {}
    file_path = initial_input.get("file_path")
    first_question = initial_input.get("question")
    model = initial_input.get("model", "llama3.2")
    max_turns = initial_input.get("max_turns", 10)
    ollama_url = initial_input.get("ollama_url", "http://localhost:11434")

    # Validate required inputs
    if not file_path:
        return {"error": "No file_path provided in input", "execution_id": ctx.execution_id}
    if not first_question:
        return {"error": "No question provided in input", "execution_id": ctx.execution_id}

    # Load the data (once at the start)
    df, metadata = await load_data(file_path)

    # Initialize conversation history
    conversation_history: list[dict[str, str]] = []

    # Process first question
    conversation_history, first_response = await process_question(
        df=df,
        metadata=metadata,
        question=first_question,
        conversation_history=conversation_history,
        model=model,
        ollama_url=ollama_url,
    )

    # Return after first question with option to continue
    initial_result = {
        "status": "success",
        "response": first_response,
        "data_summary": {
            "file_name": metadata["file_name"],
            "rows": metadata["row_count"],
            "columns": metadata["column_count"],
        },
        "turn_count": 1,
        "execution_id": ctx.execution_id,
        "message": "Ask follow-up questions using: flux workflow resume",
    }

    # Main conversation loop - pause between turns
    for turn in range(1, max_turns):
        # Pause and wait for next question
        resume_input = await pause(f"waiting_for_question_turn_{turn}")

        # Get next question from resume input
        next_question = resume_input.get("question", "") if resume_input else ""
        if not next_question:
            return {
                "status": "conversation_ended",
                "reason": "No question provided",
                "data_summary": initial_result["data_summary"],
                "conversation_history": conversation_history,
                "turn_count": len(conversation_history) // 2,
                "execution_id": ctx.execution_id,
            }

        # Process next question
        conversation_history, next_response = await process_question(
            df=df,
            metadata=metadata,
            question=next_question,
            conversation_history=conversation_history,
            model=model,
            ollama_url=ollama_url,
        )

    # Max turns reached
    return {
        "status": "conversation_ended",
        "reason": f"Maximum turns ({max_turns}) reached",
        "data_summary": initial_result["data_summary"],
        "conversation_history": conversation_history,
        "turn_count": len(conversation_history) // 2,
        "execution_id": ctx.execution_id,
    }


if __name__ == "__main__":  # pragma: no cover
    import asyncio

    # Example: Analyze sample sales data
    sample_file = "examples/ai/sample_data/sales_data.csv"
    initial_input = {
        "file_path": sample_file,
        "question": "What are the top 5 products by revenue?",
        "model": "llama3.2",
        "max_turns": 3,
    }

    async def run_example():
        try:
            # Check if sample file exists
            if not Path(sample_file).exists():
                print(f"Sample file not found: {sample_file}")
                print("Create sample data first by running:")
                print("  python examples/ai/create_sample_data.py")
                return

            # Turn 1
            result = data_analysis_agent_ollama.run(initial_input)
            if result.has_failed:
                raise Exception(f"Workflow failed: {result.output}")

            print(f"\nTurn 1 Response:\n{result.output.get('response', '')}\n")

            # Turn 2
            result = data_analysis_agent_ollama.resume(
                result.execution_id,
                {"question": "Which products have the highest profit margins?"},
            )
            if result.has_failed:
                raise Exception(f"Workflow failed: {result.output}")

            print(f"\nTurn 2 Response:\n{result.output.get('response', '')}\n")

            # Turn 3
            result = data_analysis_agent_ollama.resume(
                result.execution_id,
                {"question": "Are there any seasonal trends in the sales?"},
            )
            if result.has_failed:
                raise Exception(f"Workflow failed: {result.output}")

            print(f"\nTurn 3 Response:\n{result.output.get('response', '')}\n")

            # Display full conversation
            print("\n" + "=" * 60)
            print("Full Conversation History:")
            print("=" * 60)
            for msg in result.output.get("conversation_history", []):
                role = msg["role"].upper()
                content = msg["content"]
                print(f"\n{role}:\n{content}")

        except Exception as e:
            print(f"Error: {e}")
            print("\nMake sure:")
            print("  1. Ollama is running: ollama serve")
            print("  2. You have pulled a model: ollama pull llama3.2")
            print("  3. Sample data exists: python examples/ai/create_sample_data.py")

    asyncio.run(run_example())
