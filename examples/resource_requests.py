from __future__ import annotations

from pathlib import Path
import time
import numpy as np
import pandas as pd

from flux import ExecutionContext
from flux.domain.resource_request import ResourceRequest
from flux.task import task
from flux.utils import to_json
from flux.workflow import workflow


@task
async def load_dataset(file_path: str) -> pd.DataFrame:
    """Load a dataset from a CSV file or create a sample dataframe if file doesn't exist."""
    print(f"Loading dataset from {file_path}")
    if not Path(file_path).exists():
        print(f"File {file_path} not found. Creating a sample dataframe instead.")
        # Create a sample dataframe with numeric features and a target column
        np.random.seed(42)  # For reproducibility
        sample_size = 100
        df = pd.DataFrame(
            {
                "feature1": np.random.normal(0, 1, sample_size),
                "feature2": np.random.normal(5, 2, sample_size),
                "feature3": np.random.uniform(0, 10, sample_size),
                "categorical": np.random.choice(["A", "B", "C"], sample_size),
                "target": np.random.randint(0, 2, sample_size),  # Binary classification target
            },
        )
        return df
    return pd.read_csv(file_path)


@task
async def preprocess_data(df: pd.DataFrame) -> pd.DataFrame:
    """Preprocess the dataset (normalize, handle missing values, etc.)."""
    print("Preprocessing data...")
    # Simulate CPU-intensive preprocessing
    time.sleep(1)
    # Normalize numerical features
    for col in df.select_dtypes(include=[np.number]).columns:
        if col != "target":
            df[col] = (df[col] - df[col].mean()) / df[col].std()
    return df


@task
async def split_train_test(
    df: pd.DataFrame,
    test_size: float = 0.2,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split the dataset into training and testing sets."""
    print(f"Splitting dataset with test_size={test_size}")
    # Simple split based on random indices
    msk = np.random.rand(len(df)) < (1 - test_size)
    train = df[msk]
    test = df[~msk]
    return train, test


@task
async def train_model(train_df: pd.DataFrame) -> dict:
    """Train a simple model on the dataset."""
    print("Training model (simulating GPU usage)...")
    # Simulate GPU-intensive model training
    time.sleep(3)
    return {
        "model_type": "simple_classifier",
        "accuracy": 0.85 + np.random.random() * 0.1,
        "training_time": 3.0,
        "model_size": "10MB",
    }


@task
async def evaluate_model(model: dict, test_df: pd.DataFrame) -> dict:
    """Evaluate the trained model on test data."""
    print("Evaluating model...")
    # Simulate model evaluation
    time.sleep(1)
    return {
        **model,
        "test_accuracy": model["accuracy"] - 0.05 + np.random.random() * 0.1,
        "precision": 0.83 + np.random.random() * 0.1,
        "recall": 0.81 + np.random.random() * 0.1,
    }


@task
async def generate_visualizations(df: pd.DataFrame, model_results: dict) -> dict:
    """Generate visualizations of the data and model results."""
    print("Generating visualizations...")
    # Simulate visualization generation
    time.sleep(2)
    return {
        "plots_generated": 5,
        "data_summary": {
            "shape": df.shape,
            "columns": df.columns.tolist(),
            "numeric_stats": df.describe().to_dict(),
        },
        "model_performance": model_results,
    }


# Data processing workflow with CPU and memory requirements
@workflow.with_options(
    name="data_processing_workflow",
    requests=ResourceRequest(
        cpu=4,
        memory="8Gi",
        packages=["pandas>=1.3.0", "numpy"],
    ),
)
async def data_processing_workflow(ctx: ExecutionContext[dict[str, str]]):
    """Process data with specific CPU and memory requirements."""

    if ctx.requests:
        print(
            f"Resource requests: CPU={ctx.requests.cpu}, Memory={ctx.requests.memory}",
        )

    df = await load_dataset(ctx.input["data_path"])
    processed_df = await preprocess_data(df)
    train_df, test_df = await split_train_test(processed_df)
    return {
        "train_data": train_df,
        "test_data": test_df,
        "data_stats": {
            "total_rows": len(df),
            "train_rows": len(train_df),
            "test_rows": len(test_df),
        },
    }


# Model training workflow with GPU requirements
@workflow.with_options(
    name="model_training_workflow",
    requests=ResourceRequest.with_gpu(1),
)
async def model_training_workflow(ctx: ExecutionContext[dict]):
    """Train ML model with GPU requirements."""
    if ctx.requests:
        print(f"Resource requests: GPU={ctx.requests.gpu}")
    train_df = ctx.input["train_data"]
    test_df = ctx.input["test_data"]

    model = await train_model(train_df)
    evaluation = await evaluate_model(model, test_df)
    return evaluation


# Visualization workflow with specific package requirements
@workflow.with_options(
    name="visualization_workflow",
    requests=ResourceRequest.with_packages(
        ["matplotlib>=3.5.0", "seaborn>=0.11.0"],
    ),
)
async def visualization_workflow(ctx: ExecutionContext[dict]):
    """Generate visualizations with specific package requirements."""
    if ctx.requests:
        print(f"Resource requests: {ctx.requests.packages}")
    df = pd.concat([ctx.input["train_data"], ctx.input["test_data"]])
    model_results = ctx.input["model_results"]

    visualizations = await generate_visualizations(df, model_results)
    return visualizations


if __name__ == "__main__":  # pragma: no cover
    input_data = {
        "data_path": "examples/data/sample.csv",
    }

    data_ctx = data_processing_workflow.run(input_data)
    print(data_ctx.to_json())

    model_ctx = model_training_workflow.run(data_ctx.output)
    print(model_ctx.to_json())

    viz_input = {**data_ctx.output, "model_results": model_ctx.output}
    viz_ctx = visualization_workflow.run(viz_input)
    print(viz_ctx.to_json())

    print(
        to_json(
            {
                "data_stats": data_ctx.output["data_stats"],
                "model_performance": model_ctx.output,
                "visualizations": viz_ctx.output,
            },
        ),
    )
