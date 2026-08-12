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

Two nullable `SignedPickleType()` columns, matching `executions.input` and
`schedules.input_data`:

- `executions.routing_input` — per-execution values
- `schedules.routing_input` — a fixed set stamped onto every run the schedule
  fires

One Alembic revision adds both. No backfill: NULL means "no routing values",
which is how every existing execution behaves. Values are never cleared, the
same treatment as `preferred_worker`.

`SignedPickleType` rather than the `Base64Type` used by `wf_metadata` and
`worker_metadata`: those are server-derived or admin-validated, whereas routing
values are **caller-supplied execution data deserialized inside the dispatch
loop**. `Base64Type` is unauthenticated `dill`, and `dill` executes arbitrary
code on load — which is exactly why `executions.input` and
`schedules.input_data` are signed. Routing values sit on the same footing and
get the same protection.

```
caller ──header/field/kwarg──▶ routing_input column ──▶ dispatch matching
                                       │
                                       └──✗ never joins `input`
```

Four surfaces must stay clear of the values, each a real exposure route:

| Surface | Why it matters |
|---|---|
| `ExecutionContextModel.to_plain` / `from_plain` | the real chokepoint — `from_json`/`to_dict` are downstream of it |
| `ExecutionContext.summary()` and `FluxEncoder` | the detailed execution read |
| `ContextManager.get_summary` | a *separate* column projection serving the non-detailed read |
| `ExecutionSummaryResponse` | the response model |
| both SSE frames | `execution_scheduled` **and** `execution_resumed` |
| **dispatch diagnostics written to `output`** | see below |

`preferred_worker` and `required_worker` are absent from the first three
already, so those follow an existing path rather than inventing one. The fourth
is new and specific to this feature.

### The diagnostics leak

`_Problem` messages in `flux/routing.py` embed the offending key and resolved
value verbatim — for example `"input '{path}' resolves to an invalid service
name: '{fragment}'"` and `"...invalid {kind} key: '{key}'"`.
`_fail_undispatchable` writes that message into `model.output`, and `output` is
returned by `summary()`. So an *invalid* routing value would be readable
through the execution API.

That is not a hypothetical for the threat model, because the **`worker`
built-in role holds `execution:*:read`** alongside `worker:*:*`. The worker can
therefore call the execution read API itself. "Not visible to the worker" means
more than "not in the frame we send it".

Wording discipline decays, so this is structural rather than editorial: a
`_Problem` arising from a routing reference collapses to a fixed string —
`"routing constraint unsatisfied"` — carrying neither the resolved value nor
which routing key was involved. The unresolved-term message needs the same
treatment; `"requires input 'x', which is not present"` is a presence oracle
over the complement set, which is the very thing being hidden.

## Expression surface

`routing_input("cohort")` mirrors `input("cohort")`: a per-execution value reference,
not a worker attribute.

Note that `input(...)` has **three** different compiled encodings, not one, and
a routing reference needs a twin for each. Missing one does not fail loudly —
it resolves against `input` instead, silently reading the wrong source for the
value the feature exists to hide:

| Encoding | Produced by |
|---|---|
| `{"$input": path}` | comparison values in `require`/`prefer` |
| `{"input": path, "op":…, "value":…}` | `when(input(...) == const, …)` |
| `{"kind":…, "prefix":…, "input": path}` | `label_for(...)` / `meta_for(...)` / `service(...)` |

```python
@workflow.with_options(
    affinity=require(optional(label("cohort") == routing_input("cohort"))),
    routing=score(prefer(label("cohort") == routing_input("cohort"), weight=10)),
)
async def audit_probe(ctx: ExecutionContext[dict]):
    ...
```

Valid wherever `input(...)` is valid: `require` terms, `prefer(...)`,
`when(...)` (with the same `==`/`!=` restriction `input()` has there), and
dynamic keys via `label_for("cache.", routing_input("dataset"))`. Rejected in
`least()`/`most()`, which take worker selectors — `input()` is rejected there
too, so the rule is unchanged rather than special-cased.

