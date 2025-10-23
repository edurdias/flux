#!/usr/bin/env python3
"""
Example: Simple Database Backup

Scheduled backup workflow that runs every 6 hours to backup critical data.
"""

from flux import ExecutionContext, interval
from flux.task import task
from flux.workflow import workflow
from datetime import datetime, timezone


@task
async def create_backup(database_name: str) -> dict:
    """Create a database backup"""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_file = f"{database_name}_backup_{timestamp}.sql"

    # Simulate backup process
    return {"backup_file": backup_file, "size_mb": 125.7, "created_at": timestamp}


@task
async def upload_to_cloud(backup_info: dict) -> dict:
    """Upload backup to cloud storage"""
    return {
        "cloud_path": f"s3://backups/{backup_info['backup_file']}",
        "status": "uploaded",
        "upload_time_seconds": 45,
    }


# Every 6 hours backup
@workflow.with_options(name="database_backup", schedule=interval(hours=6, timezone="UTC"))
async def backup_workflow(ctx: ExecutionContext[str]) -> dict:
    """Backup database every 6 hours"""
    db_name = ctx.input or "production_db"

    backup_info = await create_backup(db_name)
    cloud_result = await upload_to_cloud(backup_info)

    return {
        "database": db_name,
        "backup": backup_info,
        "cloud": cloud_result,
        "execution_id": ctx.execution_id,
    }


if __name__ == "__main__":
    print("Testing backup workflow...")
    result = backup_workflow.run("test_db")
    print(f"Backup result: {result.output}")
    if backup_workflow.schedule and hasattr(backup_workflow.schedule, "interval"):
        print(f"Schedule: Every {backup_workflow.schedule.interval}")
    else:
        print("Schedule: Not configured")
