# Design: Server-side, admin-writable worker metadata consumable in affinity/score()

- **Issue:** #138
- **Date:** 2026-07-19
- **Status:** Approved design → ready for implementation plan

## Summary

Add a fourth per-worker attribute channel — **admin/control-plane-written metadata** — that is authoritative (a worker cannot set it), hot-updatable (no re-registration or reconnect), and mixed-type (string **or** number). Expose it in dispatch expressions through a dedicated **`meta("key")`** selector usable in both `affinity=require(...)` hard filters (including ordered numeric thresholds) and `score()` ranking. It is kept structurally distinct from the worker's self-advertised `labels` and `metrics`.

### Why a new channel (not labels)

Today a worker's routing-relevant attributes come from three sources, all resolved in `routing.py::_selector_value`:

| Source | Written by | Type | Selector |
|---|---|---|---|
| `worker.labels` | worker (self-advertised at register) | string | `label(key)` |
| `worker.metrics` | worker (self-advertised on pong) | number | `metric(key)` |
| `worker.resources` | worker (self-advertised) | number | `resource(field)` |
| **`worker.metadata` (new)** | **control plane / operator (admin API)** | **string or number** | **`meta(key)`** |

Self-advertised labels are the right model for *capabilities a worker declares about itself*. They are the wrong model for *facts the control plane asserts about a worker*: (1) **authority** — a compromised/misconfigured worker could spoof a label; (2) **update cadence** — the value is recomputed periodically and must not require re-register; (3) **type** — labels are string predicates, but some inputs are numeric and want to participate in `score()` ranking.

A separate channel with its own selector dissolves the issue's "precedence vs labels" open question: `meta:` and `label:` are distinct selectors over distinct stores, so they cannot collide or be spoofed across the boundary.

**Alternatives rejected:** (a) reserved-prefix admin labels — reuses `label()` but forces numbers to be stringly-typed, the exact limitation motivating the feature; (b) hybrid split (strings→`label()`, numbers→`metric()`) — overloads two worker-advertised channels with admin-authored values and muddies the "who wrote this" precedence.

## Data model & storage

- **New column** `WorkerModel.worker_metadata = Column(Base64Type(), nullable=True)` on the `workers` table — a sibling of the existing `labels` and `metrics` blob columns (`models.py`). Named `worker_metadata` because SQLAlchemy reserves the `metadata` attribute on declarative bases (same reason `WorkflowModel.wf_metadata` is named that way).
- **Value shape:** `dict[str, str | float]`, JSON-encoded via `Base64Type` exactly like `metrics`.
- **`WorkerInfo.metadata: dict[str, str | float] | None`** — new field (default `None`), populated by `DatabaseWorkerRegistry._to_info`.
- **Alembic migration:** a new version under `flux/migrations/versions/` adds the nullable column. Existing rows are unaffected (NULL = no metadata).

### Lifetime & GC

- **Survives reconnect:** `DatabaseWorkerRegistry.register()` must **not** reset `worker_metadata`. This mirrors how `metrics` already survives reconnect — contrast `labels`, which `register()` overwrites (`worker_registry.py:195`). Only `session_token`, `labels`, `max_concurrent_executions`, and `runners` are refreshed on re-register.
- **GC:** metadata rides the worker row. Worker rows are never hard-deleted today (the reaper only *evicts* — revokes the API key, recovers executions — it does not delete rows, and there is no worker-delete endpoint). Therefore metadata persists until an explicit admin `DELETE`, which is the desired operator-controlled behavior. If a worker-row delete is ever introduced, same-row storage removes the metadata automatically.

## Admin API & registry

New authenticated admin routes, addressed by worker **name** (the id used throughout), following the existing `admin_routes.py` pattern (`Depends(require_permission(...))`):

| Method | Route | Body | Permission |
|---|---|---|---|
| `PUT` | `/admin/workers/{name}/metadata/{key}` | `{"value": <str \| number>}` | `admin:workers:manage` |
| `DELETE` | `/admin/workers/{name}/metadata/{key}` | — | `admin:workers:manage` |
| `GET` | `/admin/workers/{name}/metadata` | — (returns full map) | `admin:workers:read` |
| `PUT` | `/admin/workers/{name}/metadata` | `{map}` — bulk replace (for a control plane recomputing the whole set) | `admin:workers:manage` |

