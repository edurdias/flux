"""
Tests for Flux scheduling functionality
"""

from __future__ import annotations

# import pytest  # Not available in this environment, using manual testing
from datetime import datetime, timedelta, timezone

from flux.domain.schedule import (
    CronSchedule,
    IntervalSchedule,
    OnceSchedule,
    cron,
    interval,
    once,
    schedule_factory,
)


class TestSchedules:
    """Test schedule functionality"""

    def test_cron_schedule_creation(self):
        """Test creating a cron schedule"""
        schedule = cron("0 9 * * MON-FRI", timezone="UTC")

        assert isinstance(schedule, CronSchedule)
        assert schedule.cron_expression == "0 9 * * MON-FRI"
        assert schedule.timezone == "UTC"

        schedule_dict = schedule.to_dict()
        assert schedule_dict["type"] == "cron"
        assert schedule_dict["cron_expression"] == "0 9 * * MON-FRI"
        assert schedule_dict["timezone"] == "UTC"

    def test_cron_schedule_next_run(self):
        """Test cron schedule next run calculation"""
        schedule = cron("0 0 * * *", timezone="UTC")  # Daily at midnight

        base_time = datetime(2024, 1, 1, 12, 0, 0)  # Noon
        next_run = schedule.next_run_time(base_time)

        assert next_run is not None
        assert next_run > base_time
        assert next_run.hour == 0
        assert next_run.minute == 0

    def test_interval_schedule_creation(self):
        """Test creating an interval schedule"""
        schedule = interval(hours=6, minutes=30, timezone="UTC")

        assert isinstance(schedule, IntervalSchedule)
        assert schedule.interval == timedelta(hours=6, minutes=30)
        assert schedule.timezone == "UTC"

        schedule_dict = schedule.to_dict()
        assert schedule_dict["type"] == "interval"
        assert schedule_dict["interval_seconds"] == 6 * 3600 + 30 * 60

    def test_interval_schedule_next_run(self):
        """Test interval schedule next run calculation"""
        schedule = interval(hours=2, timezone="UTC")

        base_time = datetime(2024, 1, 1, 12, 0, 0)
        next_run = schedule.next_run_time(base_time)

        assert next_run is not None
        assert next_run == base_time + timedelta(hours=2)

    def test_once_schedule_creation(self):
        """Test creating a one-time schedule"""
        run_time = datetime(2024, 12, 25, 9, 0, 0)
        schedule = once(run_time, timezone="UTC")

        assert isinstance(schedule, OnceSchedule)
        assert schedule.run_time == run_time
        assert schedule.timezone == "UTC"
        assert not schedule.executed

        schedule_dict = schedule.to_dict()
        assert schedule_dict["type"] == "once"
        assert schedule_dict["run_time"] == run_time.isoformat()
        assert not schedule_dict["executed"]

    def test_once_schedule_execution(self):
        """Test one-time schedule execution tracking"""
        run_time = datetime(2024, 12, 25, 9, 0, 0)
        schedule = once(run_time, timezone="UTC")

        # Before execution
        future_time = run_time + timedelta(minutes=1)
        next_run = schedule.next_run_time(future_time)
        assert next_run is None  # Past the run time

        current_time = run_time + timedelta(minutes=1)
        next_run = schedule.next_run_time(current_time)
        assert next_run is None

        # Mark as executed
        schedule.mark_executed()
        assert schedule.executed

        # After execution
        next_run = schedule.next_run_time()
        assert next_run is None

    def test_schedule_factory(self):
        """Test schedule factory function"""
        # Test cron schedule
        cron_data = {"type": "cron", "cron_expression": "0 9 * * *", "timezone": "UTC"}
        schedule = schedule_factory(cron_data)
        assert isinstance(schedule, CronSchedule)
        assert schedule.cron_expression == "0 9 * * *"

        # Test interval schedule
        interval_data = {"type": "interval", "interval_seconds": 3600, "timezone": "UTC"}
        schedule = schedule_factory(interval_data)
        assert isinstance(schedule, IntervalSchedule)
        assert schedule.interval.total_seconds() == 3600

        # Test once schedule
        run_time = datetime(2024, 12, 25, 9, 0, 0)
        once_data = {"type": "once", "run_time": run_time.isoformat(), "timezone": "UTC"}
        schedule = schedule_factory(once_data)
        assert isinstance(schedule, OnceSchedule)
        assert schedule.run_time == run_time

    def test_invalid_cron_expression(self):
        """Test invalid cron expression handling"""
        try:
            cron("invalid cron")
            assert False, "Expected ValueError for invalid cron expression"
        except ValueError as e:
            assert "Invalid cron expression" in str(e)

    def test_invalid_interval(self):
        """Test invalid interval handling"""
        try:
            interval(hours=0, minutes=0)
            assert False, "Expected ValueError for invalid interval"
        except ValueError as e:
            assert "Interval must be positive" in str(e)

    def test_schedule_serialization_roundtrip(self):
        """Test schedule serialization and deserialization"""
        original_schedules = [
            cron("0 9 * * MON-FRI", timezone="UTC"),
            interval(hours=6, minutes=30, timezone="America/New_York"),
            once(datetime(2024, 12, 25, 9, 0, 0), timezone="UTC"),
        ]

        for original in original_schedules:
            # Serialize to dict
            schedule_dict = original.to_dict()

            # Deserialize back
            recreated = schedule_factory(schedule_dict)

            # Compare
            assert type(recreated) is type(original)
            assert recreated.to_dict() == schedule_dict

    def test_cron_should_run(self):
        """Test cron schedule should_run method"""
        schedule = cron("0 9 * * *", timezone="UTC")  # Daily at 9 AM

        # Should run at 9:00 AM
        run_time = datetime(2024, 1, 1, 9, 0, 0)
        assert schedule.should_run(run_time)

        # Should not run at other times
        not_run_time = datetime(2024, 1, 1, 10, 0, 0)
        assert not schedule.should_run(not_run_time)

    def test_interval_should_run(self):
        """Test interval schedule should_run method"""
        schedule = interval(hours=1, timezone="UTC")

        # First run should be allowed
        assert schedule.should_run(datetime.now(timezone.utc))

        # After marking a run, should wait for interval
        run_time = datetime.now(timezone.utc)
        schedule.mark_run(run_time)

        # Too soon
        assert not schedule.should_run(run_time + timedelta(minutes=30))

        # After interval
        assert schedule.should_run(run_time + timedelta(hours=1, minutes=5))

    def test_schedule_timezone_handling(self):
        """Test timezone handling in schedules"""
        # Test UTC timezone
        utc_schedule = cron("0 9 * * *", timezone="UTC")
        assert utc_schedule.timezone == "UTC"

        # Test named timezone
        ny_schedule = interval(hours=1, timezone="America/New_York")
        assert ny_schedule.timezone == "America/New_York"


if __name__ == "__main__":
    # Run tests manually if called directly
    test_instance = TestSchedules()

    try:
        test_instance.test_cron_schedule_creation()
        print("✓ Cron schedule creation test passed")

        test_instance.test_cron_schedule_next_run()
        print("✓ Cron schedule next run test passed")

        test_instance.test_interval_schedule_creation()
        print("✓ Interval schedule creation test passed")

        test_instance.test_interval_schedule_next_run()
        print("✓ Interval schedule next run test passed")

        test_instance.test_once_schedule_creation()
        print("✓ Once schedule creation test passed")

        test_instance.test_schedule_factory()
        print("✓ Schedule factory test passed")

        test_instance.test_schedule_serialization_roundtrip()
        print("✓ Schedule serialization roundtrip test passed")

        test_instance.test_schedule_timezone_handling()
        print("✓ Schedule timezone handling test passed")

        print("\n✅ All scheduling tests passed!")

    except Exception as e:
        print(f"❌ Test failed: {e}")
        raise
