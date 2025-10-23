#!/usr/bin/env python3
"""
Example: Data Synchronization

Sync data between systems every 2 hours.
"""

from flux import ExecutionContext, interval
from flux.task import task
from flux.workflow import workflow


@task
async def extract_from_source(source: str) -> dict:
    """Extract data from source system"""
    return {"source": source, "records_extracted": 1250, "last_updated": "2024-01-15T10:30:00Z"}


@task
async def transform_data(raw_data: dict) -> dict:
    """Transform data for target system"""
    return {
        "records_transformed": raw_data["records_extracted"],
        "transformations_applied": ["dedupe", "normalize", "validate"],
    }


@task
async def load_to_target(transformed_data: dict, target: str) -> dict:
    """Load data into target system"""
    return {
        "target": target,
        "records_loaded": transformed_data["records_transformed"],
        "load_duration_seconds": 23,
    }


# Every 2 hours
@workflow.with_options(name="etl_sync", schedule=interval(hours=2, timezone="UTC"))
async def data_sync_workflow(ctx: ExecutionContext[dict]) -> dict:
    """ETL pipeline that runs every 2 hours"""
    config = ctx.input or {"source": "api.partner.com", "target": "warehouse.company.com"}

    raw_data = await extract_from_source(config["source"])
    transformed = await transform_data(raw_data)
    loaded = await load_to_target(transformed, config["target"])

    return {
        "extraction": raw_data,
        "transformation": transformed,
        "loading": loaded,
        "config": config,
        "execution_id": ctx.execution_id,
    }


if __name__ == "__main__":
    print("Testing data sync...")
    result = data_sync_workflow.run()
    print(f"Sync result: {result.output}")
    print("Schedule: Every 2 hours")
