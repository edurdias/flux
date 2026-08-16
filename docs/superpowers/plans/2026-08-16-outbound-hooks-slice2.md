# Outbound Hooks Slice 2 (Declaration Paths 2 & 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish outbound hooks (issue #248) by implementing declaration paths 2 (workflow-declared, `@workflow.with_options(hooks=[...])`) and 3 (agent-declared, `AgentDefinition.hooks`) from `docs/specs/2026-08-14-outbound-hooks-spec.md`, reusing — not reinventing — the impersonation-security model slice 1 built.

**Architecture:** A `hook.run(on=, workflow=, principal=, ...)` factory (mirroring `flux.routing.score(...)`) builds a plain, JSON-serializable spec dict, real at decoration time and re-derived via AST at registration time (the codebase's established dual-nature pattern for declarative options). Workflow registration statically extracts declared hooks, checks scope confinement and impersonation rights, then reconciles them into the `hooks` table by a derived name (create-or-replace, never delete-and-recreate, so a still-declared hook keeps its delivery history across redeploys). Agent definitions gain the same `hooks` field and the same reconciliation, but with a *runtime* owner filter instead of a static scope check, since every agent session runs the same `agents/agent_chat` workflow. The four impersonation-check predicates slice 1 built as route-local closures in `flux/api/hook_routes.py` are promoted to `Server` methods so both new registration paths can call the exact same checks path 1 uses.

**Tech Stack:** Python 3.12+, SQLAlchemy ORM + Alembic migrations, FastAPI routes via `*RoutesMixin` composition, `ast`-based static source extraction (no `exec` of untrusted source before authorization).

## Global Constraints

- Python 3.12+ only; no 3.13+-only syntax (PEP 696 defaults, etc.).
- Never commit directly to `main`; all work lands via PR from the existing branch `feat/outbound-hooks-slice2`.
- Bump `pyproject.toml` version on every PR — this is a feature, so **minor**: `0.85.0` → `0.86.0`. Do this once, in the final task.
- No AI attribution anywhere (commit messages, PR description): no `Co-Authored-By:`, no "Generated with", no session/tool URLs.
- Run `poetry run pre-commit run --all-files` before every push (never `--no-verify`); run a **cold** `poetry run pre-commit run mypy --all-files` before push — the incremental cache has masked real errors before this session.
- Comments explain the *why* only; no restating what the code does.
- Test files are `test_*.py`, never `*_test.py`.
- **Migration discipline:** a schema change ships its Alembic revision and its ORM column in the same task/commit. Update `HEAD` in both `tests/flux/test_migrations.py` and `tests/flux/test_migrations_postgresql.py` in that same commit.
- **The slice-1 security invariant, verbatim, applies to every new way a hook can be created, rebound, or fired:** *"Any route that can cause an execution to run as the hook's principal requires the right to borrow it (`principal:<subject>:impersonate`); disabling and deleting never can."* Every declared hook's `principal` must pass `_require_may_fire_as` (impersonation grant) and `_require_runnable_target` (the principal must hold `workflow:<ns>:<wf>:run` on its target) before any row is written — exactly the checks `POST /hooks` already runs.
- **Permission escalation:** declaring hooks on a workflow or agent requires `hook:*:create` in addition to the base `workflow:*:register` / `agent:*:create`+`agent:*:update` permission — the `requires_code_upload_permission` pattern `AgentDefinition` already uses for `tools_file`/`workflow_file`/`skills_dir`.
- **Scope confinement differs by path, per the spec:** workflow-declared (path 2) selectors must *statically* name only the declaring workflow's own `namespace`/`name` — checked once at registration, raising `SyntaxError` (→ HTTP 400) otherwise. Agent-declared (path 3) selectors carry no such static restriction (every agent session runs `agents/agent_chat`, so selector text cannot discriminate between agents); instead a *runtime* filter matches the firing execution's `agent` against the hook's `owner_ref` before a delivery is ever written.
- **Lifecycle:** both new paths are create-or-replace-by-derived-name on every registration (never delete-and-recreate — `hook_deliveries.hook_id` cascades on hook delete, so blindly wiping and rewriting rows would destroy in-flight delivery history for a hook that is still declared) and delete-with-owner on workflow/agent delete. This must be **real and tested** — do not copy the schedule auto-lifecycle's gaps (no delete-with-workflow, no tests); the schedule gap is being filed as a separate follow-up issue, not fixed here and not silently inherited here.
- An unparseable `hooks=[...]` declaration must raise `SyntaxError` at registration, never silently drop — the same reasoning `_extract_routing` already documents: a hook mints an execution under a stored principal, so registering it with different semantics than declared is a security bug, not a compatibility nicety.

---

## File Structure

New files:
- `flux/hooks/declarations.py` — the `hook.run(...)` factory and the workflow scope-confinement check.
- `flux/migrations/versions/0028_agent_hooks.py` — `agents.hooks` column.
- `tests/flux/hooks/test_declarations.py`
- `tests/flux/test_workflow_hooks.py`
- `tests/flux/hooks/test_owned_reconciliation.py`
- `tests/flux/test_agent_hooks_admin.py`
- `tests/flux/hooks/test_agent_owned_matching.py`
- `tests/e2e/fixtures/declared_hook_workflows.py`

Modified files (by task, see each task's **Files** block):
`flux/api/hook_routes.py`, `flux/hooks/__init__.py`, `flux/workflow.py`, `flux/catalogs.py`, `flux/hooks/registry.py`, `flux/config.py`, `flux/api/workflow_routes.py`, `flux/agents/types.py`, `flux/models.py`, `flux/agents/manager.py`, `flux/api/admin_routes.py`, `flux/hooks/selectors.py`, `flux/hooks/envelope.py`, `tests/flux/test_migrations.py`, `tests/flux/test_migrations_postgresql.py`, `CLAUDE.md`, `pyproject.toml`.

---

### Task 1: Promote hook impersonation-check predicates to reusable `Server` methods

Pure refactor: slice 1 built `_require_bindable_principal`, `_require_may_fire_as`, `_require_principal_can_run`, `_require_runnable_target` as closures nested inside `HookRoutesMixin._register_hook_routes`. Later tasks in this slice need the exact same checks from `workflow_routes.py` and `admin_routes.py`, which are sibling mixins composed onto the same `Server` instance. Promote the four functions to plain methods on `HookRoutesMixin` (still reachable as `self._require_...` from any mixin), taking their former closure variables (`auth_config`, `auth_service`, `principal_registry`) as explicit parameters instead. No behavior changes — every existing test in `tests/security/test_hook_authz.py` and `tests/flux/test_hook_routes.py` must pass unchanged.

**Files:**
- Modify: `flux/api/hook_routes.py:124-296` (the four closures and their call sites)
- Test: `tests/security/test_hook_authz.py` (regression, unchanged), `tests/flux/test_hook_routes.py` (regression, unchanged)

**Interfaces:**
- Produces: `Server._require_bindable_principal(self, principal: str, *, principal_registry) -> None`, `Server._require_may_fire_as(self, identity: FluxIdentity, principal: str, *, auth_config, auth_service, principal_registry) -> None` (async), `Server._require_principal_can_run(self, principal: str, namespace: str, workflow_name: str) -> None` (async, no extra params — delegates to `self._authorize_hook`), `Server._require_runnable_target(self, principal: str, workflow_ref: str) -> None` (async, no extra params). Tasks 6 and 7 call these four directly.

- [ ] **Step 1: Confirm the regression baseline is green before touching anything**

Run: `poetry run pytest tests/security/test_hook_authz.py tests/flux/test_hook_routes.py -v`
Expected: all PASS. This is the safety net for the refactor — no new test is written in this task.

- [ ] **Step 2: Move the four closures out of `_register_hook_routes` into methods**

In `flux/api/hook_routes.py`, delete these four nested function definitions from inside `_register_hook_routes` (currently lines ~189-280):

```python
        def _require_bindable_principal(principal: str) -> None:
            ...
        async def _require_may_fire_as(identity: FluxIdentity, principal: str) -> None:
            ...
        async def _require_principal_can_run(
            principal: str,
            namespace: str,
            workflow_name: str,
        ) -> None:
            ...
        async def _require_runnable_target(principal: str, workflow_ref: str) -> None:
            ...
```

Add them as methods on `HookRoutesMixin`, placed immediately before `_register_hook_routes` (so the "reusable" section reads first), with the exact same bodies and docstrings, adjusted only for their new signatures:

```python
class HookRoutesMixin:
    def _require_bindable_principal(
        self: Server,
        principal: str,
        *,
        principal_registry,
    ) -> None:
        """A usable service account, not merely a subject that resolves.

        A hook names its principal by *subject*, as a schedule names its
        service account and as the permission grammar and the principals
        API do. Each way of being unusable gets its own answer, because
        they have different fixes: a subject nobody issued is a typo, a
        human principal is the wrong kind of identity for unattended
        work, and a disabled one is a state someone can restore. Reporting
        any of them as "lacks permission" sends an operator hunting for a
        grant that was never the issue.
        """
        found = principal_registry.find(principal, "flux") if principal_registry else None
        if found is None or getattr(found, "type", None) != "service_account":
            raise HTTPException(
                status_code=400,
                detail=f"Service account '{principal}' not found",
            )
        if not getattr(found, "enabled", True):
            raise HTTPException(
                status_code=400,
                detail=f"Service account '{principal}' is disabled",
            )
        if getattr(found, "banned", False):
            raise HTTPException(
                status_code=400,
                detail=f"Service account '{principal}' is banned",
            )

    async def _require_may_fire_as(
        self: Server,
        identity: FluxIdentity,
        principal: str,
        *,
        auth_config,
        auth_service,
        principal_registry,
    ) -> None:
        """**Any route that can cause an execution to run as the hook's
        principal requires the right to borrow it; disabling and deleting
        never can.**

        That sentence is the whole authorization model for hooks, and a
        new caller of this method belongs on one side of it or the other.
        A hook fires its target under the bound principal's roles with an
        execution token minted for it, so ``hook:*``/`workflow:*:register`/
        `agent:*:create` — none of which decide which identity runs what —
        must never be enough on their own. Promoted from a route-local
        closure (originally `flux/api/hook_routes.py`) so the workflow- and
        agent-declared paths run the exact same check the server-side CRUD
        path does.

        With auth off there is no principal to resolve and nothing ever
        dereferences the stored value, so the whole check stands down —
        again as the schedule path does.
        """
        if not auth_config.enabled or auth_service is None:
            return
        self._require_bindable_principal(principal, principal_registry=principal_registry)
        required = f"principal:{principal}:impersonate"
        if not await auth_service.is_authorized(identity, required):
            raise HTTPException(
                status_code=403,
                detail=f"Permission denied: requires '{required}'",
            )

    async def _require_principal_can_run(
        self: Server,
        principal: str,
        namespace: str,
        workflow_name: str,
    ) -> None:
        """The create-time half of fire-time authorization.

        The drain re-checks this before every delivery, so a hook whose
        principal cannot run its target is not a security hole — it is a
        hook that can only ever dead-letter. Refusing it here is what
        turns a 3am silent non-delivery into an error at the door.
        """
        permission = f"workflow:{namespace}:{workflow_name}:run"
        if not await self._authorize_hook(principal, permission):
            raise HTTPException(
                status_code=403,
                detail=f"Principal '{principal}' lacks permission '{permission}'",
            )

    async def _require_runnable_target(self: Server, principal: str, workflow_ref: str) -> None:
        """A hook's (principal, target) pair as a client supplies it.

        Self-contained (unlike the route-local `_resolve_target`/
        `_require_registered` helpers `test_hook` still uses for its
        409-on-missing-target variant): every caller of this promoted
        method wants the same 404-on-missing-target behavior `POST /hooks`
        always had.
        """
        try:
            namespace, workflow_name = resolve_workflow_ref(workflow_ref)
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid workflow reference '{workflow_ref}': {e}",
            )
        try:
            WorkflowCatalog.create().get(namespace, workflow_name)
        except WorkflowNotFoundError:
            raise HTTPException(status_code=404, detail=f"Workflow '{workflow_ref}' not found")
        await self._require_principal_can_run(principal, namespace, workflow_name)

    def _register_hook_routes(  # type: ignore[misc]
        self: Server,
        api,
        *,
        auth_config,
        auth_service,
        principal_registry,
        limiter,
    ):
        ...
```

`WorkflowCatalog`, `resolve_workflow_ref`, and `WorkflowNotFoundError` are already imported at the top of `flux/api/hook_routes.py` (lines 23-24) — no new imports needed for these methods.

- [ ] **Step 3: Update the four call sites inside `_register_hook_routes` to call the promoted methods**

In `create_hook`, replace:
```python
            await _require_may_fire_as(identity, request.principal)
            await _require_runnable_target(request.principal, request.workflow_ref)
```
with:
```python
            await self._require_may_fire_as(
                identity,
                request.principal,
                auth_config=auth_config,
                auth_service=auth_service,
                principal_registry=principal_registry,
            )
            await self._require_runnable_target(request.principal, request.workflow_ref)
```

In `update_hook`, replace both:
```python
            if rebound or re_aimed or re_enabled:
                await _require_may_fire_as(identity, fields.get("principal", hook.principal))

            if "workflow_ref" in fields or "principal" in fields:
                await _require_runnable_target(
                    fields.get("principal", hook.principal),
                    fields.get("workflow_ref", hook.workflow_ref),
                )
```
with:
```python
            if rebound or re_aimed or re_enabled:
                await self._require_may_fire_as(
                    identity,
                    fields.get("principal", hook.principal),
                    auth_config=auth_config,
                    auth_service=auth_service,
                    principal_registry=principal_registry,
                )

            if "workflow_ref" in fields or "principal" in fields:
                await self._require_runnable_target(
                    fields.get("principal", hook.principal),
                    fields.get("workflow_ref", hook.workflow_ref),
                )
```

In `test_hook`, replace:
```python
            await _require_may_fire_as(identity, hook.principal)
            ...
            await _require_principal_can_run(hook.principal, namespace, workflow_name)
```
with:
```python
            await self._require_may_fire_as(
                identity,
                hook.principal,
                auth_config=auth_config,
                auth_service=auth_service,
                principal_registry=principal_registry,
            )
            ...
            await self._require_principal_can_run(hook.principal, namespace, workflow_name)
```

In `retry_hook_delivery`, replace:
```python
            await _require_may_fire_as(identity, hook.principal)
```
with:
```python
            await self._require_may_fire_as(
                identity,
                hook.principal,
                auth_config=auth_config,
                auth_service=auth_service,
                principal_registry=principal_registry,
            )
```

The route-local `_get_hook`, `_require_hook_permission`, `_validate_selectors`, `_resolve_target`, `_require_registered` closures stay exactly as they are — only the four security predicates move.

- [ ] **Step 4: Run the full regression suite**

Run: `poetry run pytest tests/security/test_hook_authz.py tests/flux/test_hook_routes.py tests/flux/hooks/ -v`
Expected: all PASS, identical results to Step 1.

- [ ] **Step 5: Cold mypy check**

Run: `poetry run pre-commit run mypy --all-files`
Expected: no new errors introduced by the signature changes (the `self: Server` typed-self pattern already used elsewhere in this file must resolve `self._authorize_hook` etc. correctly).

- [ ] **Step 6: Commit**

```bash
git add flux/api/hook_routes.py
git commit -m "refactor(hooks): promote impersonation checks to reusable Server methods"
```

---

### Task 2: `hook.run(...)` declarative factory and workflow scope-confinement check

The counterpart of `flux.routing.score(...)`: a factory returning a plain, JSON-serializable dict (not an object), so it round-trips through workflow metadata and a Pydantic field without a custom encoder, and can be rebuilt identically from AST-extracted literals. `principal` is required (not defaulted to the declarer), matching `HookRequest.principal`'s "the hook outlives the request that created it" rule for path 1.

**Files:**
- Create: `flux/hooks/declarations.py`
- Modify: `flux/hooks/__init__.py`
- Test: `tests/flux/hooks/test_declarations.py`

**Interfaces:**
- Consumes: `flux.hooks.selectors.validate_selector(selector: str) -> None` (raises `ValueError`).
- Produces: `hook.run(*, on: str, workflow: str, principal: str, name: str | None = None, max_attempts: int = 5) -> dict` returning `{"on": str, "workflow": str, "principal": str, "name": str | None, "max_attempts": int}`. `validate_workflow_scope(selector: str, namespace: str, name: str) -> None` (raises `ValueError`). Task 4 (AST extraction) calls `hook.run(**fields)` for validation; Task 4 also calls `validate_workflow_scope`. Task 5 (registry reconciliation) consumes the dict shape `hook.run` returns.

- [ ] **Step 1: Write the failing tests**

Create `tests/flux/hooks/test_declarations.py`:

```python
"""Unit tests for the ``hook.run(...)`` declarative factory and the
workflow scope-confinement check it shares with AST extraction."""

from __future__ import annotations

import pytest

from flux.hooks.declarations import hook, validate_workflow_scope


class TestHookRun:
    def test_returns_a_plain_json_shaped_dict(self):
        spec = hook.run(
            on="task:release:*:promote_prod:awaiting_approval",
            workflow="ops/notify_slack",
            principal="notifier",
        )

        assert spec == {
            "on": "task:release:*:promote_prod:awaiting_approval",
            "workflow": "ops/notify_slack",
            "principal": "notifier",
            "name": None,
            "max_attempts": 5,
        }

    def test_carries_an_explicit_name_and_max_attempts(self):
        spec = hook.run(
            on="execution:*:*:failed",
            workflow="ops/incident",
            principal="notifier",
            name="my-hook",
            max_attempts=3,
        )

        assert spec["name"] == "my-hook"
        assert spec["max_attempts"] == 3

    def test_principal_is_required(self):
        with pytest.raises(TypeError):
            hook.run(on="execution:*:*:failed", workflow="ops/incident")  # type: ignore[call-arg]

    def test_rejects_a_malformed_selector(self):
        with pytest.raises(ValueError, match="selector"):
            hook.run(on="not-a-selector", workflow="ops/incident", principal="notifier")

    def test_rejects_a_non_positive_max_attempts(self):
        with pytest.raises(ValueError, match="max_attempts"):
            hook.run(
                on="execution:*:*:failed",
                workflow="ops/incident",
                principal="notifier",
                max_attempts=0,
            )


class TestValidateWorkflowScope:
    def test_a_selector_naming_the_declaring_workflow_is_fine(self):
        validate_workflow_scope("execution:release:pipeline:failed", "release", "pipeline")
        validate_workflow_scope(
            "task:release:pipeline:promote_prod:awaiting_approval",
            "release",
            "pipeline",
        )

    def test_a_selector_naming_a_different_workflow_is_rejected(self):
        with pytest.raises(ValueError, match="release/pipeline"):
            validate_workflow_scope("execution:release:other_workflow:failed", "release", "pipeline")

    def test_a_selector_naming_a_different_namespace_is_rejected(self):
        with pytest.raises(ValueError, match="release/pipeline"):
            validate_workflow_scope("execution:ops:pipeline:failed", "release", "pipeline")

    def test_a_wildcard_namespace_is_rejected(self):
        """A workflow may observe itself, never the fleet -- a wildcard in
        the namespace/name position is exactly the fleet-wide subscription
        path 1 exists for."""
        with pytest.raises(ValueError):
            validate_workflow_scope("execution:*:pipeline:failed", "release", "pipeline")

    def test_a_too_short_selector_is_rejected(self):
        with pytest.raises(ValueError):
            validate_workflow_scope("execution:*", "release", "pipeline")
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `poetry run pytest tests/flux/hooks/test_declarations.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flux.hooks.declarations'`.

- [ ] **Step 3: Implement `flux/hooks/declarations.py`**

```python
"""Declarative hook specs: the counterpart of ``flux.routing.score(...)``.

``hook.run(...)`` returns a plain, JSON-serializable dict rather than an
object -- like ``flux.routing.score``, so it can live in workflow metadata
and a Pydantic field (``AgentDefinition.hooks``) without a custom encoder,
and be rebuilt identically from AST-extracted literals at registration time
(``flux.catalogs.DatabaseWorkflowCatalog._extract_hooks``).
"""

from __future__ import annotations

from flux.hooks.selectors import validate_selector


class hook:
    """Namespace for hook declaration factories, used from a workflow's
    ``@workflow.with_options(hooks=[...])`` or an agent's ``hooks`` field.
    """

    @staticmethod
    def run(
        *,
        on: str,
        workflow: str,
        principal: str,
        name: str | None = None,
        max_attempts: int = 5,
    ) -> dict:
        """Declare one hook: fire ``workflow`` as ``principal`` when ``on`` matches.

        ``principal`` is required, not defaulted to the declarer's own
        identity -- whose rights a hook fires under is a decision, not a
        side effect, the same rule ``HookRequest.principal`` enforces for
        the server-side CRUD path. ``name`` is optional: when omitted, the
        owner-scoped reconciliation derives a stable one from the owner and
        this spec's position in its ``hooks`` list.
        """
        validate_selector(on)
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got: {max_attempts}")
        return {
            "on": on,
            "workflow": workflow,
            "principal": principal,
            "name": name,
            "max_attempts": max_attempts,
        }


def validate_workflow_scope(selector: str, namespace: str, name: str) -> None:
    """Raise ``ValueError`` unless ``selector`` observes only this workflow.

    Workflow-declared hooks (declaration path 2) may only watch their own
    executions/tasks -- observing the fleet requires an operator via the
    server-side CRUD path (path 1). Selector segments 1 and 2 are the
    namespace and workflow name for both domains
    (``execution:<ns>:<wf>:...`` / ``task:<ns>:<wf>:...``), so the check is
    domain-independent and does not need ``validate_selector`` to have run
    first.
    """
    parts = selector.split(":")
    if len(parts) < 3 or parts[1] != namespace or parts[2] != name:
        raise ValueError(
            f"hook selector {selector!r} must observe only the declaring "
            f"workflow ({namespace}/{name}); a workflow may observe "
            "itself, not the fleet -- subscribing to another workflow "
            "requires an operator via POST /hooks",
        )
```

- [ ] **Step 4: Export `hook` from `flux/hooks/__init__.py`**

In `flux/hooks/__init__.py`, add the import:

```python
# ruff: noqa: F401
from flux.hooks.declarations import hook
from flux.hooks.selectors import (
    DOMAINS,
    HookEvent,
    events_from_save,
    selector_matches,
    validate_selector,
)
```

- [ ] **Step 5: Run the tests to confirm they pass**

Run: `poetry run pytest tests/flux/hooks/test_declarations.py -v`
Expected: all PASS.

- [ ] **Step 6: Cold mypy check**

Run: `poetry run pre-commit run mypy --all-files`
Expected: no new errors.

- [ ] **Step 7: Commit**

```bash
git add flux/hooks/declarations.py flux/hooks/__init__.py tests/flux/hooks/test_declarations.py
git commit -m "feat(hooks): add the hook.run(...) declarative factory"
```

---

### Task 3: `workflow.with_options(hooks=...)` parameter

**Files:**
- Modify: `flux/workflow.py:37-206`
- Test: `tests/flux/test_workflow_hooks.py`

**Interfaces:**
- Consumes: nothing beyond structural checks (a `hooks` value is validated as a list of dicts shaped like `hook.run(...)`'s return — the real validation of selector/principal already happened when `hook.run(...)` executed at decoration time).
- Produces: `workflow.hooks -> list[dict] | None` property; `workflow(func, ..., hooks=None)` stores `self._hooks`. Task 4 (AST extraction) does **not** consume this property — it re-derives hooks statically from source. This property exists for parity with `routing`/`affinity`/`requests` and for any future in-process consumer of a live `workflow` object.

- [ ] **Step 1: Write the failing tests**

Create `tests/flux/test_workflow_hooks.py`:

```python
"""Unit tests for workflow.with_options(hooks=...) — structural validation
and the .hooks property. Registration-time behavior (AST extraction, scope
confinement, permission checks) is covered in test_catalogs.py-adjacent
tests and tests/flux/hooks/test_owned_reconciliation.py."""

from __future__ import annotations

import pytest

from flux.hooks import hook
from flux.workflow import workflow


class TestWorkflowHooksOption:
    def test_hooks_defaults_to_none(self):
        @workflow
        async def plain(ctx):
            return ctx.input

        assert plain.hooks is None

    def test_hooks_are_stored_and_exposed(self):
        spec = hook.run(
            on="execution:default:with_hooks:failed",
            workflow="ops/notify",
            principal="notifier",
        )

        @workflow.with_options(hooks=[spec])
        async def with_hooks(ctx):
            return ctx.input

        assert with_hooks.hooks == [spec]

    def test_hooks_must_be_a_list(self):
        with pytest.raises(ValueError, match="hooks"):

            @workflow.with_options(hooks={"on": "execution:*"})  # type: ignore[arg-type]
            async def bad(ctx):
                return ctx.input

    def test_hooks_entries_must_be_hook_run_shaped_dicts(self):
        with pytest.raises(ValueError, match="hooks"):

            @workflow.with_options(hooks=[{"not": "a hook spec"}])
            async def bad(ctx):
                return ctx.input
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `poetry run pytest tests/flux/test_workflow_hooks.py -v`
Expected: FAIL — `with_options()` raises `TypeError: with_options() got an unexpected keyword argument 'hooks'`.

- [ ] **Step 3: Add the `hooks` parameter**

In `flux/workflow.py`, add `hooks: list[dict] | None = None` to both `with_options`'s signature and the `workflow.__init__` signature, thread it through `wrapper`, add the structural validation, store it, and expose the property.

In `with_options` (around line 39-51 and 72-88):

```python
    @staticmethod
    def with_options(
        name: str | None = None,
        namespace: str | None = None,
        secret_requests: list[str] | None = None,
        output_storage: OutputStorage | None = None,
        requests: ResourceRequest | None = None,
        affinity: dict[str, str] | list[dict] | None = None,
        schedule: Schedule | None = None,
        durability: str = "durable",
        runner: str | None = None,
        runner_options: dict | None = None,
        routing: dict | None = None,
        hooks: list[dict] | None = None,
    ) -> Callable[[F], workflow]:
        """
        ...
            hooks (list[dict] | None, optional): Outbound hooks this workflow declares, built with ``flux.hooks.hook.run(...)``. Each selector must observe only this workflow — subscribing to another workflow's events requires an operator via ``POST /hooks``. Defaults to None.
        """

        def wrapper(func: F) -> workflow:
            return workflow(
                func=func,
                name=name,
                namespace=namespace,
                secret_requests=secret_requests,
                output_storage=output_storage,
                requests=requests,
                affinity=affinity,
                schedule=schedule,
                durability=durability,
                runner=runner,
                runner_options=runner_options,
                routing=routing,
                hooks=hooks,
            )

        return wrapper
```

In `__init__` (around line 90-162), add the parameter, a structural validation block alongside the existing `routing`/`affinity` checks, and the storage:

```python
    def __init__(
        self,
        func: F,
        name: str | None = None,
        namespace: str | None = None,
        secret_requests: list[str] | None = None,
        output_storage: OutputStorage | None = None,
        requests: ResourceRequest | None = None,
        affinity: dict[str, str] | list[dict] | None = None,
        schedule: Schedule | None = None,
        durability: str = "durable",
        runner: str | None = None,
        runner_options: dict | None = None,
        routing: dict | None = None,
        hooks: list[dict] | None = None,
    ):
        ...  # existing durability/runner_options/runner/routing/affinity checks unchanged
        if hooks is not None and (
            not isinstance(hooks, list)
            or not all(
                isinstance(h, dict) and {"on", "workflow", "principal"} <= set(h)
                for h in hooks
            )
        ):
            raise ValueError(
                f"hooks must be a list of specs built with flux.hooks.hook.run(...), got: {hooks!r}",
            )
        self._func = func
        self._name = name if name else func.__name__
        self._namespace = validate_namespace(namespace)
        self._secret_requests = list(secret_requests) if secret_requests else []
        self._output_storage = output_storage
        self._requests = requests
        self._affinity = affinity
        self._schedule = schedule
        self._durability = durability
        self._runner = runner
        self._routing = routing
        self._hooks = hooks
        wraps(func)(self)
```

Add the property next to `routing`'s (around line 180-183):

```python
    @property
    def hooks(self) -> list[dict] | None:
        return self._hooks
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `poetry run pytest tests/flux/test_workflow_hooks.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full workflow unit suite to confirm no regression**

Run: `poetry run pytest tests/flux/test_workflow.py -v`
Expected: all PASS (this file does not exist under that exact name if the project splits workflow tests differently — run `poetry run pytest tests/flux/ -k workflow -v` instead if `test_workflow.py` is not found, to catch the actual test module).

- [ ] **Step 6: Cold mypy check**

Run: `poetry run pre-commit run mypy --all-files`
Expected: no new errors.

- [ ] **Step 7: Commit**

```bash
git add flux/workflow.py tests/flux/test_workflow_hooks.py
git commit -m "feat(hooks): add workflow.with_options(hooks=...)"
```

---

### Task 4: AST extraction of `hooks=[hook.run(...)]` and scope confinement

Static extraction so the server never executes untrusted uploaded source before authorization — the same reasoning `_extract_routing` documents. Wired into `DatabaseWorkflowCatalog._parse_ast`, shared by both `parse()` and `parse_static()`.

**Files:**
- Modify: `flux/catalogs.py:350-442` (the keyword walk inside `_parse_ast`), plus a new `_extract_hooks` method placed near `_extract_routing` (~line 832)
- Test: extend `tests/flux/test_catalogs.py` (or the file housing `WorkflowCatalog.parse`/`parse_static` tests — locate it first with `grep -rl "def parse_static\|\.parse_static(" tests/flux/*.py`)

**Interfaces:**
- Consumes: `flux.hooks.declarations.hook.run(**fields) -> dict` (Task 2, for validation), `flux.hooks.declarations.validate_workflow_scope(selector, namespace, name) -> None` (Task 2).
- Produces: `WorkflowInfo.metadata["hooks"] -> list[dict]` (only present when the workflow declares hooks) after `catalog.parse()`/`parse_static()`. Task 6 (workflow_routes.py wiring) reads this key.

- [ ] **Step 1: Locate the existing parse/parse_static test file**

Run: `grep -rl "parse_static\|def test_extract_routing\|_extract_routing" tests/flux/*.py`

Use whichever file that returns as the home for this task's new tests (add a new `class TestExtractHooks` / `TestHooksScopeConfinement` section there, following the existing test style for `_extract_routing` in the same file).

- [ ] **Step 2: Write the failing tests**

Add to the file located in Step 1 (adjust the class/import names to match that file's existing conventions for constructing a `DatabaseWorkflowCatalog` and calling `.parse_static(source)`):

```python
class TestExtractHooks:
    def test_a_workflow_declaring_a_confined_hook_registers_it(self, catalog):
        source = b'''
from flux import workflow
from flux.hooks import hook


@workflow.with_options(
    name="notify_on_fail",
    namespace="release",
    hooks=[
        hook.run(
            on="execution:release:notify_on_fail:failed",
            workflow="ops/notify_slack",
            principal="notifier",
        ),
    ],
)
async def notify_on_fail(ctx):
    return ctx.input
'''
        infos = catalog.parse_static(source)
        assert infos[0].metadata["hooks"] == [
            {
                "on": "execution:release:notify_on_fail:failed",
                "workflow": "ops/notify_slack",
                "principal": "notifier",
                "name": None,
                "max_attempts": 5,
            },
        ]

    def test_a_workflow_with_no_hooks_kwarg_has_no_hooks_metadata(self, catalog):
        source = b'''
from flux import workflow


@workflow
async def plain(ctx):
    return ctx.input
'''
        infos = catalog.parse_static(source)
        assert "hooks" not in infos[0].metadata

    def test_a_selector_naming_another_workflow_raises_syntax_error(self, catalog):
        source = b'''
from flux import workflow
from flux.hooks import hook


@workflow.with_options(
    namespace="release",
    hooks=[
        hook.run(
            on="execution:release:some_other_workflow:failed",
            workflow="ops/notify_slack",
            principal="notifier",
        ),
    ],
)
async def notify_on_fail(ctx):
    return ctx.input
'''
        with pytest.raises(SyntaxError, match="release/notify_on_fail"):
            catalog.parse_static(source)

    def test_scope_confinement_is_checked_even_when_hooks_precedes_namespace_in_source(
        self,
        catalog,
    ):
        """`hooks=` can appear before `namespace=` in the decorator's keyword
        list; the namespace used for the confinement check must still be the
        final one, not DEFAULT_NAMESPACE."""
        source = b'''
from flux import workflow
from flux.hooks import hook


@workflow.with_options(
    hooks=[
        hook.run(
            on="execution:release:notify_on_fail:failed",
            workflow="ops/notify_slack",
            principal="notifier",
        ),
    ],
    namespace="release",
    name="notify_on_fail",
)
async def notify_on_fail(ctx):
    return ctx.input
'''
        infos = catalog.parse_static(source)
        assert infos[0].metadata["hooks"][0]["on"] == "execution:release:notify_on_fail:failed"

    def test_a_non_literal_hooks_value_raises_syntax_error(self, catalog):
        source = b'''
from flux import workflow

_HOOKS = []


@workflow.with_options(hooks=_HOOKS)
async def plain(ctx):
    return ctx.input
'''
        with pytest.raises(SyntaxError, match="hooks"):
            catalog.parse_static(source)

    def test_a_non_hook_run_call_in_the_list_raises_syntax_error(self, catalog):
        source = b'''
from flux import workflow


@workflow.with_options(hooks=[dict(on="execution:*", workflow="ops/x", principal="p")])
async def plain(ctx):
    return ctx.input
'''
        with pytest.raises(SyntaxError, match="hook.run"):
            catalog.parse_static(source)

    def test_a_missing_required_hook_run_argument_raises_syntax_error(self, catalog):
        source = b'''
from flux import workflow
from flux.hooks import hook


@workflow.with_options(hooks=[hook.run(on="execution:*:*:failed", workflow="ops/x")])
async def plain(ctx):
    return ctx.input
'''
        with pytest.raises(SyntaxError):
            catalog.parse_static(source)
```

If the located test file has no `catalog` fixture, add one matching however that file already constructs `DatabaseWorkflowCatalog` for its `_extract_routing` tests (check for an existing fixture or inline `DatabaseWorkflowCatalog()` construction and mirror it exactly).

- [ ] **Step 3: Run the tests to confirm they fail**

Run: `poetry run pytest <located_test_file> -k "TestExtractHooks" -v`
Expected: FAIL — `hooks=` currently falls through the keyword walk unmatched (no `KeyError`, but the assertions on `infos[0].metadata["hooks"]` fail with `KeyError: 'hooks'`, and the `SyntaxError` tests fail because nothing raises).

- [ ] **Step 4: Add `_extract_hooks` to `DatabaseWorkflowCatalog`**

Add this method in `flux/catalogs.py` near `_extract_routing` (after `_extract_runner_options`, before `_extract_routing`, or directly after `_extract_routing` — either placement is fine, keep it beside the other `_extract_*` AST helpers):

```python
    def _extract_hooks(self, node: ast.AST) -> list[dict]:
        """Extract a ``hooks=[hook.run(...), ...]`` declaration into its spec list.

        Unlike ``requests``/``affinity``, an unparseable ``hooks`` value
        raises instead of returning None: a hook mints an execution under a
        stored principal, so silently dropping one would register the
        workflow with different privileges than its source declares — the
        same reasoning ``_extract_routing`` documents for routing policies.

        Building through the real ``hook.run(...)`` factory reuses its
        validation (selector well-formedness, required arguments,
        ``max_attempts``). Scope confinement (each selector must observe
        only the declaring workflow) is validated by the caller once
        ``workflow_namespace``/``workflow_name`` are final, not here —
        ``hooks`` can appear before ``namespace`` in the decorator's
        keyword list.
        """
        from flux.hooks.declarations import hook as hook_dsl

        if not isinstance(node, ast.List):
            raise SyntaxError(
                "hooks must be a literal list of hook.run(...) calls; build "
                "it with flux.hooks.hook.run(...) using literal values",
            )

        specs = []
        for element in node.elts:
            if (
                not isinstance(element, ast.Call)
                or not isinstance(element.func, ast.Attribute)
                or not isinstance(element.func.value, ast.Name)
                or element.func.value.id != "hook"
                or element.func.attr != "run"
            ):
                raise SyntaxError(
                    "each hooks[] entry must be a literal hook.run(...) call",
                )
            if element.args:
                raise SyntaxError("hook.run(...) takes only keyword arguments")

            fields: dict[str, Any] = {}
            for kw in element.keywords:
                if kw.arg is None or not isinstance(kw.value, ast.Constant):
                    raise SyntaxError(
                        f"hook.run() argument {kw.arg!r} must be a literal value",
                    )
                fields[kw.arg] = kw.value.value

            try:
                specs.append(hook_dsl.run(**fields))
            except (TypeError, ValueError) as e:
                raise SyntaxError(f"invalid hook.run() call: {e}") from e

        return specs
```

- [ ] **Step 5: Wire `_extract_hooks` and scope confinement into the keyword walk**

In `flux/catalogs.py`, in `_parse_ast` (the loop around line 354-442):

Add `workflow_hooks = None` to the initializer block (alongside `workflow_runner_options = None` at line 359):

```python
                    workflow_requests = None
                    workflow_affinity = None
                    workflow_durability = None
                    workflow_runner = None
                    workflow_routing = None
                    workflow_runner_options = None
                    workflow_hooks = None
```

Add a branch in the `for kw in decorator.keywords:` loop (after the `runner_options` branch, before the loop ends):

```python
                                elif kw.arg == "runner_options":
                                    workflow_runner_options = self._extract_runner_options(
                                        kw.value,
                                    )
                                elif kw.arg == "hooks":
                                    workflow_hooks = self._extract_hooks(kw.value)
```

Add the scope-confinement pass right after `workflow_name` is finalized, before `break`:

```python
                            if not workflow_name:
                                workflow_name = node.name

                            if workflow_hooks:
                                from flux.hooks.declarations import validate_workflow_scope

                                for spec in workflow_hooks:
                                    try:
                                        validate_workflow_scope(
                                            spec["on"],
                                            workflow_namespace,
                                            workflow_name,
                                        )
                                    except ValueError as e:
                                        raise SyntaxError(str(e)) from e

                            break
```

Fold the result into `wf_metadata`, alongside the existing `routing`/`runner_options` folds (around line 425-430):

```python
                        if workflow_routing is not None:
                            wf_metadata = dict(wf_metadata or {})
                            wf_metadata["routing"] = workflow_routing
                        if workflow_runner_options:
                            wf_metadata = dict(wf_metadata or {})
                            wf_metadata["runner_options"] = workflow_runner_options
                        if workflow_hooks:
                            wf_metadata = dict(wf_metadata or {})
                            wf_metadata["hooks"] = workflow_hooks
```

- [ ] **Step 6: Run the tests to confirm they pass**

Run: `poetry run pytest <located_test_file> -k "TestExtractHooks" -v`
Expected: all PASS.

- [ ] **Step 7: Run the full catalogs test suite to confirm no regression**

Run: `poetry run pytest <located_test_file> -v`
Expected: all PASS.

- [ ] **Step 8: Cold mypy check**

Run: `poetry run pre-commit run mypy --all-files`
Expected: no new errors.

- [ ] **Step 9: Commit**

```bash
git add flux/catalogs.py <located_test_file>
git commit -m "feat(hooks): statically extract workflow-declared hooks with scope confinement"
```

---

### Task 5: `HookRegistry` owner-scoped reconciliation

Create-or-replace-by-derived-name, and delete-by-owner — the primitives both declaration paths' registration wiring (Tasks 6 and 7) will call.

**Files:**
- Modify: `flux/hooks/registry.py`
- Modify: `flux/config.py:563-614` (`HooksConfig`)
- Test: `tests/flux/hooks/test_owned_reconciliation.py`

**Interfaces:**
- Consumes: `flux.hooks.registry.HookRegistry.create_hook`/`update_hook`/`delete_hook` (existing, Task-1-unmodified).
- Produces: `HookRegistry.list_owned_hooks(self, *, owner_type: str, owner_ref: str) -> list[HookModel]`, `HookRegistry.reconcile_owned_hooks(self, *, owner_type: str, owner_ref: str, specs: Sequence[dict], created_by: str | None = None) -> list[HookModel]`, `HookRegistry.delete_owned_hooks(self, *, owner_type: str, owner_ref: str) -> int`. Tasks 6 and 7 call `reconcile_owned_hooks` and `delete_owned_hooks`.

- [ ] **Step 1: Write the failing tests**

Create `tests/flux/hooks/test_owned_reconciliation.py`:

```python
"""Owner-scoped hook reconciliation: create-or-replace-by-derived-name, and
delete-by-owner. The property under test throughout is that a hook still
declared across a reconcile call keeps its row (and delivery history);
only a hook that disappeared from the declaration is deleted."""

from __future__ import annotations

from flux.hooks.registry import HookRegistry
from flux.models import HookDeliveryModel, RepositoryFactory


def _spec(on: str, workflow: str = "ops/notify", principal: str = "notifier", **overrides):
    spec = {"on": on, "workflow": workflow, "principal": principal, "name": None, "max_attempts": 5}
    spec.update(overrides)
    return spec


class TestReconcileOwnedHooks:
    def test_first_reconcile_creates_rows_with_derived_names(self, isolated_db):
        registry = HookRegistry.create()

        created = registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[_spec("execution:release:pipeline:failed")],
        )

        assert len(created) == 1
        assert created[0].owner_type == "workflow"
        assert created[0].owner_ref == "release/pipeline"
        assert created[0].name  # non-empty, derived
        assert registry.get_hook(created[0].name).selectors == ["execution:release:pipeline:failed"]

    def test_multiple_specs_get_distinct_derived_names(self, isolated_db):
        registry = HookRegistry.create()

        created = registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[
                _spec("execution:release:pipeline:failed"),
                _spec("execution:release:pipeline:completed"),
            ],
        )

        names = {row.name for row in created}
        assert len(names) == 2

    def test_an_explicit_name_is_used_instead_of_a_derived_one(self, isolated_db):
        registry = HookRegistry.create()

        created = registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[_spec("execution:release:pipeline:failed", name="my-stable-name")],
        )

        assert created[0].name == "my-stable-name"

    def test_reconciling_again_with_the_same_spec_updates_not_recreates(self, isolated_db):
        registry = HookRegistry.create()
        first = registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[_spec("execution:release:pipeline:failed")],
        )
        hook_id = first[0].id

        second = registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[_spec("execution:release:pipeline:failed", max_attempts=9)],
        )

        assert second[0].id == hook_id
        assert second[0].max_attempts == 9

    def test_reconciling_preserves_delivery_history_of_a_still_declared_hook(self, isolated_db):
        registry = HookRegistry.create()
        created = registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[_spec("execution:release:pipeline:failed")],
        )
        hook_id = created[0].id
        with RepositoryFactory.create_repository().session() as session:
            session.add(
                HookDeliveryModel(
                    hook_id=hook_id,
                    event_key="exec-1:ev-1",
                    payload={},
                    status="delivered",
                ),
            )
            session.commit()

        registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[_spec("execution:release:pipeline:failed", max_attempts=2)],
        )

        with RepositoryFactory.create_repository().session() as session:
            assert (
                session.query(HookDeliveryModel).filter_by(hook_id=hook_id).count() == 1
            )

    def test_a_spec_removed_from_the_declaration_deletes_its_row_and_deliveries(self, isolated_db):
        registry = HookRegistry.create()
        created = registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[
                _spec("execution:release:pipeline:failed"),
                _spec("execution:release:pipeline:completed"),
            ],
        )
        removed_id = created[1].id
        with RepositoryFactory.create_repository().session() as session:
            session.add(
                HookDeliveryModel(
                    hook_id=removed_id,
                    event_key="exec-1:ev-1",
                    payload={},
                    status="delivered",
                ),
            )
            session.commit()

        registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[_spec("execution:release:pipeline:failed")],
        )

        assert registry.list_hooks() == [
            row for row in registry.list_hooks() if row.id != removed_id
        ]
        with RepositoryFactory.create_repository().session() as session:
            assert session.query(HookDeliveryModel).filter_by(hook_id=removed_id).count() == 0

    def test_reconciling_with_an_empty_spec_list_deletes_every_owned_hook(self, isolated_db):
        registry = HookRegistry.create()
        registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[_spec("execution:release:pipeline:failed")],
        )

        registry.reconcile_owned_hooks(owner_type="workflow", owner_ref="release/pipeline", specs=[])

        assert registry.list_owned_hooks(owner_type="workflow", owner_ref="release/pipeline") == []

    def test_reconcile_never_touches_a_different_owners_hooks(self, isolated_db):
        registry = HookRegistry.create()
        registry.create_hook(
            name="user-made",
            selectors=["execution:*:*:failed"],
            workflow_ref="ops/incident",
            principal="p",
            owner_type="user",
            owner_ref="admin",
        )

        registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[_spec("execution:release:pipeline:failed")],
        )
        registry.reconcile_owned_hooks(owner_type="workflow", owner_ref="release/pipeline", specs=[])

        assert registry.get_hook("user-made") is not None


class TestDeleteOwnedHooks:
    def test_deletes_every_hook_the_owner_declared(self, isolated_db):
        registry = HookRegistry.create()
        registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[
                _spec("execution:release:pipeline:failed"),
                _spec("execution:release:pipeline:completed"),
            ],
        )

        removed = registry.delete_owned_hooks(owner_type="workflow", owner_ref="release/pipeline")

        assert removed == 2
        assert registry.list_owned_hooks(owner_type="workflow", owner_ref="release/pipeline") == []

    def test_is_a_no_op_for_an_owner_with_no_hooks(self, isolated_db):
        registry = HookRegistry.create()

        assert registry.delete_owned_hooks(owner_type="workflow", owner_ref="release/none") == 0
```

Check `tests/flux/hooks/` for an existing `isolated_db` fixture (used by `test_registry.py` already, per the earlier read of that file) — it should already be available via `conftest.py` in that directory or `tests/flux/conftest.py`; do not redefine it if it already exists.

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `poetry run pytest tests/flux/hooks/test_owned_reconciliation.py -v`
Expected: FAIL with `AttributeError: 'HookRegistry' object has no attribute 'reconcile_owned_hooks'`.

- [ ] **Step 3: Add `auto_hook_suffix` to `HooksConfig`**

In `flux/config.py`, in `HooksConfig` (after `snapshot_ttl_seconds`, around line 613):

```python
    auto_hook_suffix: str = Field(
        default="_hook",
        description=(
            "Suffix for derived names of workflow- and agent-declared hooks "
            "that were not given an explicit name. Mirrors "
            "[flux.scheduling] auto_schedule_suffix."
        ),
    )
```

- [ ] **Step 4: Implement the reconciliation methods**

In `flux/hooks/registry.py`, add these methods to `HookRegistry` (after `delete_hook`):

```python
    def list_owned_hooks(self, *, owner_type: str, owner_ref: str) -> list[HookModel]:
        with self._repository.session() as session:
            return (
                session.query(HookModel)
                .filter_by(owner_type=owner_type, owner_ref=owner_ref)
                .order_by(HookModel.name)
                .all()
            )

    def reconcile_owned_hooks(
        self,
        *,
        owner_type: str,
        owner_ref: str,
        specs: Sequence[dict],
        created_by: str | None = None,
    ) -> list[HookModel]:
        """Create-or-replace-by-derived-name; delete rows no longer declared.

        Keying on name -- not wiping and recreating every owned row -- is
        what lets a hook that is still declared keep its delivery history
        across a redeploy. Only rows for specs that disappeared from the
        declaration are removed, and only they lose their deliveries (via
        the ``hook_deliveries.hook_id`` FK cascade).
        """
        suffix = Configuration.get().settings.hooks.auto_hook_suffix
        base_name = owner_ref.rsplit("/", 1)[-1]

        desired: dict[str, dict] = {}
        for index, spec in enumerate(specs):
            name = spec.get("name") or f"{base_name}{suffix}_{index}"
            desired[name] = spec

        existing = {
            row.name: row
            for row in self.list_owned_hooks(owner_type=owner_type, owner_ref=owner_ref)
        }

        result = []
        for name, spec in desired.items():
            fields = {
                "selectors": [spec["on"]],
                "workflow_ref": spec["workflow"],
                "principal": spec["principal"],
                "max_attempts": spec.get("max_attempts", 5),
            }
            if name in existing:
                result.append(self.update_hook(name, **fields))
            else:
                result.append(
                    self.create_hook(
                        name=name,
                        selectors=fields["selectors"],
                        workflow_ref=fields["workflow_ref"],
                        principal=fields["principal"],
                        owner_type=owner_type,
                        owner_ref=owner_ref,
                        max_attempts=fields["max_attempts"],
                        created_by=created_by,
                    ),
                )

        for stale_name in set(existing) - set(desired):
            self.delete_hook(stale_name)

        return result

    def delete_owned_hooks(self, *, owner_type: str, owner_ref: str) -> int:
        """Delete every hook this owner declared -- workflow delete / agent delete."""
        count = 0
        for row in self.list_owned_hooks(owner_type=owner_type, owner_ref=owner_ref):
            self.delete_hook(row.name)
            count += 1
        return count
```

- [ ] **Step 5: Run the tests to confirm they pass**

Run: `poetry run pytest tests/flux/hooks/test_owned_reconciliation.py -v`
Expected: all PASS.

- [ ] **Step 6: Run the full hooks unit suite to confirm no regression**

Run: `poetry run pytest tests/flux/hooks/ -v`
Expected: all PASS.

- [ ] **Step 7: Cold mypy check**

Run: `poetry run pre-commit run mypy --all-files`
Expected: no new errors.

- [ ] **Step 8: Commit**

```bash
git add flux/hooks/registry.py flux/config.py tests/flux/hooks/test_owned_reconciliation.py
git commit -m "feat(hooks): add owner-scoped hook reconciliation"
```

---

### Task 6: Wire workflow-declared hooks into registration and deletion

Registration: escalation permission (`hook:*:create`) + impersonation (`_require_may_fire_as`) + runnable-target (`_require_runnable_target`) checks for every declared hook's principal, all **before** `catalog.save(...)` commits anything, then `reconcile_owned_hooks` after save. Deletion: a *real, tested* delete-with-workflow — the gap the schedule auto-lifecycle has and this slice deliberately does not copy (per the confirmed decision to build hooks correctly and file the schedule gap separately).

**Files:**
- Modify: `flux/api/workflow_routes.py:59-114` (`workflows_save`) and `:798-849` (`workflow_delete_ns`)
- Test: `tests/flux/test_hook_routes.py` is route-scoped to `/hooks*` — add a new file instead: `tests/flux/test_workflow_hooks_registration.py`

**Interfaces:**
- Consumes: `Server._require_may_fire_as`/`_require_runnable_target` (Task 1), `HookRegistry.reconcile_owned_hooks`/`delete_owned_hooks` (Task 5), `WorkflowInfo.metadata["hooks"]` (Task 4).
- Produces: nothing new consumed by later tasks — this is a leaf wiring task.

- [ ] **Step 1: Write the failing tests**

Create `tests/flux/test_workflow_hooks_registration.py`. This mirrors the fixture/client setup already used in `tests/flux/test_hook_routes.py` (a `Server` + `TestClient` over a fresh SQLite DB, auth off — permission-string enforcement itself is covered separately, matching how `test_hook_routes.py` vs `tests/security/test_hook_authz.py` split that concern):

```python
"""Registration-time wiring for workflow-declared hooks (declaration path
2): escalation + impersonation + runnable-target checks before any row is
written, owner-scoped reconciliation on save, and a real delete-with-
workflow on workflow removal. Auth is off here, matching
tests/flux/test_hook_routes.py; permission-string enforcement for the
escalation/impersonation gates is exercised in
tests/security/test_hook_authz.py-adjacent tests added by this task in the
same style."""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from flux.config import Configuration


@pytest.fixture
def db(tmp_path):
    Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'wf_hooks.db'}")
    from flux.models import DatabaseRepository

    DatabaseRepository._engines.clear()
    yield
    DatabaseRepository._engines.clear()


@pytest.fixture
def server_instance(db):
    from flux.server import Server

    return Server(host="localhost", port=8000)


@pytest.fixture
def client(server_instance):
    return TestClient(server_instance._create_api())


def _seed_workflow(namespace: str, name: str) -> str:
    from flux.models import RepositoryFactory, WorkflowModel

    repo = RepositoryFactory.create_repository()
    workflow_id = f"{namespace}/{name}"
    with repo.session() as session:
        session.add(
            WorkflowModel(
                id=workflow_id,
                name=name,
                version=1,
                imports=[],
                source=b"async def p(ctx): pass",
                namespace=namespace,
            ),
        )
        session.commit()
    return workflow_id


def _seed_principal():
    from flux.security.principals import PrincipalRegistry
    from flux.models import RepositoryFactory

    repo = RepositoryFactory.create_repository()
    registry = PrincipalRegistry(session_factory=lambda: repo.session())
    return registry.create(type="service_account", subject="notifier", external_issuer="flux")


def _upload(client, source: str, filename: str = "wf.py"):
    return client.post(
        "/workflows",
        files={"file": (filename, io.BytesIO(source.encode()), "text/x-python")},
    )


_SOURCE = """
from flux import workflow
from flux.hooks import hook


@workflow.with_options(
    namespace="release",
    hooks=[
        hook.run(
            on="execution:release:notify_on_fail:failed",
            workflow="ops/notify",
            principal="notifier",
        ),
    ],
)
async def notify_on_fail(ctx):
    return ctx.input
"""


class TestWorkflowDeclaredHookRegistration:
    def test_registering_creates_the_declared_hook(self, client):
        _seed_workflow("ops", "notify")
        _seed_principal()

        resp = _upload(client, _SOURCE)

        assert resp.status_code == 200, resp.text
        listed = client.get("/hooks").json()
        owned = [h for h in listed["hooks"] if h["owner_type"] == "workflow"]
        assert len(owned) == 1
        assert owned[0]["owner_ref"] == "release/notify_on_fail"
        assert owned[0]["selectors"] == ["execution:release:notify_on_fail:failed"]
        assert owned[0]["principal"] == "notifier"

    def test_registering_without_a_runnable_target_is_rejected(self, client):
        _seed_principal()
        # "ops/notify" is never seeded, so the principal cannot run it.

        resp = _upload(client, _SOURCE)

        assert resp.status_code in (400, 403, 404), resp.text
        assert client.get("/hooks").json()["hooks"] == []

    def test_reregistering_with_a_changed_selector_updates_the_same_row(self, client):
        _seed_workflow("ops", "notify")
        _seed_principal()
        _upload(client, _SOURCE)
        first_id = client.get("/hooks").json()["hooks"][0]["id"]

        changed = _SOURCE.replace(
            "execution:release:notify_on_fail:failed",
            "execution:release:notify_on_fail:completed",
        )
        resp = _upload(client, changed)

        assert resp.status_code == 200, resp.text
        hooks = client.get("/hooks").json()["hooks"]
        assert len(hooks) == 1
        assert hooks[0]["id"] == first_id
        assert hooks[0]["selectors"] == ["execution:release:notify_on_fail:completed"]

    def test_reregistering_without_the_hooks_kwarg_removes_it(self, client):
        _seed_workflow("ops", "notify")
        _seed_principal()
        _upload(client, _SOURCE)
        assert len(client.get("/hooks").json()["hooks"]) == 1

        without_hooks = """
from flux import workflow


@workflow.with_options(namespace="release", name="notify_on_fail")
async def notify_on_fail(ctx):
    return ctx.input
"""
        resp = _upload(client, without_hooks)

        assert resp.status_code == 200, resp.text
        assert client.get("/hooks").json()["hooks"] == []

    def test_deleting_the_workflows_only_version_deletes_its_owned_hooks(self, client):
        _seed_workflow("ops", "notify")
        _seed_principal()
        _upload(client, _SOURCE)
        assert len(client.get("/hooks").json()["hooks"]) == 1

        resp = client.delete("/workflows/release/notify_on_fail")

        assert resp.status_code == 200, resp.text
        assert client.get("/hooks").json()["hooks"] == []

    def test_deleting_one_of_several_versions_keeps_owned_hooks(self, client):
        _seed_workflow("ops", "notify")
        _seed_principal()
        _upload(client, _SOURCE)  # v1
        _upload(client, _SOURCE)  # v2, same declaration
        hooks_before = client.get("/hooks").json()["hooks"]
        assert len(hooks_before) == 1

        resp = client.delete("/workflows/release/notify_on_fail?version=1")

        assert resp.status_code == 200, resp.text
        assert len(client.get("/hooks").json()["hooks"]) == 1
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `poetry run pytest tests/flux/test_workflow_hooks_registration.py -v`
Expected: FAIL — no hooks are created on registration yet (`listed["hooks"] == []` where the test expects one), and `workflow_delete_ns` doesn't touch hooks yet.

- [ ] **Step 3: Wire registration into `workflows_save`**

In `flux/api/workflow_routes.py`, add the import:

```python
from flux.hooks.registry import HookRegistry
```

Modify `workflows_save` (currently lines 59-113): insert an escalation+impersonation block after the existing per-namespace register-permission loop and before `catalog.enrich(...)`, and a reconciliation loop after `catalog.save(...)`:

```python
                if auth_service is not None and auth_config.enabled:
                    for wf in workflows:
                        required = f"workflow:{wf.namespace}:*:register"
                        if not await auth_service.is_authorized(identity, required):
                            raise HTTPException(
                                status_code=403,
                                detail=f"Permission denied: requires '{required}'",
                            )

                # Declaring hooks widens what registering this workflow can
                # do: each fires its target under a stored principal, so
                # workflow:*:register alone must not mint one -- the same
                # requires_code_upload_permission pattern agent definitions
                # use for tools_file/workflow_file/skills_dir. Checked before
                # enrich()/save() so an unauthorized declaration never
                # touches the database.
                for wf in workflows:
                    hook_specs = (wf.metadata or {}).get("hooks") or []
                    if not hook_specs:
                        continue
                    if auth_service is not None and auth_config.enabled:
                        required = "hook:*:create"
                        if not await auth_service.is_authorized(identity, required):
                            raise HTTPException(
                                status_code=403,
                                detail=f"Permission denied: requires '{required}'",
                            )
                    for spec in hook_specs:
                        await self._require_may_fire_as(
                            identity,
                            spec["principal"],
                            auth_config=auth_config,
                            auth_service=auth_service,
                            principal_registry=principal_registry,
                        )
                        await self._require_runnable_target(spec["principal"], spec["workflow"])

                # Authorized: now it is safe to import the module for metadata.
                catalog.enrich(source, workflows)

                result = catalog.save(workflows)
                logger.debug(f"Saved workflows: {[w.qualified_name for w in workflows]}")

                self._auto_create_schedules_from_source(source, workflows)

                # Every registered version reconciles the same owner-scoped
                # rows (hooks are not version-scoped, matching schedules) --
                # unconditionally, so a redeploy that drops the hooks= kwarg
                # removes previously-declared hooks instead of orphaning them.
                for wf in workflows:
                    HookRegistry.create().reconcile_owned_hooks(
                        owner_type="workflow",
                        owner_ref=f"{wf.namespace}/{wf.name}",
                        specs=(wf.metadata or {}).get("hooks") or [],
                        created_by=identity.subject if identity else None,
                    )

                return result
```

(The `try`/`except SyntaxError`/`except HTTPException`/`except Exception` block already wrapping this whole body is unchanged — `_require_may_fire_as`/`_require_runnable_target` raise `HTTPException`, which the existing `except HTTPException: raise` clause already re-raises correctly.)

- [ ] **Step 4: Wire delete-with-workflow into `workflow_delete_ns`**

In `flux/api/workflow_routes.py`, modify `workflow_delete_ns` (currently lines 798-849):

```python
                catalog.delete(namespace, workflow_name, version)

                # A specific-version delete only removes the workflow
                # entirely when it was the last version left; hooks are
                # keyed on the workflow's identity, not a version, so
                # cleanup mirrors that -- deleting one of several versions
                # must not touch hooks still owned by the surviving ones.
                still_exists = True
                try:
                    catalog.get(namespace, workflow_name)
                except WorkflowNotFoundError:
                    still_exists = False

                if not still_exists:
                    removed = HookRegistry.create().delete_owned_hooks(
                        owner_type="workflow",
                        owner_ref=f"{namespace}/{workflow_name}",
                    )
                    if removed:
                        logger.info(
                            f"Deleted {removed} hook(s) owned by workflow "
                            f"'{namespace}/{workflow_name}'",
                        )

                logger.info(f"Successfully deleted workflow '{namespace}/{workflow_name}'")
```

(`WorkflowNotFoundError` is already imported at the top of this file; `logger.info(f"Deleting workflow...")` and the `return {...}` that follow are unchanged.)

- [ ] **Step 5: Run the tests to confirm they pass**

Run: `poetry run pytest tests/flux/test_workflow_hooks_registration.py -v`
Expected: all PASS.

- [ ] **Step 6: Run the full workflow routes + hooks regression suite**

Run: `poetry run pytest tests/flux/test_hook_routes.py tests/security/test_hook_authz.py tests/flux/hooks/ tests/flux/test_workflow_hooks_registration.py -v`

Also run whatever file houses the existing `/workflows` route tests (locate with `grep -rl "workflow_delete_ns\|def test_.*delete.*workflow\|/workflows/{namespace}" tests/flux/*.py`) to confirm the delete route change did not regress non-hook-owning workflow deletes.

Expected: all PASS.

- [ ] **Step 7: Cold mypy check**

Run: `poetry run pre-commit run mypy --all-files`
Expected: no new errors.

- [ ] **Step 8: Commit**

```bash
git add flux/api/workflow_routes.py tests/flux/test_workflow_hooks_registration.py
git commit -m "feat(hooks): wire workflow-declared hooks into registration and delete-with-workflow"
```

---

### Task 7: Agent-declared hooks (`AgentDefinition.hooks`)

Same escalation rule (`requires_code_upload_permission` gains `hooks`), same replace-on-update lifecycle, `owner_type="agent"`. No static scope-confinement check (per spec — that comes at runtime in Task 8); the same impersonation and runnable-target checks as path 2 still apply.

**Files:**
- Modify: `flux/agents/types.py` (`AgentDefinition`)
- Modify: `flux/models.py` (`AgentModel`)
- Create: `flux/migrations/versions/0028_agent_hooks.py`
- Modify: `tests/flux/test_migrations.py`, `tests/flux/test_migrations_postgresql.py` (`HEAD`)
- Modify: `flux/agents/manager.py` (`DatabaseAgentManager`)
- Modify: `flux/api/admin_routes.py` (`admin_create_agent`, `admin_update_agent`, `admin_delete_agent`)
- Test: `tests/flux/test_agent_hooks_admin.py`

**Interfaces:**
- Consumes: `hook.run(**fields) -> dict` (Task 2, via `AgentDefinition`'s field validator), `Server._require_may_fire_as`/`_require_runnable_target` (Task 1), `HookRegistry.reconcile_owned_hooks`/`delete_owned_hooks` (Task 5).
- Produces: `AgentDefinition.hooks -> list[dict]` (default `[]`), `AgentDefinition.requires_code_upload_permission()` now also true when `hooks` is non-empty. Nothing further consumes this — leaf task alongside Task 6.

- [ ] **Step 1: Write the failing migration/model tests**

In `tests/flux/test_migrations.py`, change:
```python
HEAD = "0027_hooks"
```
to:
```python
HEAD = "0028_agent_hooks"
```

In `tests/flux/test_migrations_postgresql.py`, change the same constant the same way.

- [ ] **Step 2: Run the migration tests to confirm they fail**

Run: `poetry run pytest tests/flux/test_migrations.py -v`
Expected: FAIL — `current_revision(engine) == HEAD` assertions fail because the actual head is still `"0027_hooks"`.

- [ ] **Step 3: Add the `agents.hooks` column to the ORM model**

In `flux/models.py`, in `AgentModel` (currently lines 367-402), add the column after `long_term_memory`:

```python
    long_term_memory = Column(JSON, nullable=True)
    # Agent-declared outbound hooks (declaration path 3). Nullable at the DB
    # level like every other column a migration added to this pre-existing
    # table (see 0025_execution_name for the same pattern) -- the Python-
    # side default below is what keeps every ORM-created row a list, not
    # None; DatabaseAgentManager._to_definition normalizes a legacy NULL row
    # the same way it already does for `tools`/`mcp_servers`/`agents`.
    hooks = Column(JSON, nullable=True, default=list)
```

- [ ] **Step 4: Write the migration**

Create `flux/migrations/versions/0028_agent_hooks.py`:

```python
"""Add agents.hooks: agent-declared outbound hooks (declaration path 3).

Revision ID: 0028_agent_hooks
Revises: 0027_hooks
Create Date: 2026-08-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_agent_hooks"
down_revision: str | None = "0027_hooks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "agents"
_COLUMN = "hooks"


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}
    if _COLUMN not in existing:
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}
    if _COLUMN in existing:
        op.drop_column(_TABLE, _COLUMN)
```

- [ ] **Step 5: Run the migration tests to confirm they pass**

Run: `poetry run pytest tests/flux/test_migrations.py -v`
Expected: all PASS.

- [ ] **Step 6: Write the failing `AgentDefinition`/admin-route tests**

Create `tests/flux/test_agent_hooks_admin.py`:

```python
"""Agent-declared hooks (declaration path 3): AgentDefinition.hooks
validation/escalation, and the admin routes' registration/replace/delete
wiring. Auth is off here, matching the rest of tests/flux/ -- permission
enforcement itself follows the same pattern tests/security/test_hook_authz.py
already exercises for path 1 and is not re-proven per-path here."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from flux.agents.types import AgentDefinition
from flux.config import Configuration


class TestAgentDefinitionHooks:
    def test_hooks_defaults_to_empty(self):
        definition = AgentDefinition(name="a", model="openai/gpt-4o", system_prompt="hi")
        assert definition.hooks == []

    def test_hooks_entries_are_validated_and_normalized(self):
        definition = AgentDefinition(
            name="a",
            model="openai/gpt-4o",
            system_prompt="hi",
            hooks=[{"on": "execution:agents:agent_chat:completed", "workflow": "ops/x", "principal": "p"}],
        )
        assert definition.hooks == [
            {
                "on": "execution:agents:agent_chat:completed",
                "workflow": "ops/x",
                "principal": "p",
                "name": None,
                "max_attempts": 5,
            },
        ]

    def test_a_malformed_hook_selector_is_rejected(self):
        with pytest.raises(ValidationError):
            AgentDefinition(
                name="a",
                model="openai/gpt-4o",
                system_prompt="hi",
                hooks=[{"on": "not-a-selector", "workflow": "ops/x", "principal": "p"}],
            )

    def test_requires_code_upload_permission_is_true_when_hooks_declared(self):
        definition = AgentDefinition(
            name="a",
            model="openai/gpt-4o",
            system_prompt="hi",
            hooks=[{"on": "execution:agents:agent_chat:completed", "workflow": "ops/x", "principal": "p"}],
        )
        assert definition.requires_code_upload_permission() is True

    def test_requires_code_upload_permission_is_false_with_no_hooks(self):
        definition = AgentDefinition(name="a", model="openai/gpt-4o", system_prompt="hi")
        assert definition.requires_code_upload_permission() is False


@pytest.fixture
def db(tmp_path):
    Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'agent_hooks.db'}")
    from flux.models import DatabaseRepository

    DatabaseRepository._engines.clear()
    yield
    DatabaseRepository._engines.clear()


@pytest.fixture
def server_instance(db):
    from flux.server import Server

    return Server(host="localhost", port=8000)


@pytest.fixture
def client(server_instance):
    return TestClient(server_instance._create_api())


def _seed_workflow(namespace: str, name: str) -> str:
    from flux.models import RepositoryFactory, WorkflowModel

    repo = RepositoryFactory.create_repository()
    workflow_id = f"{namespace}/{name}"
    with repo.session() as session:
        session.add(
            WorkflowModel(
                id=workflow_id,
                name=name,
                version=1,
                imports=[],
                source=b"async def p(ctx): pass",
                namespace=namespace,
            ),
        )
        session.commit()
    return workflow_id


def _seed_principal():
    from flux.security.principals import PrincipalRegistry
    from flux.models import RepositoryFactory

    repo = RepositoryFactory.create_repository()
    registry = PrincipalRegistry(session_factory=lambda: repo.session())
    return registry.create(type="service_account", subject="notifier", external_issuer="flux")


def _agent_payload(**overrides):
    payload = {
        "name": "helper",
        "model": "openai/gpt-4o",
        "system_prompt": "hi",
        "hooks": [
            {"on": "execution:agents:agent_chat:completed", "workflow": "ops/notify", "principal": "notifier"},
        ],
    }
    payload.update(overrides)
    return payload


class TestAgentDeclaredHookRegistration:
    def test_creating_an_agent_creates_its_declared_hook(self, client):
        _seed_workflow("ops", "notify")
        _seed_principal()

        resp = client.post("/admin/agents", json=_agent_payload())

        assert resp.status_code == 200, resp.text
        owned = [h for h in client.get("/hooks").json()["hooks"] if h["owner_type"] == "agent"]
        assert len(owned) == 1
        assert owned[0]["owner_ref"] == "helper"

    def test_creating_without_a_runnable_target_is_rejected(self, client):
        _seed_principal()
        # "ops/notify" is never seeded.

        resp = client.post("/admin/agents", json=_agent_payload())

        assert resp.status_code in (400, 403, 404), resp.text
        assert client.get("/hooks").json()["hooks"] == []

    def test_updating_replaces_the_same_row(self, client):
        _seed_workflow("ops", "notify")
        _seed_principal()
        client.post("/admin/agents", json=_agent_payload())
        first_id = client.get("/hooks").json()["hooks"][0]["id"]

        updated = _agent_payload(
            hooks=[
                {
                    "on": "execution:agents:agent_chat:failed",
                    "workflow": "ops/notify",
                    "principal": "notifier",
                },
            ],
        )
        resp = client.put("/admin/agents/helper", json=updated)

        assert resp.status_code == 200, resp.text
        hooks = client.get("/hooks").json()["hooks"]
        assert len(hooks) == 1
        assert hooks[0]["id"] == first_id
        assert hooks[0]["selectors"] == ["execution:agents:agent_chat:failed"]

    def test_updating_without_hooks_removes_them(self, client):
        _seed_workflow("ops", "notify")
        _seed_principal()
        client.post("/admin/agents", json=_agent_payload())
        assert len(client.get("/hooks").json()["hooks"]) == 1

        resp = client.put(
            "/admin/agents/helper",
            json={"name": "helper", "model": "openai/gpt-4o", "system_prompt": "hi"},
        )

        assert resp.status_code == 200, resp.text
        assert client.get("/hooks").json()["hooks"] == []

    def test_deleting_the_agent_deletes_its_owned_hooks(self, client):
        _seed_workflow("ops", "notify")
        _seed_principal()
        client.post("/admin/agents", json=_agent_payload())
        assert len(client.get("/hooks").json()["hooks"]) == 1

        resp = client.delete("/admin/agents/helper")

        assert resp.status_code == 200, resp.text
        assert client.get("/hooks").json()["hooks"] == []
```

- [ ] **Step 7: Run the tests to confirm they fail**

Run: `poetry run pytest tests/flux/test_agent_hooks_admin.py -v`
Expected: FAIL — `AgentDefinition(...)` rejects the unknown `hooks` kwarg (Pydantic extra-field behavior; check whether the model's config forbids or ignores extras — either way the round-trip assertions fail), and no hook rows are created by the admin routes.

- [ ] **Step 8: Add `hooks` to `AgentDefinition`**

In `flux/agents/types.py`, add the field (after `long_term_memory`, around line 33):

```python
    long_term_memory: dict[str, Any] | None = None
    hooks: list[dict[str, Any]] = Field(default_factory=list)
```

Add a field validator (after `validate_reasoning_effort`, before `validate_long_term_memory`):

```python
    @field_validator("hooks")
    @classmethod
    def validate_hooks(cls, v: list[Any]) -> list[dict]:
        from flux.hooks.declarations import hook

        try:
            return [hook.run(**entry) if isinstance(entry, dict) else entry for entry in v]
        except (TypeError, ValueError) as e:
            raise ValueError(f"invalid hook declaration: {e}") from e
```

Update `requires_code_upload_permission`:

```python
    def requires_code_upload_permission(self) -> bool:
        """Return True if this definition ships content that escalates beyond ``agent:*:create``.

        ``tools_file``/``workflow_file`` are exec'd on workers; an inline ``skills_dir`` bundle
        ships arbitrary file content materialized on the worker filesystem; ``hooks`` fires
        another workflow under a stored principal.
        """
        return bool(
            self.tools_file or self.workflow_file or self.has_skills_bundle() or self.hooks,
        )
```

Update `payload_ships_code`:

```python
def payload_ships_code(value: Any) -> bool:
    """``requires_code_upload_permission`` for a raw, unvalidated payload.

    Agent definitions are mirrored into the config store as plain JSON, so the
    same rule has to hold there without constructing an AgentDefinition.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return False
    if not isinstance(value, dict):
        return False
    if value.get("tools_file") or value.get("workflow_file") or value.get("hooks"):
        return True
    skills = value.get("skills_dir")
    if isinstance(skills, str):
        try:
            return isinstance(json.loads(skills), dict)
        except (json.JSONDecodeError, ValueError):
            return False
    return isinstance(skills, dict)
```

- [ ] **Step 9: Wire `AgentManager` reconciliation**

In `flux/agents/manager.py`, add the import:

```python
from flux.hooks.registry import HookRegistry
```

Update `DatabaseAgentManager.create` (append after the existing config-mirror write):

```python
    def create(self, definition: AgentDefinition) -> None:
        with self.session() as session:
            existing = session.get(AgentModel, definition.name)
            if existing:
                raise ValueError(f"Agent '{definition.name}' already exists")
            model = AgentModel(**definition.model_dump())
            session.add(model)
            session.commit()
        ConfigManager.current().save(_config_key(definition.name), definition.model_dump())
        HookRegistry.create().reconcile_owned_hooks(
            owner_type="agent",
            owner_ref=definition.name,
            specs=definition.hooks,
        )
```

Update `DatabaseAgentManager.update` the same way:

```python
    def update(self, definition: AgentDefinition) -> None:
        with self.session() as session:
            model = session.get(AgentModel, definition.name)
            if not model:
                raise ValueError(f"Agent '{definition.name}' not found")
            data = definition.model_dump(exclude={"name"})
            for key, value in data.items():
                setattr(model, key, value)
            session.commit()
        ConfigManager.current().save(_config_key(definition.name), definition.model_dump())
        HookRegistry.create().reconcile_owned_hooks(
            owner_type="agent",
            owner_ref=definition.name,
            specs=definition.hooks,
        )
```

Update `DatabaseAgentManager.delete`:

```python
    def delete(self, name: str) -> None:
        with self.session() as session:
            model = session.get(AgentModel, name)
            if not model:
                raise ValueError(f"Agent '{name}' not found")
            session.delete(model)
            session.commit()
        ConfigManager.current().remove(_config_key(name))
        HookRegistry.create().delete_owned_hooks(owner_type="agent", owner_ref=name)
```

Update `_to_definition` to normalize a legacy NULL row, matching the existing `tools=model.tools or []` pattern:

```python
    @staticmethod
    def _to_definition(model: AgentModel) -> AgentDefinition:
        return AgentDefinition(
            name=model.name,
            model=model.model,
            system_prompt=model.system_prompt,
            description=model.description,
            tools=model.tools or [],
            tools_file=model.tools_file,
            workflow_file=model.workflow_file,
            mcp_servers=model.mcp_servers or [],
            skills_dir=model.skills_dir,
            agents=model.agents or [],
            planning=model.planning,
            max_plan_steps=model.max_plan_steps,
            approve_plan=model.approve_plan,
            max_tool_calls=model.max_tool_calls,
            max_concurrent_tools=model.max_concurrent_tools,
            max_tokens=model.max_tokens,
            stream=model.stream,
            approval_mode=model.approval_mode,
            autonomy=model.autonomy,
            approval_routing=model.approval_routing,
            reasoning_effort=model.reasoning_effort,
            long_term_memory=model.long_term_memory,
            hooks=model.hooks or [],
        )
```

- [ ] **Step 10: Wire escalation + impersonation checks into `admin_routes.py`**

In `flux/api/admin_routes.py`, update `admin_create_agent` (currently lines 301-336) to add the impersonation/runnable-target checks after the existing `requires_code_upload_permission()` gate:

```python
        @api.post("/admin/agents")
        async def admin_create_agent(
            agent_data: dict = Body(...),
            identity: FluxIdentity = Depends(require_permission("agent:*:create")),
        ):
            from flux.agents.manager import AgentManager
            from flux.agents.types import AgentDefinition

            try:
                definition = AgentDefinition(**agent_data)
                if definition.requires_code_upload_permission():
                    from flux.security.dependencies import _get_auth_service

                    upload_auth_service = _get_auth_service()
                    if upload_auth_service is not None:
                        has_perm = await upload_auth_service.is_authorized(
                            identity,
                            "workflow:*:*:register",
                        )
                        if not has_perm:
                            raise HTTPException(
                                status_code=403,
                                detail="tools_file/workflow_file/skills_dir bundles require workflow:*:*:register permission",
                            )
                # Declaring hooks is a further escalation beyond
                # agent:*:create: each fires its target under a stored
                # principal, the same rule workflow-declared hooks follow.
                for spec in definition.hooks:
                    await self._require_may_fire_as(
                        identity,
                        spec["principal"],
                        auth_config=auth_config,
                        auth_service=auth_service,
                        principal_registry=principal_registry,
                    )
                    await self._require_runnable_target(spec["principal"], spec["workflow"])
                manager = AgentManager.current()
                manager.create(definition)
                return {
                    "status": "success",
                    "message": f"Agent '{definition.name}' created successfully",
                }
            except HTTPException:
                raise
            except ValueError as ex:
                raise HTTPException(status_code=409, detail=str(ex))
            except Exception as ex:
                raise HTTPException(status_code=500, detail=str(ex))
```

Apply the identical addition (the `for spec in definition.hooks:` block, unchanged) to `admin_update_agent` (currently lines 338-375), inserted in the same relative place — right after its existing `requires_code_upload_permission()` gate and before `manager = AgentManager.current()`.

`admin_delete_agent` needs no change — `AgentManager.delete` (Step 9) already cleans up owned hooks.

- [ ] **Step 11: Run the tests to confirm they pass**

Run: `poetry run pytest tests/flux/test_agent_hooks_admin.py -v`
Expected: all PASS.

- [ ] **Step 12: Run the full regression suite for this task's touched files**

Run: `poetry run pytest tests/flux/test_migrations.py tests/flux/test_migrations_postgresql.py tests/flux/agents/ tests/security/test_agent_config_prefix.py tests/flux/test_agent_hooks_admin.py tests/flux/hooks/ -v`

(`tests/flux/test_migrations_postgresql.py` will skip without a PostgreSQL `FLUX_DATABASE_URL` — that's expected locally; it still runs in the `migrations-postgres` CI job.)

Expected: all PASS (or skipped, for the PostgreSQL file).

- [ ] **Step 13: Cold mypy check**

Run: `poetry run pre-commit run mypy --all-files`
Expected: no new errors.

- [ ] **Step 14: Commit**

```bash
git add flux/agents/types.py flux/models.py flux/migrations/versions/0028_agent_hooks.py \
        tests/flux/test_migrations.py tests/flux/test_migrations_postgresql.py \
        flux/agents/manager.py flux/api/admin_routes.py tests/flux/test_agent_hooks_admin.py
git commit -m "feat(hooks): add agent-declared hooks (declaration path 3)"
```

---

### Task 8: Agent-owned runtime scoping

Every agent session runs `agents/agent_chat`, so an agent-declared hook's selector text cannot discriminate between agents — the spec's "Scope confinement differs" requirement. This task adds the runtime backstop: the envelope's originating execution's `agent` is compared against the hook's `owner_ref` before a delivery is written, and the envelope itself surfaces the agent name.

**Files:**
- Modify: `flux/hooks/selectors.py` (`HookEvent`, `events_from_save`)
- Modify: `flux/hooks/registry.py` (`HookIndexEntry`, `_load_snapshot`, `matches`)
- Modify: `flux/hooks/envelope.py` (`build_envelope`)
- Modify: `flux/api/hook_routes.py` (`_synthetic_event`'s `HookIndexEntry(...)` construction in `test_hook`)
- Test: `tests/flux/hooks/test_agent_owned_matching.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `HookEvent.agent -> str | None` (new field, default `None`), `HookIndexEntry.owner_type -> str`, `HookIndexEntry.owner_ref -> str` (new required fields). Leaf task — nothing later consumes these.

- [ ] **Step 1: Write the failing tests**

Create `tests/flux/hooks/test_agent_owned_matching.py`:

```python
"""Agent-owned hooks carry a runtime owner filter (declaration path 3):
every agent session runs agents/agent_chat, so selector text alone cannot
confine a hook to one agent's own sessions."""

from __future__ import annotations

from flux.domain.execution_context import ExecutionContext
from flux.hooks.registry import HookIndexEntry, HookRegistry
from flux.hooks.selectors import HookEvent, events_from_save


def _ctx(namespace: str, name: str, input: object) -> ExecutionContext:
    return ExecutionContext(
        workflow_id=f"{namespace}/{name}",
        workflow_namespace=namespace,
        workflow_name=name,
        input=input,
        execution_id="exec-1",
    )


class TestEventsFromSaveCarriesTheAgentName:
    def test_an_agents_namespace_execution_carries_its_agent(self):
        from flux.domain.events import ExecutionEvent, ExecutionEventType

        ctx = _ctx("agents", "agent_chat", {"agent": "helper", "message": "hi"})
        event = ExecutionEvent(type=ExecutionEventType.WORKFLOW_STARTED, source_id="s", name="agent_chat")

        [derived] = events_from_save(ctx, [event])

        assert derived.agent == "helper"

    def test_a_non_agents_namespace_execution_carries_no_agent(self):
        from flux.domain.events import ExecutionEvent, ExecutionEventType

        ctx = _ctx("release", "pipeline", {"foo": "bar"})
        event = ExecutionEvent(type=ExecutionEventType.WORKFLOW_STARTED, source_id="s", name="pipeline")

        [derived] = events_from_save(ctx, [event])

        assert derived.agent is None

    def test_an_agents_namespace_execution_with_a_non_dict_input_carries_no_agent(self):
        from flux.domain.events import ExecutionEvent, ExecutionEventType

        ctx = _ctx("agents", "agent_chat", "not-a-dict")
        event = ExecutionEvent(type=ExecutionEventType.WORKFLOW_STARTED, source_id="s", name="agent_chat")

        [derived] = events_from_save(ctx, [event])

        assert derived.agent is None


def _event(key: str, *, agent: str | None = None) -> HookEvent:
    domain = key.split(":", 1)[0]
    return HookEvent(
        domain=domain,
        key=key,
        execution_id="exec-1",
        workflow_namespace="agents",
        workflow_name="agent_chat",
        event_id="ev-1",
        type=key.rsplit(":", 1)[-1],
        task_name=None,
        task_call_id=None,
        value=None,
        occurred_at="2024-01-01T00:00:00+00:00",
        agent=agent,
    )


class TestOwnerScopedMatching:
    def test_an_agent_owned_hook_only_matches_its_own_agents_events(self, isolated_db):
        registry = HookRegistry.create()
        registry.reconcile_owned_hooks(
            owner_type="agent",
            owner_ref="helper",
            specs=[
                {
                    "on": "execution:agents:agent_chat:completed",
                    "workflow": "ops/notify",
                    "principal": "p",
                    "name": None,
                    "max_attempts": 5,
                },
            ],
        )

        own_event = _event("execution:agents:agent_chat:completed", agent="helper")
        other_event = _event("execution:agents:agent_chat:completed", agent="other-agent")
        no_agent_event = _event("execution:agents:agent_chat:completed", agent=None)

        assert len(registry.matches(own_event)) == 1
        assert registry.matches(other_event) == []
        assert registry.matches(no_agent_event) == []

    def test_a_workflow_owned_hook_ignores_the_owner_filter(self, isolated_db):
        """Only owner_type='agent' rows carry the runtime filter -- a
        workflow-owned hook's static scope confinement is sufficient on its
        own, per the spec's 'Scope confinement differs' distinction."""
        registry = HookRegistry.create()
        registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[
                {
                    "on": "execution:release:pipeline:failed",
                    "workflow": "ops/notify",
                    "principal": "p",
                    "name": None,
                    "max_attempts": 5,
                },
            ],
        )

        event = HookEvent(
            domain="execution",
            key="execution:release:pipeline:failed",
            execution_id="exec-2",
            workflow_namespace="release",
            workflow_name="pipeline",
            event_id="ev-2",
            type="failed",
            task_name=None,
            task_call_id=None,
            value=None,
            occurred_at="2024-01-01T00:00:00+00:00",
            agent=None,
        )

        assert len(registry.matches(event)) == 1
```

Check `tests/flux/hooks/` (or `tests/flux/conftest.py`) for where the `isolated_db` fixture used by `test_registry.py` and `test_owned_reconciliation.py` is defined, and confirm it is available here too (it should be, as a shared fixture) — do not redefine it.

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `poetry run pytest tests/flux/hooks/test_agent_owned_matching.py -v`
Expected: FAIL — `HookEvent(...)` and `HookIndexEntry` don't accept `agent`/`owner_type`/`owner_ref` yet (`TypeError: unexpected keyword argument`), and `events_from_save` doesn't derive `agent`.

- [ ] **Step 3: Add `HookEvent.agent` and derive it in `events_from_save`**

In `flux/hooks/selectors.py`, add the field to the `HookEvent` dataclass (after `occurred_at`, as the last field so existing keyword-only call sites are unaffected):

```python
@dataclass(frozen=True)
class HookEvent:
    domain: str  # "execution" | "task"
    key: str  # the matchable string, e.g. "task:release:promote:promote_prod:awaiting_approval"
    execution_id: str
    workflow_namespace: str
    workflow_name: str
    event_id: str  # the persisted ExecutionEvent id
    type: str  # lower-cased state or event-type value
    task_name: str | None
    task_call_id: str | None
    value: Any
    occurred_at: str  # ISO-8601
    # Every agent session runs agents/agent_chat, so a selector's own text
    # cannot discriminate between agents -- an agent-owned hook's runtime
    # backstop (HookRegistry.matches) compares this against the hook's
    # owner_ref instead. None outside the "agents" namespace.
    agent: str | None = None
```

Update `events_from_save` to derive it once and pass it to both constructions:

```python
def events_from_save(
    ctx: ExecutionContext,
    new_events: Sequence[ExecutionEvent],
) -> list[HookEvent]:
    """Derive the ``HookEvent``s a save is about to persist.

    One per event: `WORKFLOW_*` rows produce the `execution` domain keyed on
    the state that event announces; `TASK_*` rows produce the `task` domain
    keyed on the event type with its `TASK_` prefix stripped. Any other
    event type -- and any workflow event with no state of its own -- is
    skipped rather than raising: it should not occur, but a hook derivation
    is not the place to fail a save over it.
    """
    # The same derivation flux/api/workflow_routes.py uses to record
    # AgentSessionModel at execution creation -- ctx.input is set once at
    # ExecutionContext construction and never overwritten by a resume, so
    # this reads the same "agent" key throughout the execution's life.
    agent = (
        ctx.input.get("agent")
        if ctx.workflow_namespace == "agents" and isinstance(ctx.input, dict)
        else None
    )

    produced: list[HookEvent] = []
    for event in new_events:
        event_type = event.type
        type_name = (
            event_type.value if isinstance(event_type, ExecutionEventType) else str(event_type)
        )

        if type_name.startswith("WORKFLOW_"):
            state = _WORKFLOW_EVENT_STATES.get(type_name)
            if state is None:
                continue
            produced.append(
                HookEvent(
                    domain="execution",
                    key=f"execution:{ctx.workflow_namespace}:{ctx.workflow_name}:{state}",
                    execution_id=ctx.execution_id,
                    workflow_namespace=ctx.workflow_namespace,
                    workflow_name=ctx.workflow_name,
                    event_id=event.id,
                    type=state,
                    task_name=None,
                    task_call_id=None,
                    value=event.value,
                    occurred_at=event.time.isoformat(),
                    agent=agent,
                ),
            )
        elif type_name.startswith("TASK_"):
            task_type = type_name.removeprefix("TASK_").lower()
            produced.append(
                HookEvent(
                    domain="task",
                    key=f"task:{ctx.workflow_namespace}:{ctx.workflow_name}:{event.name}:{task_type}",
                    execution_id=ctx.execution_id,
                    workflow_namespace=ctx.workflow_namespace,
                    workflow_name=ctx.workflow_name,
                    event_id=event.id,
                    type=task_type,
                    task_name=event.name,
                    task_call_id=event.source_id,
                    value=event.value,
                    occurred_at=event.time.isoformat(),
                    agent=agent,
                ),
            )
        # else: not a hook-domain event type -- skip rather than raise.

    return produced
```

- [ ] **Step 4: Add owner fields to `HookIndexEntry` and the owner filter to `matches`**

In `flux/hooks/registry.py`, update `HookIndexEntry`:

```python
@dataclass(frozen=True)
class HookIndexEntry:
    id: str
    name: str
    selectors: tuple[str, ...]
    workflow_ref: str
    principal: str
    max_attempts: int
    owner_type: str
    owner_ref: str
```

Update `_load_snapshot` to populate them:

```python
    def _load_snapshot(self) -> tuple[HookIndexEntry, ...]:
        with self._repository.session() as session:
            rows = session.query(HookModel).filter_by(enabled=True).all()
            return tuple(
                HookIndexEntry(
                    id=row.id,
                    name=row.name,
                    selectors=tuple(row.selectors or []),
                    workflow_ref=row.workflow_ref,
                    principal=row.principal,
                    max_attempts=row.max_attempts,
                    owner_type=row.owner_type,
                    owner_ref=row.owner_ref,
                )
                for row in rows
            )
```

Update `matches` to apply the owner filter:

```python
    def matches(self, event: HookEvent) -> list[HookIndexEntry]:
        # `any(...)` collapses a hook's several selectors into a single
        # match -- an OR across selectors, not a fan-out of deliveries.
        return [
            entry
            for entry in self.snapshot()
            if any(selector_matches(selector, event.key) for selector in entry.selectors)
            and self._owner_permits(entry, event)
        ]

    @staticmethod
    def _owner_permits(entry: HookIndexEntry, event: HookEvent) -> bool:
        """Agent-owned hooks carry a runtime backstop: their selector text
        cannot discriminate between agents (every session runs
        agents/agent_chat), so ownership is enforced here instead. Every
        other owner type relies on its selector text alone -- workflow-
        declared hooks are scope-confined at registration, user-declared
        hooks are meant to range freely."""
        if entry.owner_type != "agent":
            return True
        return event.agent == entry.owner_ref
```

- [ ] **Step 5: Surface `agent` in the envelope**

In `flux/hooks/envelope.py`, add the field to the `event` sub-dict in `build_envelope`:

```python
    envelope = {
        "hook": hook.name,
        "selector": selector,
        "delivery_id": delivery_id,
        "event_key": event.delivery_key,
        "attempt": attempt,
        "hop": hop,
        "event": {
            "domain": event.domain,
            "type": event.type,
            "execution_id": event.execution_id,
            "workflow_namespace": event.workflow_namespace,
            "workflow_name": event.workflow_name,
            "task_name": event.task_name,
            "task_call_id": event.task_call_id,
            "state": event.type if event.domain == "execution" else None,
            "value": event.value,
            "occurred_at": event.occurred_at,
            "agent": event.agent,
        },
    }
```

- [ ] **Step 6: Fix `test_hook`'s `HookIndexEntry(...)` construction**

In `flux/api/hook_routes.py`, `test_hook` builds a `HookIndexEntry` directly (not via `registry.snapshot()`), which now needs the two new required fields. Update:

```python
            envelope = build_envelope(
                HookIndexEntry(
                    id=hook.id,
                    name=hook.name,
                    selectors=tuple(hook.selectors or []),
                    workflow_ref=hook.workflow_ref,
                    principal=hook.principal,
                    max_attempts=hook.max_attempts,
                    owner_type=hook.owner_type,
                    owner_ref=hook.owner_ref,
                ),
                selector,
                event,
                delivery_id=f"hook-test-{uuid4().hex}",
                attempt=1,
                hop=0,
            )
```

- [ ] **Step 7: Run the tests to confirm they pass**

Run: `poetry run pytest tests/flux/hooks/test_agent_owned_matching.py -v`
Expected: all PASS.

- [ ] **Step 8: Run the full hooks + hook-route regression suite**

Run: `poetry run pytest tests/flux/hooks/ tests/flux/test_hook_routes.py tests/flux/test_hook_models.py tests/security/test_hook_authz.py tests/flux/test_workflow_hooks_registration.py tests/flux/test_agent_hooks_admin.py -v`
Expected: all PASS — this is the widest-blast-radius task (touches the frozen `HookEvent`/`HookIndexEntry` dataclasses every earlier hooks test constructs), so a full sweep here is the real gate.

- [ ] **Step 9: Cold mypy check**

Run: `poetry run pre-commit run mypy --all-files`
Expected: no new errors.

- [ ] **Step 10: Commit**

```bash
git add flux/hooks/selectors.py flux/hooks/registry.py flux/hooks/envelope.py \
        flux/api/hook_routes.py tests/flux/hooks/test_agent_owned_matching.py
git commit -m "feat(hooks): scope agent-declared hooks to their own sessions at runtime"
```

---

### Task 9: E2E coverage, docs, and version bump

E2E covers declaration path 2 end-to-end (registration → real delivery → re-registration replaces → delete-with-workflow removes) through a real server + worker, the same style as slice 1's `tests/e2e/test_hooks.py`. Declaration path 3 (agent-declared) already has thorough `TestClient`-level integration coverage from Task 7 exercising the same registration/replace/delete lifecycle against the real admin routes and database — a deliberate scope choice, not a gap, since e2e tests are expensive and the spec's testing requirements ("registration without hook-create permission rejected", "owner lifecycle: replace on re-register, cascade on delete") are already fully exercised at that level for both paths.

**Files:**
- Create: `tests/e2e/fixtures/declared_hook_workflows.py`
- Create: `tests/e2e/test_hooks_declared.py`
- Modify: `CLAUDE.md` (hooks section — currently absent; add one alongside the other decorator-option entries)
- Modify: `pyproject.toml` (version bump)

**Interfaces:**
- Consumes: everything from Tasks 1-8. This is the final task — nothing follows it.

- [ ] **Step 1: Write the e2e fixture workflows**

Create `tests/e2e/fixtures/declared_hook_workflows.py`:

```python
"""Fixture workflows for the declared-hooks E2E (declaration path 2).

``declared_hook_source`` mirrors ``declared_hook_notifier`` — its only
task returns what it was handed, so the delivered envelope is readable
straight off the execution's output. ``declared_hook_source`` fails
immediately, which is the event its own ``hooks=`` declaration subscribes
to.
"""

from __future__ import annotations

from typing import Any

from flux import task, workflow
from flux.hooks import hook


@task
async def record(payload: Any) -> Any:
    return payload


@workflow
async def declared_hook_notifier(ctx):
    return await record(ctx.input)


@workflow.with_options(
    namespace="default",
    hooks=[
        hook.run(
            on="execution:default:declared_hook_source:failed",
            workflow="default/declared_hook_notifier",
            principal="e2e-hooks",
        ),
    ],
)
async def declared_hook_source(ctx):
    raise RuntimeError("deliberate failure for the declared-hook e2e")
```

- [ ] **Step 2: Write the e2e test**

Create `tests/e2e/test_hooks_declared.py`:

```python
"""E2E for declaration path 2 (workflow-declared hooks).

Spawns the real server + worker (the session-scoped ``cli`` fixture) and
drives the whole lifecycle: registering a workflow with ``hooks=[...]``
creates the row, an event it declares fires a real delivery, re-
registering with a different selector replaces the same row, and deleting
the workflow removes it.
"""

from __future__ import annotations

import time
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
_DELIVERY_TIMEOUT = 120


def _wait_for_delivery(cli, hook_name: str, status: str, timeout: int) -> dict:
    deadline = time.monotonic() + timeout
    rows: list = []
    while time.monotonic() < deadline:
        rows = cli.hook_deliveries(hook_name)
        matching = [row for row in rows if row["status"] == status]
        if matching:
            return matching[0]
        time.sleep(2)
    raise TimeoutError(
        f"No '{status}' delivery for hook {hook_name} within {timeout}s; saw "
        f"{[(row['status'], row['attempts'], row['last_error']) for row in rows]}",
    )


def _owned_hook_name(cli) -> str:
    hooks = cli.hook_list()["hooks"]
    owned = [h for h in hooks if h["owner_type"] == "workflow" and h["owner_ref"] == "default/declared_hook_source"]
    assert len(owned) == 1, hooks
    return owned[0]["name"]


def test_registering_a_declared_hook_fires_a_real_delivery(cli):
    cli.register(str(FIXTURES / "declared_hook_workflows.py"))
    hook_name = _owned_hook_name(cli)

    r = cli.run("declared_hook_source", "null", mode="async")
    cli.wait_for_state("declared_hook_source", r["execution_id"], "FAILED", timeout=60)

    delivery = _wait_for_delivery(cli, hook_name, "delivered", timeout=_DELIVERY_TIMEOUT)
    assert delivery["execution_id"]
    started = cli.wait_for_state(
        "declared_hook_notifier",
        delivery["execution_id"],
        "COMPLETED",
        timeout=60,
    )
    assert started["output"]["event"]["type"] == "failed"
    assert started["output"]["event"]["workflow_name"] == "declared_hook_source"


def test_reregistering_replaces_and_deleting_the_workflow_removes_it(cli):
    cli.register(str(FIXTURES / "declared_hook_workflows.py"))
    hook_name = _owned_hook_name(cli)
    first = cli.hook_get(hook_name)

    # Re-register the identical source: same declared spec, same derived
    # name, same row -- not a duplicate.
    cli.register(str(FIXTURES / "declared_hook_workflows.py"))
    second = cli.hook_get(hook_name)
    assert second["id"] == first["id"]

    cli._server_ok(
        ["workflow", "delete", "default/declared_hook_source", "--force"],
    )
    remaining = [
        h
        for h in cli.hook_list()["hooks"]
        if h["owner_type"] == "workflow" and h["owner_ref"] == "default/declared_hook_source"
    ]
    assert remaining == []
```

`flux workflow delete` (`flux/cli.py:248-282`) takes a single `workflow_name` argument resolved via `resolve_workflow_ref` (so `"default/declared_hook_source"`, not two separate positional args) plus `--force`/`-f` to skip the confirmation prompt (there is no `--yes` flag on this command — do not confuse it with `flux hook delete`, which does take `--yes`).

- [ ] **Step 3: Run the e2e tests**

Run: `poetry run pytest tests/e2e/test_hooks_declared.py -v -m "not ollama and not network"`
Expected: all PASS. If the delete CLI invocation from Step 2 doesn't match the real command shape, fix it now (this is the one step in this plan where the exact CLI surface must be checked live rather than assumed).

- [ ] **Step 4: Run the complete unit + e2e suite for the whole slice**

Run: `poetry run pytest tests/ --ignore=tests/e2e --cov=flux`
Run: `poetry run pytest tests/e2e/ -m "not ollama and not network" -v`
Expected: all PASS — this is the full CI-equivalent sweep across everything Tasks 1-9 touched, plus every pre-existing test in the repository.

- [ ] **Step 5: Update `CLAUDE.md`**

In `CLAUDE.md`, under "### Decorators and the programming model", extend the `workflow.with_options(...)` bullet to mention `hooks`, and add one sentence to the outbound-hooks context wherever it's most natural given the file's current state (check whether slice 1 already added an outbound-hooks bullet under "Other subsystems" or elsewhere — extend that entry rather than creating a new section, since `docs/specs/2026-08-14-outbound-hooks-spec.md` is the source of truth for the full design and `CLAUDE.md` only needs a pointer plus the two facts an agent working in this repo needs before touching the code: declaration paths 2/3 exist, and reuse `Server._require_may_fire_as`/`_require_runnable_target` (in `flux/api/hook_routes.py`) rather than reimplementing the impersonation check).

- [ ] **Step 6: Bump the version**

In `pyproject.toml`, change:
```toml
version = "0.85.0"
```
to:
```toml
version = "0.86.0"
```
(Minor bump — this is a feature, per the repository's stated convention.)

- [ ] **Step 7: Full pre-commit sweep**

Run: `poetry run pre-commit run --all-files --show-diff-on-failure`
Expected: clean (fix anything it flags and re-run — do not use `--no-verify`).

- [ ] **Step 8: Cold mypy check**

Run: `poetry run pre-commit run mypy --all-files`
Expected: no errors.

- [ ] **Step 9: Commit**

```bash
git add tests/e2e/fixtures/declared_hook_workflows.py tests/e2e/test_hooks_declared.py CLAUDE.md pyproject.toml
git commit -m "test(hooks): add e2e coverage for workflow-declared hooks; bump version"
```

---

## Self-Review Notes

- **Spec coverage:** "Declaration paths" §2 (workflow-declared: scope confinement, permission escalation, replace-on-reregister, delete-with-workflow) → Tasks 2-6. §3 (agent-declared: same escalation rule, same replace lifecycle, runtime scope filter) → Tasks 7-8. The "Testing" section's four declaration-path bullets (scope confinement rejected loudly / registration without hook-create permission rejected / principal lacking execute rejected at create / owner lifecycle replace-and-cascade) are each covered: scope confinement in Task 4's `SyntaxError` tests, hook-create-permission and principal-execute rejection in Tasks 6/7's registration tests (via HTTP status codes; the underlying permission-string checks are the same predicates already proven in `tests/security/test_hook_authz.py` for path 1, so this plan does not re-derive that proof per path — it proves the *wiring* calls those predicates), and owner lifecycle in Task 5's reconciliation tests plus Tasks 6/7's replace/delete integration tests. "Out of scope" items (inbound hooks, webhook action, delivery ordering, agent-session messaging action, console panels) are correctly untouched by every task above.
- **Placeholder scan:** every step carries complete code, not descriptions; every test has real assertions; no "add appropriate error handling" language appears.
- **Type consistency:** `hook.run(...)`'s return shape (`on`/`workflow`/`principal`/`name`/`max_attempts`) is identical across Task 2 (definition), Task 4 (AST re-derivation via the same factory), Task 5 (`reconcile_owned_hooks`'s `spec["on"]`/`spec["workflow"]`/`spec["principal"]` reads), Task 6 (`spec["principal"]`/`spec["workflow"]` reads in `workflows_save`), and Task 7 (`AgentDefinition.hooks` validator normalizing through the same factory). `_require_may_fire_as`/`_require_runnable_target`'s promoted signatures (Task 1) are called identically in Task 6 and Task 7. `HookIndexEntry`'s two new fields (Task 8) are populated at both of its construction sites (`registry.py::_load_snapshot` and `hook_routes.py::test_hook`).
- **Follow-up (not part of this plan, controller responsibility after all tasks land):** file a GitHub issue for the schedule auto-lifecycle's missing delete-with-workflow and test coverage — this slice deliberately does not fix or copy that gap, per the confirmed decision to build hooks correctly and track the schedule gap separately.
