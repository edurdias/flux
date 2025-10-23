from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

import croniter  # type: ignore

from flux.config import Configuration


class ScheduleType(Enum):
    """Types of schedules supported by Flux"""

    CRON = "cron"
    INTERVAL = "interval"
    ONCE = "once"  # One-time execution at specific time


class ScheduleStatus(Enum):
    """Status of a schedule"""

    ACTIVE = "active"
    PAUSED = "paused"
    DISABLED = "disabled"


class Schedule(ABC):
    """Abstract base class for all schedule types"""

    def __init__(self, timezone: str = "UTC"):
        """Initialize schedule with timezone

        Args:
            timezone: Timezone for schedule execution (default: UTC)
        """
        # Reject "local" timezone as it's not portable across systems
        if timezone == "local":
            raise ValueError(
                "Use explicit timezone names (e.g., 'America/New_York') instead of 'local'. "
                "The 'local' timezone is not portable across systems.",
            )

        # Validate timezone name
        try:
            ZoneInfo(timezone)
        except Exception as e:
            raise ValueError(f"Invalid timezone '{timezone}': {e}")

        self.timezone = timezone

    @property
    @abstractmethod
    def type(self) -> ScheduleType:
        """Get the schedule type"""
        pass

    @abstractmethod
    def next_run_time(self, base_time: datetime | None = None) -> datetime | None:
        """Get the next run time for this schedule

        Args:
            base_time: Base time to calculate from (default: now)

        Returns:
            Next scheduled run time, or None if no future runs
        """
        pass

    @abstractmethod
    def should_run(self, current_time: datetime) -> bool:
        """Check if the schedule should run at the given time

        Args:
            current_time: Current time to check

        Returns:
            True if schedule should run
        """
        pass

    @abstractmethod
    def to_dict(self) -> dict[str, Any]:
        """Serialize schedule to dictionary"""
        pass

    @classmethod
    @abstractmethod
    def from_dict(cls, data: dict[str, Any]) -> Schedule:
        """Deserialize schedule from dictionary"""
        pass


