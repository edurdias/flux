# PostgreSQL Support for Flux

Flux now supports PostgreSQL as an alternative database backend to SQLite, providing enhanced scalability, concurrent access, and production-ready database features.

## Features

- **Multiple Database Backends**: Choose between SQLite (default) and PostgreSQL
- **Environment Variable Support**: Secure credential management with `${VAR}` interpolation
- **Connection Pooling**: Optimized PostgreSQL connection management
- **Zero Breaking Changes**: Existing SQLite functionality remains unchanged
- **Docker Integration**: Complete Docker development environment
- **Comprehensive Testing**: Unit and integration tests for PostgreSQL functionality

## Quick Start

### 1. Installation

Install Flux with PostgreSQL support:

```bash
pip install 'flux-core[postgresql]'
```

Or using Poetry:

```bash
poetry install --extras postgresql
```

### 2. Configuration

#### Environment Variables
```bash
export DB_USER=flux_user
export DB_PASSWORD=your_secure_password
export DB_HOST=localhost
export DB_NAME=flux_db
```

#### Configuration File (`flux.toml`)
```toml
database_url = "postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:5432/${DB_NAME}"
database_type = "postgresql"
database_pool_size = 10
database_max_overflow = 20
```

### 3. Development with Docker

Start PostgreSQL development environment:

```bash
make dev-postgres
```

Or manually:

```bash
docker-compose -f docker-compose.yml -f docker/profiles/postgresql.yml up
```

## Configuration Options

### Database URL Formats

**SQLite (default)**:
```toml
database_url = "sqlite:///.flux/flux.db"
```

**PostgreSQL with static credentials**:
```toml
database_url = "postgresql://user:password@localhost:5432/flux_db"
```

**PostgreSQL with environment variables**:
```toml
database_url = "postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:5432/${DB_NAME}"
```

### Connection Pool Settings

```toml
database_pool_size = 5              # Base connection pool size
database_max_overflow = 10          # Additional connections when needed
database_pool_timeout = 30          # Connection timeout in seconds
database_pool_recycle = 3600        # Recycle connections after 1 hour
database_health_check_interval = 300 # Health check interval in seconds
```

### Environment Variables

All configuration can be overridden with environment variables using the `FLUX_` prefix:

```bash
export FLUX_DATABASE_URL="postgresql://user:pass@host:5432/db"
export FLUX_DATABASE_TYPE="postgresql"
export FLUX_DATABASE_POOL_SIZE=15
export FLUX_DATABASE_MAX_OVERFLOW=25
```

## Docker Development

### Available Profiles

- **SQLite**: `docker/profiles/sqlite.yml` - Lightweight development
- **PostgreSQL**: `docker/profiles/postgresql.yml` - Full PostgreSQL setup
- **PostgreSQL Test**: `docker/profiles/postgresql-test.yml` - Optimized for testing
- **Monitoring**: `docker/profiles/monitoring.yml` - Adds pgAdmin and metrics
- **CI**: `docker/profiles/ci.yml` - Optimized for continuous integration

### Usage Examples

```bash
# SQLite development (default)
docker-compose up

# PostgreSQL development
docker-compose -f docker-compose.yml -f docker/profiles/postgresql.yml up

# PostgreSQL with monitoring tools
docker-compose -f docker-compose.yml -f docker/profiles/postgresql.yml -f docker/profiles/monitoring.yml up

# Testing setup
docker-compose -f docker-compose.yml -f docker/profiles/postgresql-test.yml up
```

### Monitoring Tools

When using the monitoring profile:

- **pgAdmin**: http://localhost:5050 (admin@flux.dev / admin)
- **PostgreSQL Exporter**: http://localhost:9187/metrics

## Testing

### Running Tests

```bash
# All PostgreSQL tests (starts test database automatically)
make test-postgresql

# Unit tests only (no database required)
make test-postgresql-unit

# Integration tests only
make test-postgresql-integration

# Manual testing with specific database
FLUX_DATABASE_URL="postgresql://user:pass@host:5432/db" pytest tests/flux/integration/
```

### Test Structure

```
tests/
├── flux/
│   ├── fixtures/              # Test fixtures and utilities
│   ├── test_*postgresql*.py   # PostgreSQL unit tests
│   └── integration/           # PostgreSQL integration tests
```

### Coverage

```bash
# PostgreSQL test coverage
make coverage-postgresql

# View coverage report
open htmlcov/index.html
```

## Database Management

### Database Setup Architecture

Flux uses a **simplified PostgreSQL setup** that leverages SQLAlchemy for all application-level database operations:

- **SQL Scripts**: Handle only database/user creation and basic system configuration (requires admin privileges)
- **SQLAlchemy**: Manages all application data structures (tables, indexes, constraints, relationships)
- **No Custom Schemas**: All tables use the default `public` schema for simplicity
- **Application Health Checks**: Database connectivity verification through `PostgreSQLRepository.health_check()`

This approach ensures:
- ✅ Clear separation between infrastructure and application concerns
- ✅ Easier maintenance and debugging
- ✅ Better integration with ORM patterns
- ✅ Reduced complexity in Docker setup

### Development Database

```bash
# Start PostgreSQL
make postgres-up

# Connect to database
make postgres-shell

# View logs
make postgres-logs

# Stop PostgreSQL
make postgres-down
```

### Test Database

```bash
# Start test database
make postgres-test-up

# Connect to test database
make postgres-test-shell

# Stop test database
make postgres-test-down
```

## Migration from SQLite

### Data Migration

Currently, Flux supports selecting database backends but does not provide automatic data migration tools. To switch from SQLite to PostgreSQL:

1. **Export existing workflows** using Flux CLI
2. **Configure PostgreSQL** connection
3. **Re-import workflows** to PostgreSQL

### Gradual Migration

You can run both databases simultaneously:

1. Keep existing SQLite setup for production
2. Set up PostgreSQL for new environments
3. Gradually migrate workflows as needed

## Production Deployment

### Security Best Practices

1. **Use Environment Variables**: Never hardcode credentials
2. **SSL Connections**: Configure `sslmode=require` in connection URL
3. **Connection Limits**: Set appropriate pool sizes for your workload
4. **Monitoring**: Use PostgreSQL monitoring tools

### Example Production Configuration

```toml
database_url = "postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:5432/${DB_NAME}?sslmode=require"
database_type = "postgresql"
database_pool_size = 20
database_max_overflow = 30
database_pool_timeout = 30
database_pool_recycle = 3600
```

### Environment Variables for Production

```bash
export DB_USER=flux_prod_user
export DB_PASSWORD=super_secure_password
export DB_HOST=postgres.example.com
export DB_NAME=flux_production
export FLUX_DATABASE_POOL_SIZE=20
export FLUX_DATABASE_MAX_OVERFLOW=30
```

## Troubleshooting

### Common Issues

#### 1. PostgreSQL Driver Not Found
```
Error: PostgreSQL driver not installed
```
**Solution**: Install PostgreSQL support:
```bash
pip install 'flux-core[postgresql]'
```

#### 2. Connection Refused
```
Error: Failed to connect to PostgreSQL database
```
**Solution**:
- Verify PostgreSQL is running
- Check connection parameters
- Verify network connectivity

#### 3. Authentication Failed
```
Error: authentication failed for user
```
**Solution**:
- Verify username and password
- Check PostgreSQL `pg_hba.conf` configuration
- Ensure user has database permissions

#### 4. Database Does Not Exist
```
Error: database "flux_db" does not exist
```
**Solution**:
- Create database: `createdb flux_db`
- Or use initialization scripts in Docker setup

### Health Checks

```python
from flux.models import RepositoryFactory

# Test database connection
repo = RepositoryFactory.create_repository()
is_healthy = repo.health_check()
print(f"Database healthy: {is_healthy}")
```

### Debug Mode

Enable SQL query logging:

```toml
debug = true
```

Or via environment:

```bash
export FLUX_DEBUG=true
```

## Performance Considerations

### Connection Pooling

- **pool_size**: Base number of connections (default: 5)
- **max_overflow**: Additional connections when needed (default: 10)
- **pool_timeout**: Time to wait for connection (default: 30s)
- **pool_recycle**: Recycle connections after time (default: 3600s)

### Recommended Settings

**Development**:
```toml
database_pool_size = 5
database_max_overflow = 10
```

**Production (Light)**:
```toml
database_pool_size = 10
database_max_overflow = 20
```

**Production (Heavy)**:
```toml
database_pool_size = 20
database_max_overflow = 30
```

## Contributing

### Development Setup

```bash
# Clone repository
git clone https://github.com/edurdias/flux.git
cd flux

# Install with PostgreSQL support
make install-postgres

# Set up PostgreSQL development environment
make dev-postgres

# Run PostgreSQL tests
make test-postgresql
```

### Testing Changes

```bash
# Run unit tests
make test-postgresql-unit

# Run integration tests
make test-postgresql-integration

# Run all tests
make ci-test
```

## Support

- **Documentation**: [Flux Documentation](https://docs.flux.dev)
- **Issues**: [GitHub Issues](https://github.com/edurdias/flux/issues)
- **Discussions**: [GitHub Discussions](https://github.com/edurdias/flux/discussions)
