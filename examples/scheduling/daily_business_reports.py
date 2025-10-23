#!/usr/bin/env python3
"""
Example: Daily Business Reports Workflow

This example demonstrates a real-world scheduled workflow that generates
daily business reports every weekday morning at 9 AM UTC.
"""

from __future__ import annotations

from datetime import datetime, timezone

from flux import ExecutionContext, cron
from flux.domain.schedule import CronSchedule, IntervalSchedule
from flux.task import task
from flux.workflow import workflow


@task
async def fetch_daily_metrics() -> dict:
    """Simulate fetching daily business metrics from various sources"""
    # In a real implementation, this would connect to databases, APIs, etc.
    metrics = {
        "sales": {"total_revenue": 45670.32, "orders_count": 127, "average_order_value": 359.61},
        "users": {"new_registrations": 34, "active_users": 1256, "churn_rate": 0.02},
        "inventory": {"low_stock_items": 8, "out_of_stock_items": 2, "total_products": 450},
    }

    return metrics


@task
async def calculate_kpis(metrics: dict) -> dict:
    """Calculate key performance indicators from raw metrics"""
    kpis = {
        "revenue_per_user": metrics["sales"]["total_revenue"]
        / max(metrics["users"]["active_users"], 1),
        "conversion_rate": (
            metrics["sales"]["orders_count"] / max(metrics["users"]["active_users"], 1)
        )
        * 100,
        "inventory_health": (
            (metrics["inventory"]["total_products"] - metrics["inventory"]["out_of_stock_items"])
            / metrics["inventory"]["total_products"]
        )
        * 100,
        "user_growth_rate": (
            metrics["users"]["new_registrations"] / max(metrics["users"]["active_users"], 1)
        )
        * 100,
    }

    return kpis


@task
async def generate_report_summary(metrics: dict, kpis: dict) -> str:
    """Generate a human-readable report summary"""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    summary = f"""
📊 Daily Business Report - {timestamp}

💰 SALES METRICS:
   • Total Revenue: ${metrics['sales']['total_revenue']:,.2f}
   • Orders: {metrics['sales']['orders_count']}
   • Avg Order Value: ${metrics['sales']['average_order_value']:,.2f}

👥 USER METRICS:
   • New Registrations: {metrics['users']['new_registrations']}
   • Active Users: {metrics['users']['active_users']}
   • Churn Rate: {metrics['users']['churn_rate']:.1%}

📦 INVENTORY:
   • Low Stock Items: {metrics['inventory']['low_stock_items']}
   • Out of Stock: {metrics['inventory']['out_of_stock_items']}
   • Total Products: {metrics['inventory']['total_products']}

📈 KEY PERFORMANCE INDICATORS:
   • Revenue per User: ${kpis['revenue_per_user']:,.2f}
   • Conversion Rate: {kpis['conversion_rate']:.2f}%
   • Inventory Health: {kpis['inventory_health']:.1f}%
   • User Growth Rate: {kpis['user_growth_rate']:.2f}%

🎯 ACTION ITEMS:
   {'• Review low stock items for restocking' if metrics['inventory']['low_stock_items'] > 5 else '• Inventory levels healthy'}
   {'• Investigate high churn rate' if metrics['users']['churn_rate'] > 0.05 else '• User retention is stable'}
   {'• Celebrate strong sales performance!' if metrics['sales']['total_revenue'] > 40000 else '• Focus on sales optimization'}
"""

    return summary.strip()


@task
async def send_report_notification(report_summary: str, recipients: list[str]) -> dict:
    """Simulate sending report notifications to stakeholders"""
    # In a real implementation, this would send emails, Slack messages, etc.
    notification_result = {
        "status": "sent",
        "recipients": recipients,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "channels": ["email", "slack"],
        "summary_length": len(report_summary),
    }

    return notification_result


@task
async def store_report_data(metrics: dict, kpis: dict, execution_id: str) -> dict:
    """Simulate storing report data for historical analysis"""
    # In a real implementation, this would save to a database
    storage_result = {
        "stored_at": datetime.now(timezone.utc).isoformat(),
        "execution_id": execution_id,
        "records_stored": 1,
        "data_points": len(metrics) + len(kpis),
        "storage_location": f"reports/daily/{datetime.now(timezone.utc).strftime('%Y-%m-%d')}/{execution_id}.json",
    }

    return storage_result


