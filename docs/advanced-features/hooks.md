# Outbound hooks

An **outbound hook** is a named subscription: when an engine event matches
one of the hook's selectors, Flux starts a target workflow with a redacted
envelope of that event as its input.

```bash
flux hook create notify-approvals \
  --on "task:release:*:promote_prod:awaiting_approval" \
  --workflow ops/notify_slack \
  --principal ops-sa
```

From then on, every time the `promote_prod` task in a `release/` workflow
pauses for approval, `ops/notify_slack` runs with the event as its input —
and posts to Slack, opens a ticket, or whatever else its tasks do.

Hooks are outbound only. Ingress into a running workflow is already covered
by resume-with-input, approval decisions, and workflow services, so there is
no inbound hook entity.

## Why the only action is running a workflow

There is one action, `run_workflow`. Delivering to Slack, PagerDuty or a
GMUD system is what the **target workflow** does, with ordinary tasks —
because everything a direct webhook action would need already exists as a
workflow feature:

| Webhook delivery needs | The target workflow already has |
|---|---|
| HTTP retry policy | `retry_max_attempts` / `retry_delay` / `retry_backoff`, plus `fallback` and `timeout` |
| Credential storage and signing | `secret_requests` against the encrypted secret store |
| Body templating | plain Python in the task body — no interpolation language, no injection surface |
| A deliveries log to debug from | the delivery **is** an execution: its events, retries and failure are inspectable like any other run |