- Unknown worker → 404 (`WorkerNotFoundError`).
- **Workers can never write these.** The routes are admin-permissioned and entirely separate from `POST /workers/register` and the pong path; the built-in `worker` role has no `admin:workers:*` permission. This is the authority guarantee, enforced structurally rather than by convention.
- **Registry methods** (on `WorkerRegistry` / `DatabaseWorkerRegistry`):
  - `set_metadata(name, updates: dict, *, replace: bool = False)` — read-merge-write within one transaction (targeted like `record_metrics`); `replace=True` overwrites the whole map, else merges keys. Raises `WorkerNotFoundError`.
  - `delete_metadata(name, key)` — remove one key.
- **CLI (optional, follow-on):** a `flux workers metadata set|get|list|remove` group mirroring `flux config`/`flux secrets`.

## Validation & namespacing

`validate_worker_metadata(payload)` in `routing.py`, mirroring `validate_worker_metrics` (`routing.py:536`):

- **Values** ∈ `{str, int, float}` — reject `bool`, `None`, and nested structures; floats must be finite; bound string length.
- **Keys** — non-empty `str`, length ≤ `MAX_METRIC_KEY_LENGTH` (64), matching the label-key regex (`_LABEL_KEY_RE`).
- **Reserved prefix** — reject keys under `flux.` (`RESERVED_METRIC_PREFIX`), reserving room for future built-in control-plane metadata and matching the codebase convention for labels/metrics.
- **Count cap** — at most `MAX_WORKER_METADATA` (≈32) keys per worker.

Invalid admin writes are a client error (4xx) — unlike worker metrics (a hint channel that drops silently), admin metadata is an explicit operator action and should fail loudly.

## Routing integration (`routing.py`)

### Selector

- Add `meta(key) -> Selector("meta", key)`, compiling to the spec string `"meta:key"`. Add `"meta"` to the recognized selector kinds.

### Score stage (`prefer` / `least` / `most`)

- `_selector_value(worker, selector, loads)` gains: `if kind == "meta": return (worker.metadata or {}).get(key)`.
- `meta()` is valid in `prefer()` (boolean condition), `least()`, and `most()` (numeric, normalized 0..1 via `_as_float`). No `pick_worker` signature change — it already receives full `WorkerInfo` objects.

### Filter stage (`require`) — with ordered numeric comparisons

- Relax `_compile_match` to accept a `meta()` selector (new `is_meta` branch alongside `is_label`).
- **Ops:** meta terms allow `==`, `!=`, **and ordered comparisons** (`<`, `<=`, `>`, `>=`). Labels remain `==`/`!=` only (ordered comparisons on stringly-typed labels stay disallowed). The value side may be a constant or `input(...)`, as with label terms.
- Thread worker metadata through the evaluator:
  - `require_matches(terms, worker_labels, input_value, worker_metadata=None)` — resolve `meta:` selectors against `worker_metadata`, `label:` against `worker_labels`. Reuse `_compare` for numeric ordering on meta terms.
  - `require_diagnostic(...)` — an unresolved-input meta term still fails closed (diagnosing a permanently-undispatchable execution) unless wrapped in `optional(...)`. A `when()` term whose condition is `meta()`/`metric()` is **per-worker / fleet-dependent** and therefore not evaluated here (only `input()` conditions are execution-level).

### Evaluation restructure (`when()` becomes partly per-worker)

Today `when()` conditions are resolved once (input only) before the per-worker loop. To support `meta()`/`metric()` conditions, condition evaluation splits by source: an `input()` condition is resolved once (as now); a `meta()`/`metric()` condition is resolved **inside** the per-worker path — in `require_matches` (already per-worker) and in `pick_worker`'s score loop (a contained refactor: an inactive worker-state condition contributes 0 for that worker instead of the whole term being skipped). Both `meta()` and `metric()` conditions share one resolution path via `_selector_value`.
  - `worker_matches(...)` in `resource_request.py` passes `worker.metadata` through to `require_matches`.

