# Database Migrations

Flux manages its database schema with [Alembic](https://alembic.sqlalchemy.org/).
Schema changes ship as migration scripts inside the package
(`flux/migrations/versions/`), and the database is brought up to date
automatically — there is no manual drop-and-recreate step.

## How it works

On startup (the first time a server, worker, or inline run opens the database)
Flux brings the schema to the latest revision. Three cases are handled
automatically:

| Database state | What happens |
|---|---|
| **Fresh / empty** | The full migration chain runs, creating the current schema. |
| **Already managed by Alembic** | Any pending migrations are applied. |
| **Pre-Alembic** (created by an older Flux that used `create_all`) | Stamped at the baseline revision, then upgraded in place — your data is preserved. |

On PostgreSQL the migration step is guarded by a `pg_advisory_lock`, so multiple
servers/workers starting simultaneously cannot run migrations at the same time.
SQLite is single-node, so no lock is needed.

## Upgrading

Just deploy the new version and start it — migrations run on first connection.
For control over timing (for example, running migrations once before rolling out
multiple replicas), use the CLI:

```bash
flux db upgrade     # apply all pending migrations
flux db current     # show the database's current revision
flux db history     # list available revisions, newest first
```

## Backups

Migrations modify schema in place. As with any schema change, **back up the
database before upgrading** a production deployment. Note that execution
input/output, event values, and schedule input are HMAC-signed with your
encryption key — back up
`FLUX_SECURITY__ENCRYPTION__ENCRYPTION_KEY` alongside the database, or that data
becomes unreadable after a restore.

## For contributors

After changing the ORM models in `flux/models.py`, generate a migration:

```bash
poetry run alembic -c /dev/null \
  --raiseerr revision --autogenerate -m "describe change"
```

(or hand-write one under `flux/migrations/versions/`). Migrations are applied to
SQLite in CI's unit suite and to PostgreSQL in the PostgreSQL CI lane, including
a legacy pre-Alembic database that is stamped and upgraded — see
`tests/flux/test_migrations.py` and `tests/flux/test_migrations_postgresql.py`.
Keep migrations static (don't reflect from the live models) so old revisions
behave consistently regardless of later model changes.
