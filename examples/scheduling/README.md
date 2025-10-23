# Flux Scheduling Examples

This directory contains practical examples of Flux's scheduling functionality, demonstrating common real-world use cases.

## Examples

### 📊 `daily_report.py` - Daily Sales Report
**Use Case**: Generate business reports every weekday morning
- **Schedule**: `cron("0 9 * * MON-FRI")` - 9 AM UTC on weekdays
- **Tasks**: Fetch sales data, send email report
- **Pattern**: Daily business reporting automation

```bash
# Run example
python examples/scheduling/daily_report.py

# Expected output: Sales data and email notification
```

### 💾 `simple_backup.py` - Database Backup
**Use Case**: Automatic database backups for data protection
- **Schedule**: `interval(hours=6)` - Every 6 hours
- **Tasks**: Create backup, upload to cloud storage
- **Pattern**: Recurring maintenance operations

```bash
# Run example
python examples/scheduling/simple_backup.py

# Expected output: Backup file info and cloud upload status
```

### 🏥 `health_check.py` - System Health Monitoring
**Use Case**: Monitor system components and alert on issues
- **Schedule**: `cron("*/15 * * * *")` - Every 15 minutes
- **Tasks**: Check database, check APIs, send alerts if unhealthy
- **Pattern**: Continuous monitoring and alerting

```bash
# Run example
python examples/scheduling/health_check.py

# Expected output: Health status of database and APIs
```

### 🔄 `data_sync.py` - ETL Data Pipeline
**Use Case**: Extract, transform, load data between systems
- **Schedule**: `interval(hours=2)` - Every 2 hours
- **Tasks**: Extract from source, transform data, load to target
- **Pattern**: ETL and data synchronization workflows

```bash
# Run example
python examples/scheduling/data_sync.py

# Expected output: ETL pipeline results with record counts
```

### 📈 `daily_business_reports.py` - Comprehensive Business Analytics
**Use Case**: Advanced daily business intelligence reporting
- **Schedule**: `cron("0 9 * * MON-FRI")` - Weekday mornings
- **Tasks**: Fetch metrics, calculate KPIs, generate reports, notify stakeholders
- **Pattern**: Complex multi-stage analytics workflow

```bash
# Run example
python examples/scheduling/daily_business_reports.py

# Expected output: Detailed business metrics and KPI analysis
```

## Running the Examples

### Test Individual Examples
```bash
# Set Python path and run any example
PYTHONPATH=. python examples/scheduling/daily_report.py
```

### Validate All Examples
```bash
# Run validation script
python tests/validate_examples.py
```

### Create Schedules from Examples

1. **Start Flux services**:
```bash
flux start server
flux start worker
```

2. **Register workflows** (schedules are created automatically):
```bash
flux workflow register examples/scheduling/daily_report.py
```

When you register a workflow with a `schedule` parameter in the decorator, Flux automatically creates a schedule with the naming pattern `{workflow_name}_auto`. For example:
- `daily_sales_report` → `daily_sales_report_auto`
- `health_check` → `health_check_auto`

3. **View auto-created schedules**:
```bash
flux schedule list
```

4. **Manual schedule creation** (optional - only needed for custom schedules or input data):
```bash
# Create a custom schedule with different timing
flux schedule create daily_sales_report custom_evening \
  --cron "0 18 * * MON-FRI" \
  --timezone "UTC" \
  --description "Evening sales report"

# Create schedule with input data
flux schedule create database_backup weekly_full \
  --interval-hours 168 \
  --input '{"backup_type": "full"}' \
  --description "Weekly full backup"
```

## Schedule Patterns

### Cron Schedules
- `"0 9 * * MON-FRI"` - Weekdays at 9 AM
- `"*/15 * * * *"` - Every 15 minutes
- `"0 0 * * SUN"` - Weekly on Sunday at midnight
- `"0 2 1 * *"` - Monthly on 1st at 2 AM

### Interval Schedules
- `interval(minutes=30)` - Every 30 minutes
- `interval(hours=6)` - Every 6 hours
- `interval(hours=24)` - Daily
- `interval(days=7)` - Weekly

## Use Case Categories

### 📊 **Business Intelligence**
- Daily/weekly/monthly reports
- KPI dashboards
- Sales analytics
- Performance metrics

### 🔧 **Operations & Maintenance**
- Database backups
- Log cleanup
- Certificate renewal
- System updates

### 📡 **Monitoring & Alerting**
- Health checks
- Performance monitoring
- Error rate tracking
- SLA monitoring

### 🔄 **Data Processing**
- ETL pipelines
- Data synchronization
- Batch processing
- Data validation

### 🌐 **External Integrations**
- API synchronization
- Third-party data feeds
- Webhook processing
- File processing

## Best Practices

1. **Use appropriate timezone**: Always specify timezone for global deployments
2. **Handle failures gracefully**: Include error handling and retry logic
3. **Monitor execution**: Use execution history to track performance
4. **Resource management**: Consider execution time and system load
5. **Testing**: Test workflows manually before scheduling

## Next Steps

- Contribute your own scheduling examples!
