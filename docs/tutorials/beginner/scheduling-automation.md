# Scheduling and Automation

Learn how to schedule workflows, automate recurring tasks, and build time-driven automation systems with Flux.

## Overview

Automated scheduling is essential for data pipelines, maintenance tasks, and periodic processing. This tutorial shows how to build schedulers, handle time-based triggers, and create robust automation systems using Flux workflows.

## Prerequisites

- Complete the [External APIs](external-apis.md) tutorial
- Understanding of cron expressions and time zones
- Basic knowledge of datetime handling in Python

## What You'll Build

We'll create automated systems that:
1. Schedule periodic data backups
2. Implement cron-like scheduling within workflows
3. Handle time zones and daylight saving time
4. Create conditional scheduling based on business rules
5. Build a complete monitoring and alerting system

## Step 1: Setup Dependencies

Create your project structure:

```bash
mkdir flux-scheduler-tutorial
cd flux-scheduler-tutorial
pip install flux-engine schedule pytz
```

Create `requirements.txt`:

```txt
flux-engine>=0.1.0
schedule>=1.2.0
pytz>=2023.3
pandas>=2.0.0
smtplib
```

## Step 2: Basic Time-Based Tasks

Start with simple time-aware tasks:

```python
# scheduler_workflow.py
from flux import task, workflow
from flux.tasks import sleep, now
from datetime import datetime, timedelta, time
import pytz
import logging
from typing import Optional, Dict, Any, List
import json
import os

@task
def get_current_time(timezone: str = 'UTC') -> Dict[str, str]:
    """Get current time in specified timezone."""
    tz = pytz.timezone(timezone)
    current_time = datetime.now(tz)

    return {
        'timestamp': current_time.isoformat(),
        'timezone': timezone,
        'hour': current_time.hour,
        'minute': current_time.minute,
        'weekday': current_time.strftime('%A'),
        'date': current_time.strftime('%Y-%m-%d')
    }

@task
def is_business_hours(current_time: Dict[str, str],
                     start_hour: int = 9,
                     end_hour: int = 17,
                     business_days: List[str] = None) -> bool:
    """Check if current time is within business hours."""
    if business_days is None:
        business_days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']

    hour = current_time['hour']
    weekday = current_time['weekday']

    return (start_hour <= hour < end_hour and weekday in business_days)

@task
def should_run_maintenance(current_time: Dict[str, str]) -> bool:
    """Determine if maintenance tasks should run (weekends, late night)."""
    hour = current_time['hour']
    weekday = current_time['weekday']

    # Run during weekends or late night hours (2 AM - 5 AM)
    return weekday in ['Saturday', 'Sunday'] or (2 <= hour < 5)

@workflow
def scheduled_task_router():
    """Route tasks based on current time and business rules."""
    current_time = get_current_time('US/Eastern')

    if is_business_hours(current_time):
        logging.info("Business hours - running standard tasks")
        return run_business_tasks(current_time)
    elif should_run_maintenance(current_time):
        logging.info("Maintenance window - running maintenance tasks")
        return run_maintenance_tasks(current_time)
    else:
        logging.info("Off hours - running minimal tasks")
        return run_minimal_tasks(current_time)
```

## Step 3: Cron-like Scheduling

Implement cron-style scheduling within workflows:

