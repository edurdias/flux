# Spec: dynamic workflows — ephemeral agent registration

**Date:** 2026-07-15 · **Status:** implemented (PR 2 of the
dynamic-workflows series) · **Depends on:** `docker-airgapped` runner
(merged, #130).

## Motivation

Agents that can *author* workflows at runtime get real control flow, fan-out,
and durability for multi-step work — without a human pre-registering anything
— and programmatic tool calling falls out as a special case (a script whose
bindings are the agent's tools). PR 1 made the execution side safe: full
Python in a container whose only I/O channel is the supervised stdio
protocol. This PR adds the authoring side: how model-written source enters
the catalog, under whose identity, with what limits, and for how long.

The design principle carried over from PR 1: **dynamic workflows are ordinary
workflows.** Same catalog, same dispatch, same replay/cancel/approval
machinery. What is new is a *registration path* with an enforced policy — not
a new execution subsystem.

## Threat model

- **Adversary:** the workflow source (LLM-authored — prompt-injectable, so
  hostile by assumption) and, transitively, a compromised agent session.
- **Execution containment:** inherited from PR 1 — registration through this
  path is stamped `runner="docker-airgapped"` server-side, so the code runs
  network-less, read-only, capability-less, resource-capped, wall-clock
  bounded, with the stdio protocol as its only capability channel.
- **This PR's concerns:** (a) *who* may author and *where* their workflows
  live (identity, namespace, permission); (b) the author bypassing the
  containment stamp; (c) resource abuse of the *catalog* (unbounded
  registrations, giant sources); (d) escalation via workflow options
  (schedules, services, resource requests); (e) leftover entries accumulating
  forever.

## Model

An agent (and only an agent — no human/API authoring surface in this PR)
gains two tools:

```
create_workflow(source) -> {namespace, name, version} | {errors}
run_workflow(ref | source, input=None, mode="sync"|"async",
             durability="durable"|"transient") -> result | execution_id
```

`run_workflow` with `source` is register-then-run, idempotent by source hash.
Results return to the agent loop like `call()`/`workflow_agent` results do
today. Everything else — status, cancellation, approvals raised inside the
dynamic workflow — uses the existing surfaces unchanged.

## Identity & authorization

- **Authentication:** the new endpoints accept only the **execution token**
  of the calling agent's own execution (the established mechanism for
  in-workflow server callbacks). No API key / OIDC path — this keeps the
  surface agent-only by construction.
- **Principal & namespace:** the token resolves to the execution's
  principal; workflows live in a per-principal namespace
  `dyn-<subject-slug>-<hash8>` **derived server-side from the token's
  subject, never from the request** — so one principal cannot write into
  another's namespace by construction (the deterministic hash suffix keeps
  distinct subjects distinct even when slugging collides). Flat namespaces
  only: fits `^[a-z0-9][a-z0-9_-]*$`, ≤64 chars.
- **Permission:** `workflow:<derived-namespace>:*:register` plus the
  matching `:run`, granted to the principal the agent's executions run
  under — never part of built-in roles. (Auto-provisioning the grant from
  an agents-table flag is follow-up work: agents do not currently own
  service principals, so enablement today is an explicit operator grant.)
- **Reserved prefix:** the ordinary registration path
  (`POST /workflows`, inline auto-registration) **rejects namespaces starting
  with `dyn-`**, so nothing can squat an agent's namespace or sneak an
  un-stamped workflow into it. This is the enforcement twin of the stamp in
  (b) below.

## Registration pipeline (`POST /workflows/dynamic`)

1. **Size cap** — `max_source_bytes` (default 64 KiB; model-authored
   workflows should be small).
2. **Parse** — the existing catalog AST static parse (unparseable source
   fails loudly, as with routing policies today). Exactly one `@workflow`
   per source; module-level `@task` definitions are allowed.
3. **Policy validation** (registration-time, fail with structured errors the
   agent can act on):
   - `schedule=` is **rejected** — agent code must not create standing
     schedules (auto-scheduling is on by default; a schedule would let a
     one-shot authoring call install permanent recurring execution).
   - Service exposure (HTTP/MCP service options) is **rejected** — no
     agent-authored public endpoints.
   - `requests=`, `affinity=`, `routing=` are **rejected** — the airgapped
     profile and placement are platform-owned.
   - `namespace=` and `runner=` in source are **rejected** (not silently
     overridden): the server owns both, and an explicit error teaches the
     authoring model faster than a silent rewrite. Implementation is a
     with_options **allowlist** (`name`, `durability`, `secret_requests`,
     `output_storage`); anything outside it is a structured error.