Unresolved values behave exactly like unresolved input: the term cannot
discriminate, `optional(...)` forgives an absent value, and a non-optional
`require` term that resolves to nothing fails the match.

### Plumbing

There are **three** dispatch-side entries, not one, and missing the second is
worse than missing either:

- `worker_matches(worker, requests, affinity, runner, input_value)` — the
  matcher, which gains a `routing_value` parameter.
- `require_diagnostic(workflow.affinity, model.input)`, called directly by
  `_affinity_diagnostic` and bypassing `worker_matches` entirely. It decides
  whether an affinity expression can *never* match, and
  `_fail_undispatchable` turns a positive answer into a terminal
  `AffinityResolutionError`. If it is not also given the routing value, a
  non-optional `require(label("cohort") == routing_input("cohort"))` resolves
  unresolved, diagnoses as permanently unsatisfiable, and **every
  routing-matched execution is failed at dispatch instead of routed**. This is
  the single most important line of this section.
- `pick_worker(...)`, the score stage, called with `input_value=model.input`.
  The example above puts `routing_input()` inside `score(prefer(...))`, so it needs
  the routing value too.

Behind those, the resolution helpers `_resolve_require_input`,
`_resolve_input_path`, `_resolve_require_term`, `_resolve_selector_key` and
`_when_condition_active` each need the routing value threaded through, covering
all three encodings above. Counting occurrences of the literal `$input` in
`flux/routing.py` undercounts this — one of them is the emission site, and the
`when()` and dynamic-key encodings do not contain the string at all.

`Condition.__init__` and `DynamicLabel.__init__` both `isinstance(..., InputRef)`
to decide what a comparison value or dynamic key may be. A routing reference
that is not an `InputRef` subclass raises `"value must be a constant or
input(...)"` from the spec's own examples, so those checks widen to accept
either.

The catalog has **two independent AST parsers** — one for `affinity`, one for
`routing` — with roughly ten `call_name(...) == "input"` checks between them.
Each needs a `routing` twin, so an unparseable reference fails at registration
rather than at dispatch. (#208 touched only the routing-stage parser, which is
why the two are easy to conflate.)

### What stays public

The *key name* is not secret — it is written in the workflow source, and that
source travels to the worker. What is hidden is the per-execution value **and
whether this execution had one at all**. That is the property #211 asks for:
the classifier it describes is presence, and presence moves out of reach. A
worker can read `routing_input("cohort")` in its own affinity and still not know
whether it was selected by it.

## Ingress

Three adapters, one validator behind them, so the rules cannot drift:

| Path | Channel |
|---|---|
| run endpoint | `X-Flux-Routing-Input:` header, a JSON object |
| schedules | `routing_input` field on create/update |
| `call()` | `routing_input=` kwarg on the child |

There is a **fourth** `_create_execution` caller: service invocation
(`api/service_routes.py`), which `service_mcp.py` proxies onto. It is
deliberately excluded — a workflow service is a stable public endpoint whose
callers are the workflow's consumers, not its operators, and the sampling and
canary cases this design serves are operator-initiated. Excluded means the
header is *ignored* there rather than rejected, since service callers never set
it; if that turns out to be wrong it is an additive change.

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
- **A repeated `X-Flux-Routing-Input` header is a 400 by rule**, not by
  accident. Starlette joins duplicates with `", "`, which happens to produce
  invalid JSON today — resting the rejection on that coincidence is exactly
  the silent-drop risk this section exists to remove.
- **Nesting depth and key count are bounded** as well as byte size: 4KB of
  JSON permits deeply nested objects, and path resolution walks one level per
  dot.
- **No key at any depth may contain `.`** — path resolution splits the whole
  path on dots and descends one level per part, so `{"a": {"b.c": 1}}` is
  exactly as unreachable as `{"a.b": 1}`: `routing_input("a.b.c")` looks for
  `a → b → c`. The validator recurses; a rule written only for top-level keys
  would re-admit the silent unreachability it exists to prevent. Nested objects
  remain allowed — only the ambiguous spelling is refused, at every level.