```python
from dataclasses import dataclass
import re

@dataclass
class CronSchedule:
    minute: str = "*"      # 0-59
    hour: str = "*"        # 0-23
    day: str = "*"         # 1-31
    month: str = "*"       # 1-12
    weekday: str = "*"     # 0-6 (Sunday = 0)

    def matches(self, dt: datetime) -> bool:
        """Check if datetime matches cron schedule."""
        return (
            self._matches_field(self.minute, dt.minute, 0, 59) and
            self._matches_field(self.hour, dt.hour, 0, 23) and
            self._matches_field(self.day, dt.day, 1, 31) and
            self._matches_field(self.month, dt.month, 1, 12) and
            self._matches_field(self.weekday, dt.weekday() + 1, 1, 7)  # Convert to 1-7
        )

    def _matches_field(self, pattern: str, value: int, min_val: int, max_val: int) -> bool:
        """Check if a cron field pattern matches the given value."""
        if pattern == "*":
            return True

        if "," in pattern:
            return value in [int(x.strip()) for x in pattern.split(",")]

        if "/" in pattern:
            base, step = pattern.split("/")
            base_val = 0 if base == "*" else int(base)
            return (value - base_val) % int(step) == 0

        if "-" in pattern:
            start, end = map(int, pattern.split("-"))
            return start <= value <= end

        return value == int(pattern)

@task
def parse_cron_schedule(cron_expr: str) -> CronSchedule:
    """Parse cron expression into CronSchedule object."""
    parts = cron_expr.split()
    if len(parts) != 5:
        raise ValueError("Cron expression must have 5 fields: minute hour day month weekday")

    return CronSchedule(
        minute=parts[0],
        hour=parts[1],
        day=parts[2],
        month=parts[3],
        weekday=parts[4]
    )

@task
def check_schedule_match(cron_expr: str, timezone: str = 'UTC') -> bool:
    """Check if current time matches cron schedule."""
    schedule = parse_cron_schedule(cron_expr)
    tz = pytz.timezone(timezone)
    current_time = datetime.now(tz)

    return schedule.matches(current_time)

@workflow
def cron_based_workflow(tasks_config: Dict[str, Dict[str, Any]]):
    """Execute tasks based on cron schedules."""
    current_time = get_current_time()
    executed_tasks = []

    for task_name, config in tasks_config.items():
        cron_expr = config['schedule']
        timezone = config.get('timezone', 'UTC')

        if check_schedule_match(cron_expr, timezone):
            logging.info(f"Executing scheduled task: {task_name}")

            # Execute the appropriate task based on configuration
            if task_name == 'backup':
                result = run_backup_task(config)
            elif task_name == 'report':
                result = generate_daily_report(config)
            elif task_name == 'cleanup':
                result = cleanup_old_files(config)
            else:
                result = run_generic_task(task_name, config)

            executed_tasks.append({
                'task': task_name,
                'executed_at': current_time['timestamp'],
                'result': result
            })
        else:
            logging.debug(f"Skipping task {task_name} - schedule not matched")

    return {
        'executed_tasks': executed_tasks,
        'total_executed': len(executed_tasks),
        'execution_time': current_time['timestamp']
    }
```

## Step 4: Data Backup Automation

Create automated backup workflows:

