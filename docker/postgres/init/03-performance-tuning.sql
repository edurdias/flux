-- Basic performance tuning for development environment

-- Configure connection limits for development
ALTER SYSTEM SET max_connections = 50;
ALTER SYSTEM SET shared_buffers = '128MB';
ALTER SYSTEM SET work_mem = '4MB';

-- Enable auto-vacuum
ALTER SYSTEM SET autovacuum = on;

-- Reload configuration
SELECT pg_reload_conf();