# AGENT.md

Tool-agnostic guidance for AI coding assistants (Claude Code, Cursor, Copilot, Codex, Aider, …) working on Flux. For commands, architecture, and gotchas, read `CLAUDE.md` first — this file does not duplicate that material; it covers *how* to make changes safely.

## Operating principles

1. **Trust the test suite over your intuition.** Flux is event-sourced and distributed. Edge cases (resume after worker eviction, replay determinism, schedule polling, claim races) are tested but not always obvious from the code. Run the relevant tests after every behavioral change, not just at the end.
2. **Prefer the smallest correct change.** This codebase has accumulated many subsystems (security, observability, agents, console, services, MCP). Touch only what your task requires; avoid drive-by refactors and "while I'm here" cleanups.
3. **Don't paper over warnings.** If a test surfaces a deprecation or a config-missing error, fix the root cause (or seed config explicitly) instead of catching/suppressing.
4. **Match existing style.** Ruff (line length 100, double quotes) is the formatter of record; pre-commit is the lint gate. Type hints are expected on public functions; `from __future__ import annotations` is the convention.

## Required workflow for any change

```
1. branch off main           – never commit directly to main
2. understand the surface    – read the touched module + its tests + adjacent CLAUDE.md / AGENT.md
3. make the change           – include unit tests; add/extend e2e tests when touching server/worker/CLI
4. bump pyproject.toml       – patch for fixes, minor for features (CI enforces this)
5. poetry run pre-commit run --all-files
6. poetry run pytest tests/ --ignore=tests/e2e
7. poetry run pytest tests/e2e/ -m "not ollama"
8. open a PR – describe the why, not the what
9. respond to review – commit and push fixes before replying to comments
```

The version bump in step 4 is enforced by `.github/workflows/pull-request.yml::version-check`. PRs without it fail before tests run.

## Testing discipline

- **Unit first, E2E for system behavior.** Anything that reaches across server ↔ worker, CLI ↔ server, or scheduler ↔ catalog should have an E2E test. See `tests/e2e/conftest.py` for the `cli` fixture pattern.
- **One workflow registration per E2E test module.** Worker module-cache collisions across tests are a known footgun (commit `33c7a7b`). When E2E tests register the same workflow name from different fixture files, prefer module-scoped fixtures or distinct names.
- **Replay determinism.** When you change task/workflow event emission, replay an existing execution (`workflow.run(execution_id=...)`) and confirm the new event log is a strict superset / consistent rewrite. Determinism tests live in `tests/examples/test_determinism.py`.
- **Don't add tests that need network access** unless they're properly skipped when the dependency is missing (see how `@pytest.mark.ollama` is handled in `tests/e2e/conftest.py`).
- **Don't commit databases.** `*.db`, `*.db-wal`, `*.db-shm`, `test.db` are gitignored — verify with `git status` before staging.

## Editing the public surface

- New decorator options on `@task` / `@workflow` need:
  - the option plumbed through `_with_options` and `__init__` in `flux/task.py` or `flux/workflow.py`
  - a corresponding field on `WorkflowInfo` / persistence model if it survives across server boundaries
  - serialization via `flux/encoders.py` if it travels in events / requests
  - documentation in the relevant `docs/advanced-features/*.md`
- New CLI subcommands go in `flux/cli.py`; follow the existing `--format json|simple`, `--server-url`, and Click group conventions. Add a method to `tests/e2e/conftest.py::FluxCLI` so E2E tests can drive it.
- New HTTP endpoints in `flux/server.py` should:
  - declare permissions via `Depends(require_permission("..."))` (skip only with deliberate justification)
  - accept and emit Pydantic models (don't return raw dicts)
  - respect the same rate-limiter / CORS / auth middleware as their neighbours

## Configuration & secrets

- The shipped `flux.toml` deliberately omits `bootstrap_token` and `encryption_key`. Never re-introduce default values for either — both are security-sensitive and the server now auto-generates the bootstrap token on first start.
- New config keys belong on a `BaseConfig` subclass in `flux/config.py`; `pydantic-settings` picks them up automatically once they're declared. Document them in `flux.toml` (commented if optional) and in `README.md` if they affect operators.
- Don't read environment variables directly from feature code. Go through `Configuration.get().settings.<group>.<key>` so tests can override via `Configuration.get().override(...)`.

## Working with workflows from inside an agent

If your task involves *running* a workflow (rather than editing the framework):

- Inline runs (`workflow.run(...)`) are fine for examples and one-shot scripts; they auto-register the workflow.
- For anything resembling production behavior, start `flux start server` + `flux start worker` and exercise the CLI / HTTP API. The `cli` fixture in `tests/e2e/conftest.py` is the cleanest example of orchestrating that.
- Workflow source files must be importable from a `.py` file (see `flux/workflow.py::_ensure_registered`); a workflow defined in a notebook cell or `exec`'d string can't be auto-registered.

## Documentation

- `README.md` is the public face — keep examples in sync with the actual decorator surface; broken README snippets are caught by `tests/test_validate_examples.py`.
- `docs/` is published to MkDocs (`mkdocs.yml`). Major features need an entry under `docs/advanced-features/`.
- Internal design specs that don't ship belong under `.claude/docs/` (gitignored) — not in the repo's tracked docs.

## Things to avoid

- **Don't add `Co-Authored-By:` lines** to commits or PR descriptions on this repo.
- **Don't bypass pre-commit** with `--no-verify`. If a hook fails, fix the underlying issue or update the hook config in `.pre-commit-config.yaml` deliberately.
- **Don't downgrade Python.** The project is Python 3.14; the `pyupgrade` hook still runs `--py39-plus` for syntactic conservatism, but runtime targets 3.14 features.
- **Don't introduce `uv`.** The repo uses Poetry exclusively; `uv.lock` is gitignored to keep this unambiguous.
- **Don't add inline comments that restate the code.** Reserve comments for *why* something is non-obvious (e.g. workarounds for cross-DB FK enforcement, race-condition fixes, security guards). The existing codebase models this well — match its tone.
- **Don't add backwards-compat shims for removed APIs** unless you have a concrete external caller in mind. Internal refactors should remove the old path, not deprecate it.