```python
import shutil
import zipfile
from pathlib import Path

@task(
    retry_count=3,
    retry_delay=5.0,
    timeout=300.0
)
def backup_database(config: Dict[str, Any]) -> Dict[str, Any]:
    """Create database backup."""
    import subprocess

    db_host = config['host']
    db_name = config['database']
    db_user = config['user']
    backup_dir = Path(config['backup_dir'])

    # Create backup directory if it doesn't exist
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Generate backup filename with timestamp
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_file = backup_dir / f"{db_name}_backup_{timestamp}.sql"

    try:
        # Execute pg_dump (PostgreSQL example)
        cmd = [
            'pg_dump',
            f'--host={db_host}',
            f'--username={db_user}',
            f'--dbname={db_name}',
            f'--file={backup_file}',
            '--verbose'
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if result.returncode != 0:
            raise Exception(f"Backup failed: {result.stderr}")

        # Compress backup file
        compressed_file = backup_file.with_suffix('.sql.gz')
        import gzip
        with open(backup_file, 'rb') as f_in:
            with gzip.open(compressed_file, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)

        # Remove uncompressed file
        backup_file.unlink()

        file_size = compressed_file.stat().st_size

        return {
            'status': 'success',
            'backup_file': str(compressed_file),
            'file_size_mb': round(file_size / (1024 * 1024), 2),
            'created_at': timestamp
        }

    except Exception as e:
        logging.error(f"Database backup failed: {e}")
        raise

@task
def backup_application_files(config: Dict[str, Any]) -> Dict[str, Any]:
    """Backup application files and logs."""
    source_dirs = config['source_directories']
    backup_dir = Path(config['backup_dir'])
    exclude_patterns = config.get('exclude_patterns', [])

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_file = backup_dir / f"app_backup_{timestamp}.zip"

    backup_dir.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(backup_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
            total_files = 0
            total_size = 0

            for source_dir in source_dirs:
                source_path = Path(source_dir)

                if not source_path.exists():
                    logging.warning(f"Source directory does not exist: {source_dir}")
                    continue

                for file_path in source_path.rglob('*'):
                    if file_path.is_file():
                        # Check exclude patterns
                        should_exclude = any(
                            pattern in str(file_path) for pattern in exclude_patterns
                        )

                        if not should_exclude:
                            relative_path = file_path.relative_to(source_path.parent)
                            zipf.write(file_path, relative_path)
                            total_files += 1
                            total_size += file_path.stat().st_size

        backup_size = backup_file.stat().st_size

        return {
            'status': 'success',
            'backup_file': str(backup_file),
            'files_backed_up': total_files,
            'original_size_mb': round(total_size / (1024 * 1024), 2),
            'compressed_size_mb': round(backup_size / (1024 * 1024), 2),
            'compression_ratio': round((1 - backup_size / total_size) * 100, 1) if total_size > 0 else 0,
            'created_at': timestamp
        }

    except Exception as e:
        logging.error(f"File backup failed: {e}")
        raise

@task
def cleanup_old_backups(config: Dict[str, Any]) -> Dict[str, Any]:
    """Remove backups older than retention period."""
    backup_dir = Path(config['backup_dir'])
    retention_days = config.get('retention_days', 30)

    if not backup_dir.exists():
        return {'status': 'skipped', 'reason': 'Backup directory does not exist'}

    cutoff_date = datetime.now() - timedelta(days=retention_days)
    removed_files = []
    total_space_freed = 0

    try:
        for backup_file in backup_dir.iterdir():
            if backup_file.is_file():
                file_mtime = datetime.fromtimestamp(backup_file.stat().st_mtime)

                if file_mtime < cutoff_date:
                    file_size = backup_file.stat().st_size
                    backup_file.unlink()
                    removed_files.append({
                        'file': backup_file.name,
                        'size_mb': round(file_size / (1024 * 1024), 2),
                        'age_days': (datetime.now() - file_mtime).days
                    })
                    total_space_freed += file_size

        return {
            'status': 'success',
            'files_removed': len(removed_files),
            'space_freed_mb': round(total_space_freed / (1024 * 1024), 2),
            'removed_files': removed_files,
            'retention_days': retention_days
        }

    except Exception as e:
        logging.error(f"Backup cleanup failed: {e}")
        raise

@workflow
def run_backup_task(config: Dict[str, Any]):
    """Complete backup workflow."""
    backup_results = []

    # Database backup
    if config.get('backup_database', False):
        db_config = config['database']
        db_result = backup_database(db_config)
        backup_results.append(db_result)

    # File backup
    if config.get('backup_files', False):
        file_config = config['files']
        file_result = backup_application_files(file_config)
        backup_results.append(file_result)

    # Cleanup old backups
    if config.get('cleanup_old', True):
        cleanup_config = {
            'backup_dir': config.get('backup_dir', './backups'),
            'retention_days': config.get('retention_days', 30)
        }
        cleanup_result = cleanup_old_backups(cleanup_config)
        backup_results.append(cleanup_result)

    return {
        'backup_type': 'scheduled',
        'results': backup_results,
        'success_count': sum(1 for r in backup_results if r['status'] == 'success'),
        'completed_at': datetime.now().isoformat()
    }
```

## Step 5: Report Generation and Alerting

Create automated reporting and alerting:

```python
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

@task
def generate_system_metrics() -> Dict[str, Any]:
    """Collect system metrics for reporting."""
    import psutil
    import disk_usage

    # CPU usage
    cpu_percent = psutil.cpu_percent(interval=1)

    # Memory usage
    memory = psutil.virtual_memory()
    memory_percent = memory.percent

    # Disk usage
    disk = psutil.disk_usage('/')
    disk_percent = (disk.used / disk.total) * 100

    # Process count
    process_count = len(psutil.pids())

    return {
        'cpu_usage_percent': cpu_percent,
        'memory_usage_percent': memory_percent,
        'disk_usage_percent': round(disk_percent, 2),
        'total_processes': process_count,
        'uptime_hours': round(psutil.boot_time() / 3600, 2),
        'collected_at': datetime.now().isoformat()
    }

@task
def analyze_metrics(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze metrics and determine health status."""
    alerts = []

    # Check thresholds
    if metrics['cpu_usage_percent'] > 80:
        alerts.append({
            'level': 'warning',
            'metric': 'CPU',
            'value': metrics['cpu_usage_percent'],
            'threshold': 80,
            'message': 'High CPU usage detected'
        })

    if metrics['memory_usage_percent'] > 85:
        alerts.append({
            'level': 'critical',
            'metric': 'Memory',
            'value': metrics['memory_usage_percent'],
            'threshold': 85,
            'message': 'Critical memory usage'
        })

    if metrics['disk_usage_percent'] > 90:
        alerts.append({
            'level': 'critical',
            'metric': 'Disk',
            'value': metrics['disk_usage_percent'],
            'threshold': 90,
            'message': 'Disk space critically low'
        })

    # Determine overall health
    critical_alerts = [a for a in alerts if a['level'] == 'critical']
    warning_alerts = [a for a in alerts if a['level'] == 'warning']

    if critical_alerts:
        health_status = 'critical'
    elif warning_alerts:
        health_status = 'warning'
    else:
        health_status = 'healthy'

    return {
        'health_status': health_status,
        'alerts': alerts,
        'critical_count': len(critical_alerts),
        'warning_count': len(warning_alerts),
        'analyzed_at': datetime.now().isoformat()
    }

@task
def generate_html_report(metrics: Dict[str, Any], analysis: Dict[str, Any]) -> str:
    """Generate HTML report."""
    html_template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>System Health Report</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; }}
            .header {{ background-color: #f0f0f0; padding: 10px; border-radius: 5px; }}
            .metrics {{ margin: 20px 0; }}
            .metric {{ margin: 10px 0; padding: 10px; border-left: 4px solid #ccc; }}
            .healthy {{ border-left-color: #4CAF50; }}
            .warning {{ border-left-color: #FF9800; }}
            .critical {{ border-left-color: #F44336; }}
            .alert {{ padding: 10px; margin: 5px 0; border-radius: 3px; }}
            .alert.warning {{ background-color: #FFF3CD; border: 1px solid #FFEAA7; }}
            .alert.critical {{ background-color: #F8D7DA; border: 1px solid #F5C6CB; }}
            table {{ border-collapse: collapse; width: 100%; }}
            th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
            th {{ background-color: #f2f2f2; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>System Health Report</h1>
            <p>Generated on: {timestamp}</p>
            <p>Status: <strong class="{status_class}">{health_status}</strong></p>
        </div>

        <div class="metrics">
            <h2>System Metrics</h2>
            <table>
                <tr><th>Metric</th><th>Value</th><th>Status</th></tr>
                <tr><td>CPU Usage</td><td>{cpu_usage}%</td><td class="{cpu_class}">
                    {cpu_status}</td></tr>
                <tr><td>Memory Usage</td><td>{memory_usage}%</td><td class="{memory_class}">
                    {memory_status}</td></tr>
                <tr><td>Disk Usage</td><td>{disk_usage}%</td><td class="{disk_class}">
                    {disk_status}</td></tr>
                <tr><td>Total Processes</td><td>{process_count}</td><td class="healthy">
                    Normal</td></tr>
            </table>
        </div>

        {alerts_section}

        <div class="footer">
            <p><small>Report generated by Flux Automation System</small></p>
        </div>
    </body>
    </html>
    """

    # Determine status classes
    def get_status_class(value, warning_threshold, critical_threshold):
        if value > critical_threshold:
            return 'critical', 'Critical'
        elif value > warning_threshold:
            return 'warning', 'Warning'
        else:
            return 'healthy', 'Normal'

    cpu_class, cpu_status = get_status_class(metrics['cpu_usage_percent'], 70, 80)
    memory_class, memory_status = get_status_class(metrics['memory_usage_percent'], 75, 85)
    disk_class, disk_status = get_status_class(metrics['disk_usage_percent'], 80, 90)

    # Generate alerts section
    alerts_html = ""
    if analysis['alerts']:
        alerts_html = "<div class='alerts'><h2>Alerts</h2>"
        for alert in analysis['alerts']:
            alerts_html += f"""
            <div class="alert {alert['level']}">
                <strong>{alert['level'].upper()}:</strong> {alert['message']}
                ({alert['metric']}: {alert['value']}% > {alert['threshold']}%)
            </div>
            """
        alerts_html += "</div>"

    return html_template.format(
        timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        health_status=analysis['health_status'].title(),
        status_class=analysis['health_status'],
        cpu_usage=metrics['cpu_usage_percent'],
        cpu_class=cpu_class,
        cpu_status=cpu_status,
        memory_usage=metrics['memory_usage_percent'],
        memory_class=memory_class,
        memory_status=memory_status,
        disk_usage=metrics['disk_usage_percent'],
        disk_class=disk_class,
        disk_status=disk_status,
        process_count=metrics['total_processes'],
        alerts_section=alerts_html
    )

@task(
    retry_count=2,
    retry_delay=5.0
)
def send_email_report(html_content: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Send email report."""
    smtp_server = config['smtp_server']
    smtp_port = config['smtp_port']
    smtp_user = config['smtp_user']
    smtp_password = config['smtp_password']

    from_email = config['from_email']
    to_emails = config['to_emails']
    subject = config.get('subject', 'System Health Report')

    try:
        # Create message
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = from_email
        msg['To'] = ', '.join(to_emails)

        # Add HTML content
        html_part = MIMEText(html_content, 'html')
        msg.attach(html_part)

        # Send email
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)

        return {
            'status': 'sent',
            'recipients': len(to_emails),
            'sent_at': datetime.now().isoformat()
        }

    except Exception as e:
        logging.error(f"Failed to send email report: {e}")
        raise

@workflow
def generate_daily_report(config: Dict[str, Any]):
    """Generate and send daily system report."""
    # Collect metrics
    metrics = generate_system_metrics()

    # Analyze metrics
    analysis = analyze_metrics(metrics)

    # Generate HTML report
    html_report = generate_html_report(metrics, analysis)

    # Save report to file
    report_dir = Path(config.get('report_dir', './reports'))
    report_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_file = report_dir / f"system_report_{timestamp}.html"

    with open(report_file, 'w') as f:
        f.write(html_report)

    result = {
        'report_file': str(report_file),
        'metrics': metrics,
        'analysis': analysis,
        'generated_at': datetime.now().isoformat()
    }

    # Send email if configured and alerts exist
    email_config = config.get('email')
    if email_config and (analysis['critical_count'] > 0 or config.get('always_email', False)):
        email_result = send_email_report(html_report, email_config)
        result['email_sent'] = email_result

    return result
```

