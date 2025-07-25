-- Create multiple databases for different environments
-- Development database (created by default via POSTGRES_DB)

-- Test database
CREATE DATABASE flux_test;

-- Create test user for isolated testing
CREATE USER flux_test_user WITH PASSWORD 'flux_test_password';
GRANT ALL PRIVILEGES ON DATABASE flux_test TO flux_test_user;

-- Enable commonly used extensions
\c flux_test;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";