# Scheduled workflow - Daily at 9 AM UTC on weekdays
@workflow.with_options(
    name="daily_business_reports",
    schedule=cron("0 9 * * MON-FRI", timezone="UTC"),
)
async def daily_business_reports_workflow(ctx: ExecutionContext[dict]) -> dict:
    """
    Daily business reports workflow that runs every weekday at 9 AM UTC.

    This workflow:
    1. Fetches daily business metrics from various sources
    2. Calculates key performance indicators
    3. Generates a human-readable report summary
    4. Sends notifications to stakeholders
    5. Stores the data for historical analysis
    """

    # Get configuration from input or use defaults
    config = ctx.input or {
        "recipients": ["management@company.com", "analytics@company.com"],
        "include_kpis": True,
        "store_historical_data": True,
    }

    # Step 1: Fetch daily metrics
    metrics = await fetch_daily_metrics()

    # Step 2: Calculate KPIs
    kpis = await calculate_kpis(metrics) if config.get("include_kpis", True) else {}

    # Step 3: Generate report summary
    report_summary = await generate_report_summary(metrics, kpis)

    # Step 4: Send notifications
    notification_result = await send_report_notification(
        report_summary,
        config.get("recipients", []),
    )

    # Step 5: Store historical data (optional)
    storage_result = None
    if config.get("store_historical_data", True):
        storage_result = await store_report_data(metrics, kpis, ctx.execution_id)

    # Return comprehensive result
    return {
        "execution_id": ctx.execution_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "kpis": kpis,
        "report_summary": report_summary,
        "notification_result": notification_result,
        "storage_result": storage_result,
        "config": config,
        "status": "completed",
    }


if __name__ == "__main__":
    """Test the workflow manually"""
    print("🚀 Testing Daily Business Reports Workflow...")

    # Test with default configuration
    print("\n📊 Running with default configuration...")
    ctx1 = daily_business_reports_workflow.run(None)

    if ctx1.has_succeeded:
        result = ctx1.output
        print("✅ Workflow completed successfully!")
        print(f"   Execution ID: {result['execution_id']}")
        print(f"   Metrics collected: {len(result['metrics'])} categories")
        print(f"   KPIs calculated: {len(result['kpis'])} indicators")
        print(f"   Notifications sent: {result['notification_result']['status']}")

        print("\n📈 Sample Report Summary:")
        print(
            result["report_summary"][:500] + "..."
            if len(result["report_summary"]) > 500
            else result["report_summary"],
        )
    else:
        print(f"❌ Workflow failed: {ctx1.output}")

    # Test with custom configuration
    print("\n⚙️  Running with custom configuration...")
    custom_config = {
        "recipients": ["ceo@company.com", "cto@company.com"],
        "include_kpis": True,
        "store_historical_data": False,
    }

    ctx2 = daily_business_reports_workflow.run(custom_config)

    if ctx2.has_succeeded:
        result = ctx2.output
        print("✅ Custom workflow completed successfully!")
        print(f"   Custom recipients: {len(result['config']['recipients'])}")
        print(f"   Historical storage: {'Enabled' if result['storage_result'] else 'Disabled'}")
    else:
        print(f"❌ Custom workflow failed: {ctx2.output}")

    # Display schedule information
    schedule = daily_business_reports_workflow.schedule
    if schedule:
        print("\n⏰ Schedule Information:")
        print(f"   Type: {schedule.to_dict()['type']}")
        if isinstance(schedule, CronSchedule):
            print(f"   Expression: {schedule.cron_expression}")
        elif isinstance(schedule, IntervalSchedule):
            print(f"   Interval: {schedule.interval}")
        else:
            print("   Configuration: Custom schedule")
        print(f"   Timezone: {schedule.timezone}")
        print(f"   Next run: {schedule.next_run_time()}")

    print("\n✨ Daily Business Reports workflow is ready for scheduling!")
