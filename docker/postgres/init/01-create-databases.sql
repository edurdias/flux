-- Idempotent test-database bootstrap.
--
-- Runs on first init of the postgres data dir. Written to be safe whether or
-- not the container's POSTGRES_DB / POSTGRES_USER env already created the test
-- database and role (the test compose profile sets them; the dev profile does
-- not), so it never aborts init on an "already exists" error.

-- Test database (only if it does not already exist).
SELECT 'CREATE DATABASE flux_test'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'flux_test')\gexec

-- Test role (only if it does not already exist).
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'flux_test_user') THEN
        CREATE ROLE flux_test_user LOGIN PASSWORD 'flux_test_password';
    END IF;
END
$$;

GRANT ALL PRIVILEGES ON DATABASE flux_test TO flux_test_user;

-- PostgreSQL 15+: the `public` schema is no longer writable by non-owner roles
-- by default, so a plain GRANT on the database is not enough for SQLAlchemy's
-- create_all to create tables. Make the test role own `public` (and its
-- database) so schema DDL works regardless of who created the database.
\connect flux_test
ALTER DATABASE flux_test OWNER TO flux_test_user;
ALTER SCHEMA public OWNER TO flux_test_user;
GRANT ALL ON SCHEMA public TO flux_test_user;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