## Step 6: Complete Scheduling System

Bring everything together in a comprehensive scheduler:

```python
# scheduler_main.py
import time
import signal
import sys
from threading import Event
from typing import Dict

class FluxScheduler:
    def __init__(self, config_file: str):
        self.config = self.load_config(config_file)
        self.running = Event()
        self.running.set()

        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

    def load_config(self, config_file: str) -> Dict:
        """Load scheduler configuration."""
        with open(config_file, 'r') as f:
            import yaml
            return yaml.safe_load(f)

    def signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        logging.info(f"Received signal {signum}, shutting down gracefully...")
        self.running.clear()

    def run(self):
        """Main scheduler loop."""
        logging.info("Starting Flux Scheduler...")

        while self.running.is_set():
            try:
                # Execute scheduled workflows
                result = cron_based_workflow(self.config['tasks'])

                if result['total_executed'] > 0:
                    logging.info(f"Executed {result['total_executed']} scheduled tasks")

                # Sleep for check interval (default 60 seconds)
                check_interval = self.config.get('check_interval', 60)
                self.running.wait(timeout=check_interval)

            except KeyboardInterrupt:
                break
            except Exception as e:
                logging.error(f"Scheduler error: {e}")
                # Sleep before retrying
                self.running.wait(timeout=30)

        logging.info("Scheduler stopped")

def main():
    """Main entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('scheduler.log'),
            logging.StreamHandler(sys.stdout)
        ]
    )

    config_file = sys.argv[1] if len(sys.argv) > 1 else 'scheduler_config.yaml'

    scheduler = FluxScheduler(config_file)
    scheduler.run()

if __name__ == "__main__":
    main()
```

## Step 7: Configuration Files

Create configuration files for your scheduler:

