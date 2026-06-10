# Contributing to Flux

Thanks for your interest in contributing! This guide covers the practical
workflow. For a deeper tour of the architecture, see `CLAUDE.md` and `AGENT.md`.

## Prerequisites

- **Python 3.12+** (CI tests 3.12, 3.13, and 3.14).
- **Poetry** for dependency management. `uv` is not used (`uv.lock` is gitignored).

## Setup

```bash
poetry install                          # base dev environment
poetry install --extras observability   # what CI installs for lint/unit
```

## Development workflow

1. **Branch** off `main`.
2. **Make your change**, following the conventions already in the surrounding
   code (naming, error handling, test style).
3. **Bump the version** in `pyproject.toml`. CI enforces that every PR raises the
   version above `main` — patch for fixes, minor for features.
4. **Run the checks locally** before pushing (CI runs the same):

   ```bash
   poetry run pre-commit run --all-files            # lint + format + type check
   poetry run pytest tests/ --ignore=tests/e2e      # unit suite
   poetry run pytest tests/e2e/ -m "not ollama" -v  # end-to-end suite
   ```

   The `make lint`, `make format`, and `make check` targets wrap these.

5. **Open a PR.** Keep it focused; describe the change and how you verified it.

## Conventions

- **Don't bypass pre-commit** with `--no-verify`. If a hook fails, fix the cause
  or update `.pre-commit-config.yaml` deliberately.
- **Don't add `Co-Authored-By:` lines** to commits or PRs.
- **Test files are `test_*.py`** (enforced by `name-tests-test`), except under
  `tests/*/fixtures/`.
- **Stay 3.12-compatible.** `pyupgrade` runs `--py312-plus`; PEP 695 syntax is
  fine, but avoid 3.13+-only features.
- **Comment the *why*, not the *what*.** Reserve comments for non-obvious
  constraints (race fixes, cross-DB quirks, security guards).

## Tests

- `tests/flux/` — framework unit tests.
- `tests/examples/` — runs every example in `examples/` through the inline path.
- `tests/e2e/` — spawns a real server + worker; the `cli` fixture in
  `tests/e2e/conftest.py` is the canonical setup.
- `tests/security/` — auth, permissions, providers.

Markers: `e2e`, `ollama`, `postgresql`, `integration`, `slow` (see
`pyproject.toml`).

## Reporting security issues

Please follow `SECURITY.md` — do not open public issues for vulnerabilities.
