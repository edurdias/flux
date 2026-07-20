# Spec: server-side worker metadata — control-plane facts in affinity/score()

**Date:** 2026-07-20 · **Status:** draft for review (issue #138)
· **Motivating use case:** routing inputs the control plane asserts
*about* a worker — authoritative, numeric, hot-updatable — alongside the
labels a worker advertises about itself.

## Motivation

A worker's routing-relevant attributes today are **self-advertised**:
labels and metrics applied at registration / heartbeat. That is the
right channel for capability the worker declares about itself, and the
wrong channel for three kinds of input:

1. **Authority.** A compromised or misconfigured worker can advertise
   any label or metric. Some routing inputs (policy flags, quality
   scores, drain hints) must be writable only by the control plane.
2. **Update cadence.** Labels change only at re-registration. Values
   recomputed periodically by an operator or controller should take
   effect on the next dispatch without bouncing the worker.
3. **Type.** Labels are string predicates. Centrally-computed values
   are often numeric and want to participate in `score()` ranking, not
   just equality filtering.

## Design

### A third worker attribute channel

Workers gain **metadata**: a server-held `dict[str, str | float]`
written exclusively through an authenticated admin API. It sits beside
the two self-advertised channels and is consumed by dispatch through a
dedicated selector, so the channels can never collide or spoof each
other:

| channel  | written by            | selector      | typical use            |
|----------|-----------------------|---------------|------------------------|
| labels   | worker (registration) | `label(key)`  | capability, topology   |
| metrics  | worker (heartbeat)    | `metric(key)` | load, health signals   |
| metadata | control plane (admin) | `meta(key)`   | policy, weights, drain |

No reserved prefix is needed: `meta("x")` reads only the metadata dict,
`label("x")` reads only labels. A worker that advertises a label named
like a metadata key gains nothing — the selector namespaces are
disjoint by construction. Workers have **no write path** to metadata:
registration does not accept it and never touches the column, so it
survives worker reconnect and re-registration unchanged.

### Storage

New nullable `workers.worker_metadata` column (`Base64Type`, matching
`labels`/`metrics`), Alembic revision `0013_worker_metadata`, additive.
Lifetime is the worker row's lifetime: there is no worker-delete API
today, and if one is added the column goes with the row — no separate
GC is required.

### Values

- Keys: non-empty strings, ≤ 64 chars, same shape as label keys
  (`[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?`). At most 64 keys per
  worker.
- Values: strings (≤ 256 chars) or finite numbers. Numbers are stored
  as `float`; booleans are accepted at the API edge and stored as
  `"true"`/`"false"` strings, matching label conventions.

Validation lives beside `validate_worker_metrics` in `flux/routing.py`
(`validate_worker_metadata`) so the API edge and any future writers
share one rule. Invalid payloads are a 400 — unlike metrics, metadata
is an operator command channel, not a hint channel, so errors must
surface, not drop.

## Admin API

All routes require `admin:workers:manage` (the existing worker-admin
permission, already used for join tokens). Reads too: metadata is an
operator control channel, and `GET /workers` already surfaces the
values for general visibility.

```
GET    /admin/workers/{name}/metadata            → {"metadata": {...}}
PUT    /admin/workers/{name}/metadata            body {"metadata": {...}, "replace": false}
DELETE /admin/workers/{name}/metadata/{key}      → remaining metadata
DELETE /admin/workers/{name}/metadata            → {} (clear)
```

- `PUT` merges by default (`replace=true` swaps the whole dict) and
  returns the resulting metadata, so controllers can read-modify-write
  without a second round trip.
- Unknown worker → 404. Invalid keys/values/counts → 400 with the
  offending key in the message. Deleting an absent key is idempotent.
- `GET /workers` and `GET /workers/{name}` gain a `metadata` field on
  `WorkerResponse` — the read-back API for tooling, no new permission.

## Dispatch consumption

New selector in the routing DSL (`flux/routing.py`):

```python
from flux.routing import require, score, meta, least, most, prefer

@workflow.with_options(
    affinity=require(meta("maintenance") != "true"),      # hard filter
    routing=score(
        most(meta("weight"), weight=5),                   # numeric ranking
        prefer(meta("tier") == "gold", weight=2),
        least(load()),
    ),
)
```

- `require(...)`: `meta(...)` comparisons join `label(...)`/
  `label_for(...)` as valid terms, `==`/`!=` only, compared in string
  form (numbers via `str(float)`; authors should filter on string
  metadata and rank on numeric metadata). Fail-closed semantics are
  unchanged: an absent key fails `==` and passes `!=` (the documented
  inversion, which makes `meta("maintenance") != "true"` work without
  seeding every worker). `optional()`/`when()` compose unchanged.
  There is no dynamic `meta_for(...)` — metadata keys are static;
  input-completed keys can be added later if a use case appears.
- `score(...)`: `meta(...)` is valid in `prefer()` (equality) and in
  `least()`/`most()` (numeric, 0–1 normalized across the eligible set
  like every other term; non-numeric or absent values score 0).
- The AST extractors (`_extract_require`, `_extract_routing`) accept
  `meta` exactly like `label`/`metric` — policies remain data, no user
  code runs on the server.
- Static dict affinity (`affinity={"k": "v"}`) is untouched — it keeps
  matching labels only.

### Freshness: effective on the next dispatch, no reconnect

The event dispatcher ranks in-memory `WorkerInfo` snapshots taken at
SSE connect, and admin writes can land on any replica. Two mechanisms
keep dispatch honest:

1. **Refresh inside the dispatch transaction.** `next_executions_batch`
   and `next_resumes_batch` load the candidate workers' metadata in one
   PK-indexed query per batch and overwrite each `WorkerInfo.metadata`
   before matching/scoring. The poll path (`next_execution`,
   constrained branch) refreshes the polling worker the same way. This
   makes "updates take effect on the next claim/dispatch" true by
   construction on every replica, at the cost of one cheap query per
   dispatch cycle.
2. **Wake on write.** The admin write also updates the local replica's
   in-memory copies (`_worker_info`, `_worker_cache`) and sets the
   dispatch wakeup (local `_work_available` + cross-replica NOTIFY), so
   an execution parked waiting for `require(meta(...))` dispatches
   promptly after the operator flips the value rather than on the next
   fallback tick.

## CLI

Sub-group under the existing `flux worker` group, server-side (unlike
`pause`/`resume`, which drive the local control socket):

```
flux worker metadata show  NAME
flux worker metadata set   NAME key=value [key=value ...] [--string] [--replace]
flux worker metadata unset NAME KEY
flux worker metadata clear NAME
```

`set` parses values as numbers when they look numeric (`--string`
forces string typing); `--replace` swaps the dict instead of merging.
Output is the resulting metadata as JSON, matching the other admin
groups.

## Security analysis (delta from today)

- Metadata is written only through `admin:workers:manage` routes; the
  worker-facing surface (registration, pong, checkpoint) cannot touch
  it. A hostile worker can therefore *observe* its metadata (via the
  unpermissioned `GET /workers`, same as labels) but not change it.
- The `meta()` selector reads only the server-held dict, so no label or
  metric a worker advertises can satisfy a `meta(...)` term — the
  authoritative channel cannot be spoofed across the boundary.
- Values are bounded (count, key length, value length) at the API edge,
  so the column cannot be grown without bound by a compromised admin
  token any faster than any other admin write.

## Testing

- Unit: validation (`validate_worker_metadata`), registry CRUD +
  survival across re-registration, `meta()` DSL factories + AST
  extraction, `require_matches`/`pick_worker` with metadata (filtering,
  ranking, absent-key semantics), admin route auth + 404/400 paths,
  dispatch-time refresh (stale in-memory snapshot, fresh DB value
  wins).
- Migration parity: `tests/flux/test_migrations.py` HEAD →
  `0013_worker_metadata`.
- E2E: register a workflow with `affinity=require(meta("allowed") ==
  "true")`; run it — execution stays queued; set the metadata via
  `flux worker metadata set`; assert it dispatches and completes
  without the worker reconnecting.

## Rollout / compatibility

Additive throughout: nullable column, new routes, new selector. Old
workflows and workers are unaffected; a `meta(...)` policy evaluated
against a fleet with no metadata behaves like any absent selector value
(fails `==` requires, scores 0). No config knobs.
