# Routing-only execution input

Design for [#211](https://github.com/edurdias/flux/issues/211): values the
scheduler can match on that the target worker cannot observe.

## Problem

Affinity and scoring expressions match against execution input, and that input
is delivered to the worker — it is how a workflow receives its arguments. So
any field used purely for routing is observable by the worker it routes to.

That is harmless until the routing decision is *about* the worker. For audit
sampling, A/B assignment, or canary probes, "does my input contain the cohort
field?" classifies "am I being watched" before the workflow body runs. The
mechanism meant to make those executions indistinguishable is defeated by the
transport rather than by anything the workflow does.

The presence of the key is the classifier, not its value, so obfuscating the
value does not help, and always sending the field forces a hard pin on
executions that should route freely.

### What is already solved

Pinning to a *named worker* is closed: `X-Flux-Require-Worker` (#210) travels
as a header, lives on its own column, and never reaches the worker. This design
generalises that treatment to arbitrary matching values — cohorts, tiers,
capability flags — where the routing attribute is not worker identity.

### Why stripping at delivery does not work

The rejected shape is a reserved `_routing` namespace removed from what the
worker receives. It leaves the values in the persisted `input`, and a running
workflow can read them straight back:

- `scope_to_execution` filters an execution token by resource *family*, keeping
  `execution:*` rather than narrowing to the row;
- `GET /executions/{id}` requires `execution:*:read`, held by both the
  `operator` and `viewer` built-ins;
- `ExecutionContext.summary()` returns `"input"`.

Response redaction does not help: it scrubs secret-store values by identity,
and a routing field is not a secret. A subtractive guarantee holds only if
every exposure path strips correctly, and one already does not.

## Approach

Store routing values in their own column, never merged into `input`. Additive
rather than subtractive: no path has to remember to strip them, because they
were never in the payload.

## Storage and data flow

Two nullable `Base64Type()` columns, matching how `wf_metadata` and
`worker_metadata` store structured data:

- `executions.routing_input` — per-execution values
- `schedules.routing_input` — a fixed set stamped onto every run the schedule
  fires

One Alembic revision adds both. No backfill: NULL means "no routing values",
which is how every existing execution behaves. Values are never cleared, the
same treatment as `preferred_worker`.

```
caller ──header/field/kwarg──▶ routing_input column ──▶ dispatch matching
                                       │
                                       └──✗ never joins `input`
```

Three surfaces must stay clear of the column, each a real exposure route:

| Surface | Why it matters |
|---|---|
| `ExecutionContext.from_json` / `to_dict` | what the worker receives and replays |
| `ExecutionContext.summary()` | what `GET /executions/{id}` returns |
| the SSE dispatch frame | what travels to the worker at claim time |

`preferred_worker` and `required_worker` are absent from all three already, so
this follows an existing path rather than inventing one.

## Expression surface

`routing("cohort")` mirrors `input("cohort")`: a per-execution value reference,
not a worker attribute. It compiles to `{"$routing": path}` beside the existing
`{"$input": path}`.

```python
@workflow.with_options(
    affinity=require(optional(label("cohort") == routing("cohort"))),
    routing=score(prefer(label("cohort") == routing("cohort"), weight=10)),
)
async def audit_probe(ctx: ExecutionContext[dict]):
    ...
```

Valid wherever `input(...)` is valid: `require` terms, `prefer(...)`,
`when(...)` (with the same `==`/`!=` restriction `input()` has there), and
dynamic keys via `label_for("cache.", routing("dataset"))`. Rejected in
`least()`/`most()`, which take worker selectors — `input()` is rejected there
too, so the rule is unchanged rather than special-cased.

Unresolved values behave exactly like unresolved input: the term cannot
discriminate, `optional(...)` forgives an absent value, and a non-optional
`require` term that resolves to nothing fails the match.

### Plumbing

Hard matching funnels through one entry, `worker_matches(worker, requests,
affinity, runner, input_value)`, which gains a `routing_value` parameter.
Behind it, the five `$input` resolution sites in `flux/routing.py` gain
`$routing` siblings reading from that value. The catalog's AST extractor gains
`routing(...)` alongside `input(...)`, so an unparseable reference fails at
registration rather than at dispatch.

### What stays public

The *key name* is not secret — it is written in the workflow source, and that
source travels to the worker. What is hidden is the per-execution value **and
whether this execution had one at all**. That is the property #211 asks for:
the classifier it describes is presence, and presence moves out of reach. A
worker can read `routing("cohort")` in its own affinity and still not know
whether it was selected by it.

## Ingress

Three adapters, one validator behind them, so the rules cannot drift:

| Path | Channel |
|---|---|
| run endpoint | `X-Flux-Routing-Input:` header, a JSON object |
| schedules | `routing_input` field on create/update |
| `call()` | `routing_input=` kwarg on the child |

### Validation

Rejected, never dropped — a silently discarded routing directive does not
fail, it routes the execution somewhere the caller did not intend, and the
caller cannot tell that from success. Malformed JSON, a non-object payload, an
oversized value, or a bad key is a 400 from HTTP paths and a `ValueError` from
`call()`.

- **Size bound: 4KB** of serialized JSON, measured on the raw header value
  before parsing (and on the equivalent payload for the schedule and `call()`
  paths, so the limit is the same wherever the values enter). A header is not a
  payload channel and servers cap header bytes anyway; an explicit limit makes
  the failure ours and legible rather than a proxy's opaque 431.
- **Keys may not contain `.`** — path resolution splits on dots to descend
  nested objects, so a top-level dotted key would be silently unreachable.
  Nested objects remain allowed; only the ambiguous spelling is refused.

An absent header means no routing values, and expressions resolve as
unresolved — existing behaviour, so an execution submitted without it routes
exactly as it does today.

### No new permission

Deliberate contrast with `X-Flux-Require-Worker`, which *compels* a named node
to run the code — a capability beyond running the workflow, hence
`worker:{name}:target`. Routing metadata only supplies values to expressions
the workflow author already declared. It cannot add a constraint, reach a
worker the declared affinity does not already allow, or target a node the
caller could not reach through `input`. It is the same power the caller has
today, moved to a channel the target cannot read.

### `call()`

A parent can set routing values on a child it spawns and cannot read them back
afterwards. That is consistent — the parent knows what it set — but it means a
workflow can influence its children's placement through a channel invisible to
those children. That is correct for canary orchestration and is stated here
rather than left to be discovered.

## CLI

```bash
flux workflow run audit_probe --routing-input cohort=canary --routing-input region=eu
flux schedule create nightly-canary ... --routing-input cohort=canary
```

Repeatable `key=value`, mirroring `--label` on `flux start worker`. Named
`--routing-input` (short `-r`) to match the column, schedule field, and `call()`
kwarg, leaving `routing()` as the name of the selector that reads it.

- **Values arrive as strings**, as with `--label`. Harmless in practice: they
  are matched against label values, which are strings too. A caller needing an
  int or bool goes through the API.
- **Nesting is not expressible from the CLI.** The header accepts nested
  objects and `routing("a.b")` descends them, but `key=value` is flat and dots
  are refused in keys. The CLI covers flat keys; nested structures are an
  API/SDK affair. Better an explicit limit than a dotted-key spelling that
  conflicts with path resolution.

Malformed pairs (no `=`, empty key, duplicate key) fail before the request goes
out.

## Testing

The guarantee needs tests that fail when it breaks, because nothing else
notices. Three exposure routes, three assertions, each verified by temporarily
adding the field rather than assumed:

- absent from `ExecutionContext.from_json`/`to_dict`
- absent from `summary()`
- absent from the SSE dispatch frame

Behavioural coverage: matching resolves from `routing_input` in `require`,
`prefer`, `when`, and a dynamic key; unresolved routing values behave like
unresolved input; values arrive correctly from all three ingress paths and the
CLI; every rejection case returns 400 rather than dropping.

One test specifically for the threat model: two executions of the same workflow,
one with routing values and one without, produce byte-identical worker-visible
context. That is the actual claim — indistinguishability — rather than a proxy
for it.

Migration `HEAD` updated in both `test_migrations.py` and
`test_migrations_postgresql.py`, since a stale value in the second surfaces
only in CI's postgres job.

## Documentation

A section in `docs/advanced-features/dynamic-routing.md` beside the binding-header
material, and `routing()` added to the selector table.

## Out of scope

- **Per-key encryption at rest.** The column is unreadable through the API;
  encrypting it would imply a confidentiality guarantee against database access
  that nothing else here makes.
- **Wildcard or glob matching** on routing values beyond what `input()` does.
- **Clearing values at dispatch.** Resume re-matches (`next_resumes_batch`
  calls `_worker_matches_workflow` for unassigned rows), so clearing at first
  dispatch would silently change where a paused execution lands.
