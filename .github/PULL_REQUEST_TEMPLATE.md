<!--
Contributing guide: CONTRIBUTING.md
Architecture tour: CLAUDE.md · Agent conventions: AGENTS.md
-->

## Why

<!-- What problem does this solve? Link the issue if there is one (Fixes #123). -->

## What changed

<!-- The shape of the change, not a file-by-file listing. -->

## How it was verified

<!-- Commands you ran, plus anything you exercised manually. -->

- [ ] `poetry run pre-commit run --all-files`
- [ ] `poetry run pytest tests/ --ignore=tests/e2e`
- [ ] `poetry run pytest tests/e2e/ -m "not ollama and not network" -v`

## Checklist

- [ ] Version bumped in `pyproject.toml` (patch for fixes, minor for features) — **CI fails without this**
- [ ] Tests added or updated for the new behavior
- [ ] Docs updated (`docs/`, `README.md`, or `flux.toml` comments) if the change is user-facing
- [ ] Alembic revision added alongside any ORM column change, with `HEAD` updated in both
      `tests/flux/test_migrations.py` and `tests/flux/test_migrations_postgresql.py`