class CronSchedule(Schedule):
    """Cron-based schedule implementation"""

    def __init__(self, cron_expression: str, timezone: str = "UTC"):
        """Initialize cron schedule

        Args:
            cron_expression: Standard cron expression (5 or 6 fields)
            timezone: Timezone for schedule execution
        """
        super().__init__(timezone)

        if not self._is_valid_cron(cron_expression):
            raise ValueError(f"Invalid cron expression: {cron_expression}")

        self.cron_expression = cron_expression

        # Cache tolerance from configuration
        self._tolerance = Configuration.get().settings.scheduling.schedule_check_tolerance

    @property
    def type(self) -> ScheduleType:
        """Get the schedule type"""
        return ScheduleType.CRON

    def _is_valid_cron(self, expression: str) -> bool:
        """Validate cron expression format"""
        try:
            # Test with croniter to validate
            croniter.croniter(expression, datetime.now())
            return True
        except (ValueError, TypeError):
            return False

    def next_run_time(self, base_time: datetime | None = None) -> datetime | None:
        """Get next run time based on cron expression"""
        if base_time is None:
            base_time = datetime.now(timezone.utc)

        try:
            cron = croniter.croniter(self.cron_expression, base_time)
            next_time = cron.get_next(datetime)
            return next_time
        except Exception:
            return None

    def should_run(self, current_time: datetime) -> bool:
        """Check if current time matches cron schedule"""
        try:
            # Convert to timezone-aware datetime if needed
            if current_time.tzinfo is None:
                tz = ZoneInfo(self.timezone)
                current_time = current_time.replace(tzinfo=tz)

            cron = croniter.croniter(self.cron_expression, current_time)
            # Check if current_time exactly matches the schedule by checking both directions
            prev_time = cron.get_prev(datetime)
            next_time = cron.get_next(datetime)

            # Should run if we're exactly at a scheduled time (within configured tolerance)
            return (
                abs((current_time - prev_time).total_seconds()) <= self._tolerance
                or abs((current_time - next_time).total_seconds()) <= self._tolerance
            )
        except Exception:
            return False

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary"""
        return {
            "type": ScheduleType.CRON.value,
            "cron_expression": self.cron_expression,
            "timezone": self.timezone,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CronSchedule:
        """Deserialize from dictionary"""
        return cls(
            cron_expression=data["cron_expression"],
            timezone=data.get("timezone", "UTC"),
        )


class IntervalSchedule(Schedule):
    """Interval-based schedule implementation"""

    def __init__(
        self,
        seconds: int = 0,
        minutes: int = 0,
        hours: int = 0,
        days: int = 0,
        weeks: int = 0,
        timezone: str = "UTC",
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ):
        """Initialize interval schedule

        Args:
            seconds: Interval in seconds
            minutes: Interval in minutes
            hours: Interval in hours
            days: Interval in days
            weeks: Interval in weeks
            timezone: Timezone for schedule execution
            start_time: Optional start time for the schedule
            end_time: Optional end time for the schedule
        """
        super().__init__(timezone)

        self.interval = timedelta(
            seconds=seconds,
            minutes=minutes,
            hours=hours,
            days=days,
            weeks=weeks,
        )

        if self.interval.total_seconds() <= 0:
            raise ValueError("Interval must be positive")

        self.start_time = start_time
        self.end_time = end_time

        # Track the last scheduled time
        self.last_run_time: datetime | None = None

    @property
    def type(self) -> ScheduleType:
        """Get the schedule type"""
        return ScheduleType.INTERVAL

    def next_run_time(self, base_time: datetime | None = None) -> datetime | None:
        """Get next run time based on interval"""
        if base_time is None:
            base_time = datetime.now(timezone.utc)

        # If we have a start time, use that as the basis
        if self.start_time and base_time < self.start_time:
            next_time = self.start_time
        elif self.last_run_time:
            next_time = self.last_run_time + self.interval
        else:
            next_time = base_time + self.interval

        # Check if we're past the end time
        if self.end_time and next_time > self.end_time:
            return None

        return next_time

    def should_run(self, current_time: datetime) -> bool:
        """Check if enough time has passed since last run"""
        if self.start_time and current_time < self.start_time:
            return False

        if self.end_time and current_time > self.end_time:
            return False

        if self.last_run_time is None:
            if self.start_time:
                return current_time >= self.start_time
            return True

        time_since_last = current_time - self.last_run_time
        return time_since_last >= self.interval

    def mark_run(self, run_time: datetime):
        """Mark that the schedule was executed at the given time"""
        self.last_run_time = run_time

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary"""
        return {
            "type": ScheduleType.INTERVAL.value,
            "interval_seconds": int(self.interval.total_seconds()),
            "timezone": self.timezone,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "last_run_time": self.last_run_time.isoformat() if self.last_run_time else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IntervalSchedule:
        """Deserialize from dictionary"""
        instance = cls(
            seconds=data.get("interval_seconds", 0),
            timezone=data.get("timezone", "UTC"),
            start_time=datetime.fromisoformat(data["start_time"])
            if data.get("start_time")
            else None,
            end_time=datetime.fromisoformat(data["end_time"]) if data.get("end_time") else None,
        )

        if data.get("last_run_time"):
            instance.last_run_time = datetime.fromisoformat(data["last_run_time"])

        return instance


class OnceSchedule(Schedule):
    """One-time schedule for specific datetime execution"""

    def __init__(self, run_time: datetime, timezone: str = "UTC"):
        """Initialize one-time schedule

        Args:
            run_time: Specific time to run the workflow
            timezone: Timezone for schedule execution
        """
        super().__init__(timezone)
        self.run_time = run_time
        self.executed = False

        # Cache tolerance from configuration
        self._tolerance = Configuration.get().settings.scheduling.once_schedule_tolerance

    @property
    def type(self) -> ScheduleType:
        """Get the schedule type"""
        return ScheduleType.ONCE

    def next_run_time(self, base_time: datetime | None = None) -> datetime | None:
        """Get next run time (only if not executed yet)"""
        if self.executed:
            return None

        if base_time is None:
            base_time = datetime.now(timezone.utc)

        # Ensure both datetime objects have the same timezone awareness
        run_time = self.run_time
        if run_time.tzinfo is None and base_time.tzinfo is not None:
            run_time = run_time.replace(tzinfo=timezone.utc)
        elif run_time.tzinfo is not None and base_time.tzinfo is None:
            base_time = base_time.replace(tzinfo=timezone.utc)

        return run_time if run_time > base_time else None

    def should_run(self, current_time: datetime) -> bool:
        """Check if it's time to run (within configured tolerance of scheduled time)"""
        if self.executed:
            return False

        # Ensure both datetime objects have the same timezone awareness
        run_time = self.run_time
        if run_time.tzinfo is None and current_time.tzinfo is not None:
            run_time = run_time.replace(tzinfo=timezone.utc)
        elif run_time.tzinfo is not None and current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)

        time_diff = abs((current_time - run_time).total_seconds())
        return time_diff < self._tolerance

    def mark_executed(self):
        """Mark the schedule as executed"""
        self.executed = True

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary"""
        return {
            "type": ScheduleType.ONCE.value,
            "run_time": self.run_time.isoformat(),
            "timezone": self.timezone,
            "executed": self.executed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OnceSchedule:
        """Deserialize from dictionary"""
        instance = cls(
            run_time=datetime.fromisoformat(data["run_time"]),
            timezone=data.get("timezone", "UTC"),
        )
        instance.executed = data.get("executed", False)
        return instance


def cron(expression: str, timezone: str = "UTC") -> CronSchedule:
    """Create a cron schedule

    Args:
        expression: Cron expression (e.g., "0 9 * * MON-FRI")
        timezone: Timezone for schedule execution

    Returns:
        CronSchedule instance
    """
    return CronSchedule(expression, timezone)


def interval(
    seconds: int = 0,
    minutes: int = 0,
    hours: int = 0,
    days: int = 0,
    weeks: int = 0,
    timezone: str = "UTC",
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> IntervalSchedule:
    """Create an interval schedule

    Args:
        seconds: Interval in seconds
        minutes: Interval in minutes
        hours: Interval in hours
        days: Interval in days
        weeks: Interval in weeks
        timezone: Timezone for schedule execution
        start_time: Optional start time
        end_time: Optional end time

    Returns:
        IntervalSchedule instance
    """
    return IntervalSchedule(
        seconds=seconds,
        minutes=minutes,
        hours=hours,
        days=days,
        weeks=weeks,
        timezone=timezone,
        start_time=start_time,
        end_time=end_time,
    )


def once(run_time: datetime, timezone: str = "UTC") -> OnceSchedule:
    """Create a one-time schedule

    Args:
        run_time: Specific time to run the workflow
        timezone: Timezone for schedule execution

    Returns:
        OnceSchedule instance
    """
    return OnceSchedule(run_time, timezone)


def schedule_factory(data: dict[str, Any]) -> Schedule:
    """Factory function to create schedule from dictionary data

    Args:
        data: Schedule data dictionary

    Returns:
        Appropriate Schedule instance

    Raises:
        ValueError: If schedule type is not supported
    """
    schedule_type = data.get("type")

    if schedule_type == ScheduleType.CRON.value:
        return CronSchedule.from_dict(data)
    elif schedule_type == ScheduleType.INTERVAL.value:
        return IntervalSchedule.from_dict(data)
    elif schedule_type == ScheduleType.ONCE.value:
        return OnceSchedule.from_dict(data)
    else:
        raise ValueError(f"Unsupported schedule type: {schedule_type}")
