#!/usr/bin/env python3
"""
Example: Scheduled workflow implementation

This example demonstrates how to use the new scheduling functionality in Flux.
"""

from __future__ import annotations

from flux import ExecutionContext, cron, interval
from flux.task import task
from flux.workflow import workflow
from datetime import datetime, timezone


@task
async def generate_report(data: str):
    """Generate a report with the given data"""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return f"Report generated at {timestamp}: {data}"


@task
async def send_notification(message: str):
    """Send a notification with the message"""
    return f"Notification sent: {message}"


# Example 1: Cron-based schedule - daily at 9 AM UTC on weekdays
@workflow.with_options(name="daily_report", schedule=cron("0 9 * * MON-FRI", timezone="UTC"))
async def daily_report_workflow(ctx: ExecutionContext[str]):
    """Daily business report workflow"""
    input_data = ctx.input if ctx.input else "Daily metrics"
    report = await generate_report(input_data)
    notification = await send_notification(report)

    return {"report": report, "notification": notification, "execution_id": ctx.execution_id}


# Example 2: Interval-based schedule - every 6 hours
@workflow.with_options(name="sync_data", schedule=interval(hours=6, timezone="UTC"))
async def data_sync_workflow(ctx: ExecutionContext[dict]):
    """Data synchronization workflow"""
    sync_config = ctx.input or {"source": "database", "target": "warehouse"}

    result = await generate_report(
        f"Syncing from {sync_config['source']} to {sync_config['target']}",
    )

    return {"sync_result": result, "config": sync_config, "execution_id": ctx.execution_id}


# Example 3: Workflow without schedule (manual execution)
@workflow.with_options(name="manual_workflow")
async def manual_workflow(ctx: ExecutionContext[str]):
    """Manual workflow for on-demand execution"""
    message = ctx.input or "Manual execution"
    result = await generate_report(message)
    return {"result": result, "execution_id": ctx.execution_id}


if __name__ == "__main__":
    # Test the workflows manually
    print("Testing scheduled workflows...")

    print("\n1. Testing daily report workflow:")
    ctx1 = daily_report_workflow.run("Test data")
    print(f"Result: {ctx1.output}")
    print(f"Schedule: {daily_report_workflow.schedule}")

    print("\n2. Testing data sync workflow:")
    ctx2 = data_sync_workflow.run({"source": "API", "target": "database"})
    print(f"Result: {ctx2.output}")
    print(f"Schedule: {data_sync_workflow.schedule}")

    print("\n3. Testing manual workflow:")
    ctx3 = manual_workflow.run("Manual test")
    print(f"Result: {ctx3.output}")
    print(f"Schedule: {manual_workflow.schedule}")

    print("\n4. Testing schedule functionality:")

    # Test cron schedule
    cron_schedule = cron("0 9 * * MON-FRI", timezone="UTC")
    print(f"Cron schedule type: {cron_schedule.to_dict()['type']}")
    print(f"Cron expression: {cron_schedule.cron_expression}")
    print(f"Next run time: {cron_schedule.next_run_time()}")

    # Test interval schedule
    interval_schedule = interval(hours=6, timezone="UTC")
    print(f"Interval schedule type: {interval_schedule.to_dict()['type']}")
    print(f"Interval seconds: {interval_schedule.to_dict()['interval_seconds']}")
    print(f"Next run time: {interval_schedule.next_run_time()}")

    print("\n✓ Scheduled workflows are working correctly!")
