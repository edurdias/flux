#!/usr/bin/env python3
"""
Example: System Health Check

Monitor system health every 15 minutes.
"""

from flux import ExecutionContext, cron
from flux.task import task
from flux.workflow import workflow


@task
async def check_database() -> dict:
    """Check database connectivity"""
    return {"status": "healthy", "response_time_ms": 45}


@task
async def check_api_endpoints() -> dict:
    """Check critical API endpoints"""
    return {"endpoints_checked": 5, "all_healthy": True, "avg_response_time": 120}


@task
async def alert_if_unhealthy(db_status: dict, api_status: dict) -> dict:
    """Send alert if any service is unhealthy"""
    if db_status["status"] != "healthy" or not api_status["all_healthy"]:
        return {"alert_sent": True, "message": "System health issue detected"}
    return {"alert_sent": False, "message": "All systems healthy"}


# Every 15 minutes
@workflow.with_options(name="health_check", schedule=cron("*/15 * * * *", timezone="UTC"))
async def health_check_workflow(ctx: ExecutionContext) -> dict:
    """Monitor system health every 15 minutes"""
    db_status = await check_database()
    api_status = await check_api_endpoints()
    alert_result = await alert_if_unhealthy(db_status, api_status)

    return {
        "database": db_status,
        "apis": api_status,
        "alert": alert_result,
        "execution_id": ctx.execution_id,
    }


if __name__ == "__main__":
    print("Testing health check...")
    result = health_check_workflow.run()
    print(f"Health status: {result.output}")
    print("Schedule: Every 15 minutes")