```yaml
# scheduler_config.yaml
check_interval: 60  # Check every minute

tasks:
  backup:
    schedule: "0 2 * * *"  # Daily at 2 AM
    timezone: "US/Eastern"
    backup_database: true
    backup_files: true
    cleanup_old: true
    database:
      host: "localhost"
      database: "myapp"
      user: "backup_user"
      backup_dir: "./backups/database"
    files:
      source_directories:
        - "./app"
        - "./config"
        - "./logs"
      backup_dir: "./backups/files"
      exclude_patterns:
        - "__pycache__"
        - "*.pyc"
        - ".git"
        - "node_modules"
    retention_days: 30

  report:
    schedule: "0 8 * * 1-5"  # Weekdays at 8 AM
    timezone: "US/Eastern"
    report_dir: "./reports"
    always_email: false
    email:
      smtp_server: "smtp.gmail.com"
      smtp_port: 587
      smtp_user: "your-email@gmail.com"
      smtp_password: "your-app-password"
      from_email: "your-email@gmail.com"
      to_emails:
        - "admin@company.com"
        - "ops@company.com"
      subject: "Daily System Health Report"

  cleanup:
    schedule: "0 1 * * 0"  # Weekly on Sunday at 1 AM
    timezone: "UTC"
    directories:
      - path: "./logs"
        retention_days: 14
        pattern: "*.log"
      - path: "./temp"
        retention_days: 1
        pattern: "*"
      - path: "./cache"
        retention_days: 7
        pattern: "cache_*"

  health_check:
    schedule: "*/15 * * * *"  # Every 15 minutes
    timezone: "UTC"
    endpoints:
      - url: "http://localhost:8000/health"
        timeout: 5
      - url: "http://localhost:8001/api/status"
        timeout: 10
    alert_on_failure: true
```

## Step 8: Testing the Scheduler

Create comprehensive tests:

```python
# test_scheduler.py
import pytest
from datetime import datetime
import pytz
from scheduler_workflow import (
    CronSchedule, parse_cron_schedule, check_schedule_match,
    get_current_time, is_business_hours, should_run_maintenance
)

class TestCronSchedule:
    def test_parse_basic_schedule(self):
        """Test parsing basic cron expressions."""
        schedule = parse_cron_schedule("0 8 * * 1-5")

        assert schedule.minute == "0"
        assert schedule.hour == "8"
        assert schedule.day == "*"
        assert schedule.month == "*"
        assert schedule.weekday == "1-5"

    def test_daily_schedule_match(self):
        """Test daily schedule matching."""
        schedule = CronSchedule(minute="0", hour="8", day="*", month="*", weekday="*")

        # Should match 8:00 AM any day
        dt = datetime(2023, 10, 15, 8, 0)  # Sunday
        assert schedule.matches(dt)

        # Should not match other hours
        dt = datetime(2023, 10, 15, 9, 0)
        assert not schedule.matches(dt)

    def test_weekday_schedule_match(self):
        """Test weekday-specific schedule."""
        schedule = CronSchedule(minute="0", hour="9", day="*", month="*", weekday="1-5")

        # Monday (weekday 0 -> 1 in cron)
        dt = datetime(2023, 10, 16, 9, 0)  # Monday
        assert schedule.matches(dt)

        # Sunday (weekday 6 -> 7 in cron)
        dt = datetime(2023, 10, 15, 9, 0)  # Sunday
        assert not schedule.matches(dt)

    def test_interval_schedule(self):
        """Test interval-based schedules."""
        schedule = CronSchedule(minute="*/15", hour="*", day="*", month="*", weekday="*")

        # Should match quarter hours
        assert schedule.matches(datetime(2023, 10, 15, 10, 0))
        assert schedule.matches(datetime(2023, 10, 15, 10, 15))
        assert schedule.matches(datetime(2023, 10, 15, 10, 30))
        assert schedule.matches(datetime(2023, 10, 15, 10, 45))

        # Should not match other minutes
        assert not schedule.matches(datetime(2023, 10, 15, 10, 5))
        assert not schedule.matches(datetime(2023, 10, 15, 10, 10))

class TestBusinessLogic:
    def test_business_hours_detection(self):
        """Test business hours logic."""
        # Business hours (Monday 10 AM)
        time_data = {
            'hour': 10,
            'weekday': 'Monday'
        }
        assert is_business_hours(time_data)

        # Outside business hours (Saturday 10 AM)
        time_data = {
            'hour': 10,
            'weekday': 'Saturday'
        }
        assert not is_business_hours(time_data)

        # Outside business hours (Monday 6 AM)
        time_data = {
            'hour': 6,
            'weekday': 'Monday'
        }
        assert not is_business_hours(time_data)

    def test_maintenance_window_detection(self):
        """Test maintenance window logic."""
        # Weekend maintenance
        time_data = {
            'hour': 10,
            'weekday': 'Saturday'
        }
        assert should_run_maintenance(time_data)

        # Late night maintenance
        time_data = {
            'hour': 3,
            'weekday': 'Tuesday'
        }
        assert should_run_maintenance(time_data)

        # Regular business hours
        time_data = {
            'hour': 10,
            'weekday': 'Tuesday'
        }
        assert not should_run_maintenance(time_data)

@pytest.fixture
def mock_config():
    return {
        'tasks': {
            'test_task': {
                'schedule': '0 9 * * 1-5',
                'timezone': 'UTC'
            }
        }
    }

def test_integration_workflow(mock_config):
    """Integration test for the complete workflow."""
    # This would require mocking the current time
    # to test specific schedule matches
    pass
```