So "delivery" collapses into creating an execution — a local database write
with no third party on the path. The cost is one execution per fired event,
heavier than one POST; `durability="transient"` on the notification workflow
and the [retention sweep](#retention) bound what that accumulates.

## Selector grammar

A selector is a `:`-delimited string naming an engine event. It uses the
same wildcard rules as permissions: a terminal `*` matches any number of
remaining segments, a `*` anywhere else matches exactly one.

```
execution:<namespace>:<workflow>:<state>          # ExecutionState transitions
task:<namespace>:<workflow>:<task>:<event>        # task events
```

States and event types are matched by their lower-cased value — `paused`,
`completed`, `failed`, `awaiting_approval`, `approved`, `rejected`,
`retry_started`, and so on. Approvals need no domain of their own: the
approval lifecycle is already three task event types.

```
execution:*                                     # every state transition
execution:*:*:failed                            # any workflow fails
execution:release:*:paused                      # anything in release/ pauses
task:release:*:promote_prod:awaiting_approval   # one gate, fleet-wide
task:release:*:*:rejected                       # any rejection in release/
```

A hook carries a **list** of selectors, exactly as a role carries
permissions, and fires once for an event matching any of them — several
selectors are an OR, not a fan-out. "Global vs. workflow-specific" is not a
mode, it is wildcard width.

!!! warning "Wide selectors are loud"
    Every selector can fire; no event class is whitelisted. `task:*`
    subscribes to every task event of every execution, and each match writes
    a delivery row and starts an execution. That is legal, self-inflicted,
    and visible in `flux hook deliveries`.

## What the target receives

The target workflow's input is the envelope:

```json
{
  "hook": "notify-approvals",
  "selector": "task:release:*:promote_prod:awaiting_approval",
  "delivery_id": "…",
  "event_key": "…",
  "attempt": 1,
  "hop": 0,
  "event": {
    "domain": "task",
    "type": "awaiting_approval",
    "execution_id": "…",
    "workflow_namespace": "release",
    "workflow_name": "promote_prod_pipeline",
    "task_name": "promote_prod",
    "task_call_id": "…",
    "state": null,
    "value": { "…": "the event's value" },
    "occurred_at": "2026-08-14T12:00:00Z"
  }
}
```

`state` mirrors `type` for the `execution` domain and is null for `task`.
So a target workflow is an ordinary workflow taking a dict:

```python
from typing import Any

from flux import task, workflow, ExecutionContext


@task.with_options(secret_requests=["slack-webhook"])
async def post_to_slack(text: str, secrets: dict[str, Any] = {}) -> None:
    import httpx

    await httpx.AsyncClient().post(secrets["slack-webhook"], json={"text": text})


@workflow
async def notify_slack(ctx: ExecutionContext[dict]):
    event = ctx.input["event"]
    await post_to_slack(
        f"{event['workflow_name']} is {event['type']} "
        f"(execution {event['execution_id']})",
    )
```

**Redaction.** The envelope is passed through the redaction module when it
is built, so known secret values are scrubbed before the payload is stored
or handed on. Values from `sensitive` tasks are already `[REDACTED:sensitive]`
at storage and never reach the envelope at all.

## Delivery semantics

Firing is a **transactional outbox**. The delivery row is written in the
same transaction as the event it reports, so no event is ever missed and no
delivery ever blocks a checkpoint. The **drain** then runs on the scheduler
tick, under the same cross-replica lock as the other sweeps, and turns each
due row into an execution.

Two consequences worth planning around:

- **Latency is a scheduler tick**, not a millisecond. `[flux.scheduling]
  poll_interval` (default 30s) bounds it.
- **Deliveries are at-least-once.** Retries use exponential backoff, and a
  row goes `dead` after the hook's `max_attempts`. **Target workflows should
  be idempotent per `event_key`** — the same discipline the replay model
  already demands. Deduplicating on `ctx.input["event_key"]` (or using it as
  an idempotency key against the system being called) is the intended
  pattern.

Some failures are terminal rather than retried, because retrying only buries
the audit trail:

| Situation | Outcome |
|---|---|
| Target workflow not in the catalog | `dead`, with the error recorded |
| Principal disabled, or lacking run permission at fire time | `dead` |
| Hook disabled while deliveries were queued | `dead` — `enabled=false` is a stop button, and one that lets the backlog drain is not one |
| Hop limit reached | `dead`, before anything is authorized or created |
| Anything else (busy database, transient read) | retried with backoff until `max_attempts` |

There is **no cross-delivery ordering**. Per-execution ordering may be added
later; it is not promised now.

### The hop guard

The envelope carries a `hop`. A delivery for an event from an execution no
hook started is hop 0; an execution started by a hook stamps `hop + 1` onto
the deliveries its own events produce. `[flux.hooks] hop_limit` (default 3)
is the number of links a chain may have: hops 0, 1 and 2 fire, and a
delivery that has *reached* the limit is dead-lettered with a loop error.

Without it, `execution:*:*:completed` targeting any workflow is a fork bomb —
each completion starts an execution whose completion starts another.

### Inline executions

`wf.run(...)` — the inline path used by scripts and `tests/examples/` —
persists events through the same door, so it **enqueues deliveries
normally**. But the drain lives in the server's scheduler tick, so those
rows stay `pending` until a server runs against the same database. Hooks are
a distributed-path feature in practice; an inline run alone will not deliver
anything.

## Permissions

House grammar, `hook:<name>:<verb>`:

| Method | Path | Required permission |
|--------|------|---------------------|
| `POST` | `/hooks` | `hook:*:create` |
| `GET` | `/hooks` | `hook:*:read` |
| `GET` | `/hooks/{name}` | `hook:{name}:read` |
| `PUT` | `/hooks/{name}` | `hook:{name}:update` |
| `DELETE` | `/hooks/{name}` | `hook:{name}:delete` |
| `POST` | `/hooks/{name}/test` | `hook:{name}:update` |
| `GET` | `/hooks/{name}/deliveries` | `hook:deliveries:read` |
| `POST` | `/hooks/{name}/deliveries/{id}/retry` | `hook:deliveries:retry` |

Built-in roles: `admin` covers everything through `*`; `operator` gets full
hook management (`hook:*`, `hook:deliveries:read`, `hook:deliveries:retry`);
`viewer` gets `hook:*:read` and `hook:deliveries:read`; `worker` gets
nothing.

**The hook's principal.** A hook stores the `principal_id` it runs its
target as — stated explicitly, never inferred from whoever created it, since
the hook outlives that request. That principal must hold run permission on
the target workflow, and the check runs twice: at create/update, so a
misconfiguration fails at the door, and again at fire time, so a later
revocation dead-letters deliveries instead of silently bypassing RBAC.

Because a hook observes events and acts under a stored principal,
`hook:*:create` sits above `workflow:*:register` in the hierarchy — creating
one is a privilege decision, not a convenience.

## CLI

```bash
flux hook create <name> --on <selector> --workflow <ns/name> --principal <subject> \
                        [--on <selector> ...] [--max-attempts 5]
flux hook list [--enabled-only]
flux hook get <name>
flux hook update <name> [--on ...] [--workflow ...] [--principal ...] \
                        [--max-attempts N] [--enable | --disable]
flux hook delete <name> [--yes]
flux hook test <name>                       # fire once with a synthetic event
flux hook deliveries <name> [--limit 50]
flux hook retry <name> <delivery_id>        # hand a dead row back to the drain
```

Every command takes `--format simple|json` and `--server-url`.

`flux hook test` starts the target with a synthetic envelope built from the
hook's first selector, so a broken target or an under-privileged principal
surfaces before an incident does rather than during one. It writes no
delivery row and ignores `enabled` — a hook is tested precisely while it is
off, before it is trusted with real events.

`flux hook update <name> --disable` is the stop button: matching stops
immediately, and anything already queued dead-letters rather than draining.

## Operating

```bash
flux hook deliveries notify-approvals --limit 20
```

```
✗ 6f2c… - dead
   Event: 1a7b… | Attempts: 5
   Error: target workflow 'ops/notify_slack' not found: …
```

A `dead` row is a delivery Flux gave up on. Fix the cause — re-register the
target, restore the principal's role, re-enable the hook — then hand the row
back:

```bash
flux hook retry notify-approvals 6f2c…
```

Retrying resets the row to `pending` with a full attempt budget: it was
given up on, and an operator retrying it is saying the reason it died has
been dealt with.

### Retention

Settled deliveries (`delivered` and `dead`) older than
`[flux.retention] retention_days` are removed by the retention sweep along
with expired executions. `pending` rows are never swept, whatever their age:
they are unmet obligations, and deleting one would drop a delivery silently
instead of dead-lettering it visibly.

## Configuration

```toml
[flux.hooks]
enabled = true             # master switch; false stops all matching
hop_limit = 3              # links a chain of hook-started executions may have
drain_batch_size = 20      # deliveries settled per scheduler tick
snapshot_ttl_seconds = 5.0 # max age of a replica's cached enabled-hook index
```

`enabled = false` stops the outbox before it matches anything; existing hook
rows are untouched, they simply stop firing. `snapshot_ttl_seconds` bounds
how long one replica can keep matching a hook another replica just changed —
a local write invalidates its own replica's cache immediately, and the TTL
covers the cross-replica gap.

## Limitations (slice 1)

- Hooks are created through the API and CLI only. Declaring them on a
  workflow (`@workflow.with_options(hooks=[...])`) or on an agent definition
  is specified but not yet implemented.
- No `webhook` action, and no body-templating language: external calls are
  the target workflow's job.
- No per-execution delivery ordering.
- No console panel; the deliveries API is designed to feed one later.