An absent header means no routing values, and expressions resolve as
unresolved — existing behaviour, so an execution submitted without it routes
exactly as it does today.

### Schedule read-back

`ScheduleResponse` already omits `input_data`, so omitting `routing_input` is
the consistent choice and is what this design does. Worth noting alongside:
`schedule` is not in `EXECUTION_TOKEN_RESOURCES`, so an execution token cannot
reach the schedule API at all, whatever it returns.

### No new permission

Deliberate contrast with `X-Flux-Require-Worker`, which *compels* a named node
to run the code — a capability beyond running the workflow, hence
`worker:{name}:target`.

Stated precisely, because the loose version is wrong: routing values **can**
steer placement. A workflow author who writes
`require(label("host") == routing_input("host"))` hands callers node-level pinning
with no `worker:{name}:target` grant. The argument is not that routing values
are inert; it is that they are **exactly as powerful as `input`, no more** —
`require(label("host") == input("host"))` grants the identical thing today. The
load-bearing step is that the published `input_schema` is discovery metadata
the run endpoint never enforces, so `input` is already unconstrained and
`routing_input` is no less so.

This inherits an existing gap rather than opening a new one, which is why it
needs no new permission. Pretending the gap does not exist would be the
mistake.

### `call()` and the in-process fast path

`call()` has a fast path that never creates an execution row: a `mode="sync"`
call to a transient workflow with `runner in (None, "inprocess")` and
`transient_fast_path` enabled runs `_call_in_process(...)` in the caller's
process, with no server round-trip and therefore no dispatch matching. A
`routing_input=` on that path would be silently discarded — precisely the
failure the validation rule above forbids.

`routing_input` therefore **forces the dispatched path**: supplying it disables
the fast path for that call. Rejecting instead would be defensible, but it
would make the flag's behaviour depend on a performance optimisation the caller
did not choose and may not know about.

**`routing_input` must be excluded from the task-id digest.** `_compute_task_id`
SHA256s `[full_name, task_args, args, kwargs]` and the result becomes the
`source_id` of the recorded `TASK_STARTED` event. A worker holding
`execution:*:read` can fetch the *parent* execution, take that digest, and
offline-test candidates over what is usually a very small cohort space —
recovering both presence and value for the child. The occurrence counter
already disambiguates repeated calls, so dropping it from the digest costs
nothing.

**`FluxClient` needs a per-request header hook.** `run_workflow` takes no
headers today, and `for_current_execution` sets `X-Flux-Preferred-Worker` as a
*client-level* default — so per-call routing values need new plumbing rather
than reuse.

A parent can set routing values on a child it spawns and cannot read them back
afterwards. That is consistent — the parent knows what it set — but it means a
workflow can influence its children's placement through a channel invisible to
those children. That is correct for canary orchestration and is stated here
rather than left to be discovered.

## CLI

```bash
flux workflow run audit_probe '{"batch": 41}' -r cohort=canary -r region=eu
flux schedule create audit_probe nightly-canary --cron "0 2 * * *" -r cohort=canary
```

Both commands take positional arguments the examples must respect:
`workflow run WORKFLOW_NAME INPUT` and `schedule create WORKFLOW_NAME
SCHEDULE_NAME`.

Repeatable `key=value`, mirroring `--label` on `flux start worker`. Named
`--routing-input` (short `-r`) to match the column, schedule field, and `call()`
kwarg — the selector that reads it is `routing_input(...)`, so one word means
one thing throughout. `-r` is
unused on both commands today (`workflow run` uses `-m/-v/-d`, `schedule
create` uses `-c/-tz/-d/-i/-f`).

- **Values arrive as strings**, as with `--label`. Mostly harmless: `_compare`
  numeric-coerces when both sides parse as floats, so `"8"` does match a
  `meta()` value of `8`. **Booleans are the real gap** — `_as_float` returns
  `None` for bool, so a CLI `"true"` is compared as `"true"` against `"True"`
  and never matches. A caller needing a boolean goes through the API.
