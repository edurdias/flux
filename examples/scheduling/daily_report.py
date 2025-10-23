#!/usr/bin/env python3
"""
Example: Daily Sales Report

Generate sales report every weekday at 9 AM UTC.
"""

from flux import ExecutionContext, cron
from flux.task import task
from flux.workflow import workflow
from datetime import datetime, timezone


@task
async def fetch_sales_data() -> dict:
    """Fetch yesterday's sales data"""
    return {"total_sales": 12500.50, "orders": 85, "top_product": "Widget Pro"}


@task
async def send_email(data: dict) -> dict:
    """Send report via email"""
    report = f"Sales: ${data['total_sales']}, Orders: {data['orders']}"
    return {
        "sent_to": "team@company.com",
        "report": report,
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }


# Daily at 9 AM weekdays
@workflow.with_options(name="daily_sales_report", schedule=cron("0 9 * * MON-FRI", timezone="UTC"))
async def daily_report_workflow(ctx: ExecutionContext) -> dict:
    """Generate daily sales report"""
    sales_data = await fetch_sales_data()
    email_result = await send_email(sales_data)

    return {"sales": sales_data, "email": email_result, "execution_id": ctx.execution_id}


if __name__ == "__main__":
    print("Testing daily report...")
    result = daily_report_workflow.run()
    print(f"Report: {result.output}")
    if daily_report_workflow.schedule:
        print(f"Next run: {daily_report_workflow.schedule.next_run_time()}")
    else:
        print("Next run: Not scheduled")
