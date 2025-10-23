#!/usr/bin/env python3
"""
Tests for scheduling examples to ensure they work correctly.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

from flux.domain.schedule import CronSchedule, IntervalSchedule

# Add project root to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_daily_report_example():
    """Test daily sales report example"""
    try:
        from examples.scheduling.daily_report import daily_report_workflow

        # Test workflow execution
        result = daily_report_workflow.run()
        assert result.has_succeeded, "Daily report workflow should succeed"

        output = result.output
        assert "sales" in output, "Output should contain sales data"
        assert "email" in output, "Output should contain email result"
        assert "execution_id" in output, "Output should contain execution ID"

        # Test schedule configuration
        schedule = daily_report_workflow.schedule
        assert isinstance(schedule, CronSchedule), "Should use cron schedule"
        assert schedule.cron_expression == "0 9 * * MON-FRI", "Should run weekdays at 9 AM"
        assert schedule.timezone == "UTC", "Should use UTC timezone"

        print("✓ Daily report example test passed")
        return True

    except Exception as e:
        print(f"❌ Daily report example test failed: {e}")
        return False


def test_backup_example():
    """Test database backup example"""
    try:
        from examples.scheduling.simple_backup import backup_workflow

        # Test workflow execution with custom input
        result = backup_workflow.run("test_database")
        assert result.has_succeeded, "Backup workflow should succeed"

        output = result.output
        assert output["database"] == "test_database", "Should use provided database name"
        assert "backup" in output, "Output should contain backup info"
        assert "cloud" in output, "Output should contain cloud upload info"

        # Test schedule configuration
        schedule = backup_workflow.schedule
        assert isinstance(schedule, IntervalSchedule), "Should use interval schedule"
        assert schedule.interval.total_seconds() == 6 * 3600, "Should run every 6 hours"

        print("✓ Backup example test passed")
        return True

    except Exception as e:
        print(f"❌ Backup example test failed: {e}")
        return False


def test_health_check_example():
    """Test system health check example"""
    try:
        from examples.scheduling.health_check import health_check_workflow

        # Test workflow execution
        result = health_check_workflow.run()
        assert result.has_succeeded, "Health check workflow should succeed"

        output = result.output
        assert "database" in output, "Output should contain database status"
        assert "apis" in output, "Output should contain API status"
        assert "alert" in output, "Output should contain alert result"

        # Test schedule configuration
        schedule = health_check_workflow.schedule
        assert isinstance(schedule, CronSchedule), "Should use cron schedule"
        assert schedule.cron_expression == "*/15 * * * *", "Should run every 15 minutes"

        print("✓ Health check example test passed")
        return True

    except Exception as e:
        print(f"❌ Health check example test failed: {e}")
        return False


def test_data_sync_example():
    """Test data synchronization example"""
    try:
        from examples.scheduling.data_sync import data_sync_workflow

        # Test workflow execution with custom config
        config = {"source": "test_source", "target": "test_target"}
        result = data_sync_workflow.run(config)
        assert result.has_succeeded, "Data sync workflow should succeed"

        output = result.output
        assert "extraction" in output, "Output should contain extraction result"
        assert "transformation" in output, "Output should contain transformation result"
        assert "loading" in output, "Output should contain loading result"
        assert output["config"]["source"] == "test_source", "Should use provided source"

        # Test schedule configuration
        schedule = data_sync_workflow.schedule
        assert isinstance(schedule, IntervalSchedule), "Should use interval schedule"
        assert schedule.interval.total_seconds() == 2 * 3600, "Should run every 2 hours"

        print("✓ Data sync example test passed")
        return True

    except Exception as e:
        print(f"❌ Data sync example test failed: {e}")
        return False


def test_schedule_next_run_times():
    """Test that all example schedules calculate next run times correctly"""
    try:
        from examples.scheduling.daily_report import daily_report_workflow
        from examples.scheduling.simple_backup import backup_workflow
        from examples.scheduling.health_check import health_check_workflow
        from examples.scheduling.data_sync import data_sync_workflow

        workflows = [
            ("Daily Report", daily_report_workflow),
            ("Backup", backup_workflow),
            ("Health Check", health_check_workflow),
            ("Data Sync", data_sync_workflow),
        ]

        current_time = datetime.now(timezone.utc)

        for name, workflow in workflows:
            schedule = workflow.schedule
            next_run = schedule.next_run_time(current_time)

            assert next_run is not None, f"{name} schedule should have next run time"
            assert next_run > current_time, f"{name} next run should be in future"

            print(f"✓ {name} next run: {next_run}")

        print("✓ All schedules calculate next run times correctly")
        return True

    except Exception as e:
        print(f"❌ Schedule next run times test failed: {e}")
        return False


def run_all_tests():
    """Run all scheduling example tests"""
    print("🧪 Testing Scheduling Examples...")
    print("=" * 50)

    tests = [
        test_daily_report_example,
        test_backup_example,
        test_health_check_example,
        test_data_sync_example,
        test_schedule_next_run_times,
    ]

    passed = 0
    failed = 0

    for test_func in tests:
        print(f"\n🔍 Running {test_func.__name__}...")
        try:
            if test_func():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"❌ Test {test_func.__name__} failed with exception: {e}")
            failed += 1

    print("\n" + "=" * 50)
    print(f"📊 Test Results: {passed} passed, {failed} failed")

    if failed == 0:
        print("🎉 All scheduling example tests passed!")
        return True
    else:
        print("💥 Some tests failed!")
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