## Step 9: Deployment and Monitoring

Deploy your scheduler as a system service:

```bash
# flux-scheduler.service (systemd service file)
[Unit]
Description=Flux Workflow Scheduler
After=network.target

[Service]
Type=simple
User=flux
WorkingDirectory=/opt/flux-scheduler
ExecStart=/opt/flux-scheduler/venv/bin/python scheduler_main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
# deployment.sh
#!/bin/bash

# Install and start the scheduler service
sudo cp flux-scheduler.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable flux-scheduler
sudo systemctl start flux-scheduler

# Check status
sudo systemctl status flux-scheduler
```

## Step 10: Running the Complete System

1. **Setup configuration:**
   ```bash
   cp scheduler_config.yaml.example scheduler_config.yaml
   # Edit configuration as needed
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the scheduler:**
   ```bash
   python scheduler_main.py scheduler_config.yaml
   ```

4. **Monitor logs:**
   ```bash
   tail -f scheduler.log
   ```

## Production Best Practices

### 1. Configuration Management
- Use environment variables for sensitive data
- Implement configuration validation
- Support hot-reloading of configurations

### 2. Monitoring and Alerting
- Track scheduler health and uptime
- Monitor task execution success rates
- Set up alerts for failed tasks

### 3. Error Handling
- Implement comprehensive retry logic
- Use circuit breakers for external dependencies
- Graceful degradation when services are unavailable

### 4. Security
- Secure credential storage
- Network security for remote tasks
- Access control for scheduler management

### 5. Scalability
- Distribute tasks across multiple workers
- Implement task queuing for high loads
- Resource monitoring and auto-scaling

## Advanced Features

### Conditional Scheduling
```python
@task
def check_business_conditions() -> bool:
    """Check if business conditions allow task execution."""
    # Example: Check if stock market is open
    # Check if there are pending deployments
    # Check system load
    return True

@workflow
def conditional_task_execution():
    """Execute tasks based on business conditions."""
    if check_business_conditions():
        return run_business_critical_task()
    else:
        return skip_task_with_reason("Business conditions not met")
```

### Dynamic Scheduling
```python
@task
def adjust_schedule_based_on_load(current_load: float) -> str:
    """Dynamically adjust schedule based on system load."""
    if current_load > 80:
        return "*/30 * * * *"  # Every 30 minutes if high load
    elif current_load > 50:
        return "*/15 * * * *"  # Every 15 minutes if medium load
    else:
        return "*/5 * * * *"   # Every 5 minutes if low load
```

## Next Steps

- Explore [Multi-Step Data Processing](../intermediate/multi-step-processing.md) for complex workflows
- Learn about [Distributed Computing Patterns](../intermediate/distributed-patterns.md)
- Check out [Performance Optimization](../advanced/performance-optimization.md) techniques

## Summary

You've learned how to:
- Build time-based and cron-like scheduling systems
- Create automated backup and maintenance workflows
- Implement monitoring and alerting systems
- Handle time zones and business logic in scheduling
- Deploy production-ready automation systems

These patterns provide the foundation for building sophisticated automation systems that can handle complex scheduling requirements while maintaining reliability and observability.