- **Nesting is not expressible from the CLI.** The header accepts nested
  objects and `routing_input("a.b")` descends them, but `key=value` is flat and dots
  are refused in keys. The CLI covers flat keys; nested structures are an
  API/SDK affair. Better an explicit limit than a dotted-key spelling that
  conflicts with path resolution.

Malformed pairs (no `=`, empty key, duplicate key) fail before the request goes
out.

## Operator visibility

There is deliberately **no API read path** for routing values, at any privilege
level. Adding an admin-scoped read would put the values back on the surface
this design exists to keep them off, and the permission boundary protecting it
would then be the guarantee — the same fragility as stripping at delivery.

That leaves a real gap: an operator asking "why is this worker getting all the
traffic?" cannot see which executions carried routing values. Closing it
without reopening the leak, the server logs at ingress the execution id and the
**key names** set — never the values. Keys are already public (they appear in
the workflow source); the per-execution values and their presence are not, and
the log is not reachable through the execution API the worker can call.

Anything beyond that is a database read, which is the same answer as for
`preferred_worker` today.

## Testing

The guarantee needs tests that fail when it breaks, because nothing else
notices. One assertion per surface in the table above, mirroring it one-to-one
so the checklist is auditable at review time, and each verified by temporarily
adding the field rather than assumed:

- absent from `to_plain`/`from_plain` (and therefore `from_json`/`to_dict`)
- absent from `summary()` and the `FluxEncoder` output
- absent from `get_summary`'s projection and `ExecutionSummaryResponse`
- absent from **both** SSE frames — `execution_scheduled` and
  `execution_resumed`, which reach the builder by different paths
- absent from the failure `output` written by `_fail_undispatchable`
- absent from the parent's event log for a `call()` child, including the
  `source_id` digest

Behavioural coverage: matching resolves from `routing_input` in `require`,
`prefer`, `when`, and a dynamic key; unresolved routing values behave like
unresolved input; values arrive correctly from all three ingress paths and the
CLI; every rejection case returns 400 rather than dropping.

One test specifically for the threat model: two executions of the same workflow,
one with routing values and one without, are indistinguishable **as the worker
principal sees them** — identical in-process context *and* identical responses
from the execution read API called with `worker:*:*` + `execution:*:read`,
which is what the `worker` built-in role actually holds. Comparing only the
in-process context would test a proxy for the claim rather than the claim.

The invalid-value case gets its own test, since it is the one path that writes
a routing-derived string into `output`: an unmatchable routing value must
produce a diagnostic naming the key and not the value.

Migration `HEAD` updated in both `test_migrations.py` and
`test_migrations_postgresql.py`, since a stale value in the second surfaces
only in CI's postgres job.

## Documentation

A section in `docs/advanced-features/dynamic-routing.md` beside the binding-header
material, and `routing_input()` added to the selector table.

## Naming

The selector is `routing_input(...)`, not `routing(...)`.

`routing(...)` collides with `@workflow.with_options(routing=score(...))`,
which means something else entirely — a scoring policy, not a per-execution
value — and it would land at `flux.routing.routing`. Naming it
`routing_input` matches every other artifact in the design: the column, the
header, the schedule field, the `call()` kwarg, and the CLI flag. One word,
one meaning, everywhere.

The cost is a longer name inside expressions, which is the right trade for a
term that appears in workflow source and gets read far more often than it is
typed.

If a top-level `flux` export is ever wanted, note that `flux/__init__.py`
already resolves a submodule-vs-attribute collision for `flux.task` and
`flux.workflow`, and `flux.routing` is a submodule too — so exporting a
`routing_input` name from it needs the same care.

## Out of scope

- **Per-key encryption at rest.** The column is unreadable through the API;
  encrypting it would imply a confidentiality guarantee against database access
  that nothing else here makes.
- **Wildcard or glob matching** on routing values beyond what `input()` does.
- **Clearing values at dispatch.** Resume re-matches (`next_resumes_batch`
  calls `_worker_matches_workflow` for unassigned rows), so clearing at first
  dispatch would silently change where a paused execution lands.