### Conditional gating: `when()` and `optional()`

`meta()` composes with `optional()` like any match term. More importantly, **`when()` conditions are extended to gate on dynamic worker state**, not only requester intent — because worker metadata (and worker metrics) are exactly the kind of externally-changing worker state routing wants to branch on.

`when(condition, term)` applies `term` only when `condition` holds. The condition may reference:

- **`input(...)`** — requester intent, resolved **once per execution** (worker-independent). Participates in `require_diagnostic`.
- **`meta(...)`** — control-plane-authoritative worker state, resolved **per worker** at match/score time. Fleet-dependent, so excluded from `require_diagnostic`.
- **`metric(...)`** — worker-reported dynamic state, resolved **per worker**. Fleet-dependent, excluded from `require_diagnostic`.

Conditions compare one source against a **constant** — no cross-source conditions like `meta("x") == input("y")`. Examples:

- `require(when(input("tier") == "dedicated", meta("dedicated_cap") >= 1))` — request-gated meta hard-floor.
- `score(when(meta("class") == "gpu", most(meta("gpu_priority"))))` — rank by GPU priority only among GPU-class workers.
- `score(when(metric("queue_depth") < 100, prefer(meta("region") == input("region"))))` — apply the region preference only to workers that aren't backlogged.
- `require(optional(meta("region_cap") >= input("min_cap")))` — skip the meta filter when the request omits `min_cap`; with a constant RHS `optional` is a harmless no-op.

**The line is dynamic state vs. static capability.** `when()` conditions accept `input()`/`meta()`/`metric()` (requester intent + dynamic worker state). **`label()` is excluded** — it is the *static capability-declaration* channel set once at registration, not state, and gating a hard `require` constraint on a worker-set label would let a worker flip the gate to dodge the constraint. (`resource()` is per-worker dynamic state like `metric()` and could join the same bucket later; out of scope now.)

**Authority caveat (documented, not enforced).** `meta()` and `metric()` differ only in authority: `meta()` is control-plane-set and unspoofable; `metric()` is worker-reported. For a *hard `require` gate* a compromised worker must not be able to flip, use `meta()`. `metric()` gates are appropriate for soft/operational conditions (backlog, temperature, free capacity). The DSL permits both and leaves the choice to the operator — consistent with `metric()` already being trusted as a scoring input today.

`meta()` has literal keys only — there is no dynamic `meta_for(...)` (the `label_for`/`service` dynamic-key machinery does not extend to metadata).

## Dispatch hot-update (multi-replica correct, no duplicate query)

**Problem.** The dispatcher matches against in-memory `WorkerInfo` (`server.py::_worker_info`), which exists only on the replica a worker is connected to. Metrics stay fresh because the worker pongs *its own* replica, which mutates the in-memory copy (`server.py:712`). Admin metadata has no such push — an operator's write lands on an arbitrary replica — so a naive in-memory approach would not reach the dispatching replica in a multi-replica deployment.

**Solution.** Read metadata from the authoritative DB **inside the existing per-batch load query**, not as a separate statement. `next_executions_batch` (and `next_resumes_batch`) already call `_worker_load_map(session, names)`, an aggregate over the `executions` table. Fold the `workers.worker_metadata` read into that same round-trip:

```sql
SELECT w.name, w.worker_metadata, COUNT(e.execution_id)
FROM workers w
LEFT JOIN executions e
       ON e.worker_name = w.name AND e.state IN (RUNNING, CLAIMED, SCHEDULED)
WHERE w.name IN (:names)
GROUP BY w.name          -- worker_metadata is functionally dependent on the PK
```

Add a combined helper (e.g. `_worker_load_and_metadata_map(session, names) -> dict[str, tuple[int, dict]]`) used by both batch paths; hydrate `WorkerInfo.metadata` on the candidate workers from its result before matching/scoring. The poll path (`next_execution(worker)`) hydrates the single worker's metadata in the session it already opens.