4. **Stamping** — the server writes `runner="docker-airgapped"` into the
   entry's metadata regardless of what the source declares (the author is
   the adversary; AST-extracted options cannot be the enforcement point).
   The required runner is configurable (`require_runner`) for dev setups,
   default `docker-airgapped`.
5. **Idempotency & versioning** — the source hash is recorded in metadata;
   re-registering byte-identical source returns the existing entry (no
   version churn, and the module cache — already source-hash keyed — stays
   warm). Different source under the same name gets the catalog's normal
   version increment.
6. **Quota** — at most `max_per_agent` distinct workflow names per agent
   namespace (default 50); exceeding it is a structured error suggesting
   deletion or reuse.
7. **Bookkeeping** — `created_by` (principal subject), `created_at`,
   `last_used_at` recorded in entry metadata; `last_used_at` refreshed on
   each run.

## Execution

`run_workflow` resolves to the normal run endpoints — dynamic executions are
dispatched, claimed, checkpointed, replayed, cancelled, and approved exactly
like any other. Consequences, stated for the record:

- Dispatch routes only to workers advertising `docker-airgapped`; a fleet
  without one fails the run with the existing runner-matching error.
- `durability="transient"` is the sensible default for cheap one-shots
  (at-most-once, agent retries); `"durable"` is available and — because the
  event log is the forensic record of what model-authored code did — is the
  default for `run_workflow` unless the agent asks otherwise.
- Secrets/configs/approvals requested inside a dynamic workflow relay
  through the parent worker and are authorized against the execution's
  identity, i.e. the agent's grants — nothing widens.
- The executions of `dyn-*` namespaces are visible/scoped through the
  existing workflow-read authz (PR #125/#126 machinery) with no changes.

## TTL / GC

The existing `RetentionJob` gains a dynamic-catalog sweep: entries in `dyn-*`
namespaces whose `last_used_at` is older than `ttl` (default 7 days) are
deleted — **unless** any execution of that workflow is non-terminal (never
collect under a live run; the FK on PostgreSQL enforces the invariant the
query must respect). Sweep cost is bounded by the quota × agent count.

## Config (`[flux.dynamic_workflows]`, all default-off/-conservative)

| Key | Default | Notes |
|---|---|---|
| `enabled` | `false` | master switch; endpoints 404 when off |
| `require_runner` | `"docker-airgapped"` | stamped on every dynamic entry; relaxable for dev |
| `max_source_bytes` | `65536` | registration size cap |
| `max_per_agent` | `50` | distinct names per agent namespace |
| `ttl` | `604800` (7d) | unused-entry GC horizon; `0` disables GC |

Per-agent enablement rides the agent definition (`agents` table): a boolean
that, when set, provisions the namespace grants on the agent's principal.

## What this PR does not do

- No human/CLI/API authoring surface (agents only, per the series decision).
- No vocabulary layer (schema-constrained `agent()`, staged pipeline,
  budget) — PR 3.
- No new isolation machinery — PR 1's runner is consumed, not extended.

## Failure semantics

| Event | Behavior |
|---|---|
| policy violation at registration | structured error to the agent tool (actionable message; nothing persisted) |
| quota exceeded | structured error naming the limit and current count |
| no airgapped-capable worker | run fails with the existing runner-matching error |
| dynamic execution times out / crashes | PR 1 semantics (terminal FAILED / durability-mapped) |
| GC vs live execution | entry kept; swept on a later pass once terminal |

## Testing

- **Unit:** policy validation matrix (schedule/service/requests/namespace
  override), stamping wins over source-declared runner, idempotent re-register
  by hash, version increment on changed source, quota, reserved-prefix
  rejection on the ordinary path, GC keeps-live/collects-stale.
- **Security:** endpoints reject API-key/OIDC identities and foreign
  execution tokens; permission matrix for the per-agent grants (the
  `tests/security/` harness with real role resolution).
- **E2E:** agent authors + runs a dynamic workflow through a real server +
  docker-capable worker (gated like other docker tests); replay after a
  worker kill for the durable case.

## Rollout / compatibility

Additive: new endpoints (404 unless enabled), new config section, one
metadata shape — no migration required if bookkeeping lives in
`wf_metadata`; revisit if GC query cost demands indexed columns. Version:
0.58.0.