**Properties:** updates take effect on the next dispatch, with no reconnect, replica-agnostic and exact — and **no duplicate operation or added round-trip** versus the load query that already runs. The candidate set is the locally-connected workers (small), and grouping by the PK keeps the join cheap on both SQLite and PostgreSQL.

## Error handling

- Admin write with an invalid key/value/count → 4xx with a clear message (loud, not silent).
- Admin write to an unknown worker → 404.
- A `require(meta(...))` term whose input is unresolved on a non-optional term → the execution fails as undispatchable via `require_diagnostic` (fail-closed, never park-forever), consistent with existing label affinity.
- A malformed routing policy referencing `meta:` → `pick_worker` returns `None` and dispatch degrades to least-loaded, exactly as for other malformed terms.
- Metadata never blocks dispatch of a workflow that does not reference it: absent metadata simply yields `None` from `_selector_value` (scores 0 for that term) and only fails a `require(meta(...))` the workflow explicitly declared.

## Testing

- **routing unit** — `meta()` compiles to `"meta:key"`; `_selector_value` resolves from `worker.metadata`; `pick_worker` ranks by numeric meta (`least`/`most`/`prefer`); `require_matches` with `==`/`!=`/ordered meta; `when()` gating with per-worker `meta()`/`metric()` conditions (active/inactive per worker) and once-per-execution `input()` conditions; `label()` rejected as a `when()` condition; `optional()` composition; fail-closed on absent meta; malformed spec degrades.
- **validation unit** — `validate_worker_metadata` accept/reject matrix (types, key regex, reserved `flux.`, count cap).
- **registry unit** — set/merge/replace/delete/read; metadata survives a simulated re-register; `WorkerNotFoundError` on unknown worker.
- **API/security** — admin routes permission-gated (a worker principal → 403; admin → 200); read-back; a write on one path reflected on the next dispatch.
- **dispatch unit** — the combined load+metadata query returns correct loads and metadata; a worker with no metadata yields `{}`/`None`.
- **e2e** — admin sets `meta("priority")`; a workflow with `routing=score(most(meta("priority")))` dispatches to the higher-priority worker; `affinity=require(meta("health_score") >= 0.8)` excludes a low-health worker; an admin update takes effect on the next dispatch without the worker reconnecting.
- **docs** — update the `routing.py` module docstring (selectors list) and the CLAUDE.md routing / worker-metrics sections to describe `meta()` and the admin metadata channel.

## Files touched (anticipated)

- `flux/models.py` — `WorkerModel.worker_metadata` column.
- `flux/migrations/versions/` — new Alembic migration.
- `flux/worker_registry.py` — `WorkerInfo.metadata`; `_to_info`; `set_metadata`/`delete_metadata`; ensure `register()` does not reset metadata.
- `flux/routing.py` — `meta()` selector; `_selector_value` meta branch; `_compile_match`/`require_matches`/`_resolve_require_term` meta support + ordered ops; `when()` accepting `input()`/`meta()`/`metric()` conditions with per-worker evaluation of the worker-state ones in `pick_worker`/`require_matches`; `validate_worker_metadata`; `MAX_WORKER_METADATA`.
- `flux/domain/resource_request.py` — `worker_matches` threads `worker.metadata`.
- `flux/context_managers.py` — combined load+metadata helper; hydrate `WorkerInfo.metadata` in `next_executions_batch` / `next_resumes_batch` / `next_execution`.
- `flux/api/admin_routes.py` (+ `flux/api/schemas.py`) — the four admin routes.
- `flux/cli.py` — optional `flux workers metadata` group (follow-on).
- Tests across `tests/flux/`, `tests/security/`, `tests/e2e/`.
- `CLAUDE.md` + `routing.py` docstring.

## Out of scope

- Dynamic metadata keys completed from input (`meta_for`) — literal keys only.
- `resource()` as a `when()` condition — it belongs to the same per-worker dynamic-state bucket as `metric()` and is a trivial later addition, but not in this spec.
- Per-key history/audit trail — a single column, not a child table (revisit if audit is later required).
- The `flux workers metadata` CLI is a natural follow-on, not required for the core feature.
