# Outbound hooks

Design for outbound hooks as a first-class entity: named subscriptions that
start a workflow when engine events matching a selector occur. Outbound only
— ingress is already covered by resume-with-input, approval decisions, and
workflow services, so no inbound hook entity exists.

The single action is **run a workflow**. Calling an external API (Slack, a
GMUD system, PagerDuty) is what the *target workflow* does, with ordinary
tasks. This is deliberate — see "Why the only action is a workflow".

Immediate consumers:

- `approval_routing="notify"` (issue #144) — its pending delivery mechanism
  becomes "whatever hooks match the approval event"; the agent loop stays
  transport-agnostic.
- Chat/ops notification for approval gates (deploy-promotion GMUD flows),
  via a notification workflow that POSTs to Slack.
- Event-driven composition — "on any `release/*` failure, run
  `release/incident_report`" as a stored row instead of code in every
  workflow.

## Why the only action is a workflow

An earlier draft had two actions, `webhook` and `run_workflow`. Direct
webhook delivery drags in machinery the engine would have to own: HTTP retry
policy, credential storage and signing (HMAC/bearer), body templating for
receivers with fixed schemas, and a template-validation surface. Every one
of those already exists as a workflow feature:

- **Retries** — the notification task's own `retry_max_attempts` /
  `retry_backoff`, plus fallback and timeout, instead of a parallel
  hook-level retry policy.
- **Credentials** — `secret_requests` against the encrypted secret store,
  instead of a hook-level `secret_ref` and auth-mode field.
- **Body shaping** — plain Python in the task body, instead of a
  placeholder-interpolation template language and its injection surface.
- **Audit and debugging** — the delivery *is* an execution: its events,
  retries, and failure are inspectable in the console and CLI like any
  other run, instead of a bespoke deliveries log being the only trace.

"Delivery" collapses from an outbound HTTP call into creating an execution —
a DB write with no third party on the path. External flakiness becomes the
child workflow's problem, handled by the error-handling chain built for
exactly that.

Cost: one execution per fired event, heavier than one POST. Mitigations
exist today: notification workflows can declare `durability="transient"`
(only outer lifecycle checkpoints persist) and the retention sweep
(`flux/retention.py`) bounds accumulation. High-frequency selectors are
opt-in and visible. A `webhook` action can be added later without schema
surgery if a no-execution path ever proves necessary; it is out of scope
now.

## Problem

The engine records every state transition and task event durably, but nothing
*pushes* them out. A paused approval gate is discoverable only by polling
(`GET /approvals`, the console inbox, the CLI). External systems that must
react to executions — chat channels, ticketing, downstream pipelines — have
no subscription surface, and `approval_routing="notify"` is declared but
mechanism-less (`flux/tasks/ai/approval_policy.py`).

## Selector grammar

Selectors reuse the permission wildcard matcher
(`flux/security/identity.py::_wildcard_match`) verbatim: terminal `*` matches
any number of remaining segments, non-terminal `*` matches exactly one. Two
domains, mirroring what the engine emits (`flux/domain/events.py`):

```
execution:<ns>:<workflow>:<state>          # ExecutionState transitions
task:<ns>:<workflow>:<task>:<event>        # ExecutionEventType occurrences
```

States and events are matched by their lower-cased enum value (`paused`,
`failed`, `awaiting_approval`, `approved`, `rejected`, `retry_started`, …).
Approvals need no separate domain: the approval lifecycle is already three
task event types.

Examples:

```
execution:*                                     # every state transition
execution:*:*:failed                            # any workflow fails
execution:release:*:paused                      # anything in release/ pauses
task:release:*:promote_prod:awaiting_approval   # the #144 selector
task:release:*:*:rejected                       # any rejection in release/
```

A hook row carries a *list* of selectors, exactly as a role carries
permissions. "Global vs. workflow- or task-specific" is not a mode — it is
wildcard width. The shape deliberately rhymes with the approve permission
`workflow:<ns>:<wf>:task:<task>:approve` so operators learn one grammar.

Every selector can fire; no event class is whitelisted. Subscribing to
`task:*` is legal and loud, and the cost is self-inflicted and visible in the
deliveries table. The docs carry the warning; the engine does not second-guess
the selector.

## Entity and storage

Two tables, one Alembic revision, ORM columns added together per the
migrations contract:

```
hooks
  id, name (unique), enabled
  selectors        JSON list of selector strings
  action           "run_workflow"   # enum; reserved for future variants
  workflow_ref     target ("ns/name")
  principal_id     the hook executes the target as this principal
  owner_type       "user" | "workflow" | "agent"
  owner_ref        registering principal / "ns/name" / agent name
  max_attempts, created_by, created_at, updated_at

hook_deliveries
  id, hook_id, event_key, payload, attempts
  status           "pending" | "delivered" | "dead"
  execution_id     the started execution (the hook→run audit link)
  next_attempt_at, last_error, created_at, delivered_at
```

No URL, no secret, no auth mode, no template on the row: everything about
*how* the external world is reached lives in the target workflow. The hook
row only says *when* and *what to start, as whom*.

## Firing: transactional outbox

Even with delivery reduced to a local execution insert, it must not run
inline in the checkpoint write path: creating an execution means a catalog
lookup, a fire-time permission check, and hop-guard bookkeeping, none of
which belong inside the hot transaction that persists a state transition.
The enqueue — a minimal outbox row — is transactional with the event it
reports:

- Execution state transitions enqueue in the same transaction as the state
  write (`flux/context_managers.py`).
- Task events (including the approval lifecycle rows, already written
  atomically via `UnitOfWork`) enqueue in the same transaction as the event
  append.

No event is ever missed and no delivery ever blocks a checkpoint.

The drain runs in the scheduler tick under the cross-replica dispatch lock,
alongside its existing sweeps (park-TTL, wake conditions, orphaned
cancellations). On PostgreSQL the tick is nudged by LISTEN/NOTIFY, the same
mechanism the dispatcher uses; on SQLite the poll interval bounds latency.

Matching cost stays flat: enabled hooks compile into an in-memory index
bucketed by domain and first concrete segment, invalidated on hook CRUD —
the worker-registry pattern.

## Delivery semantics

Delivery means creating an execution of the target workflow with the
envelope as input, authorized as the hook's `principal_id`.

- **At-least-once.** Exponential backoff between attempts; `dead` after
  `max_attempts`. With no third party on the path, retries only cover
  transient local failures; the durable failure modes — target workflow
  deleted, principal disabled or lacking execute permission — dead-letter
  with the error recorded. Target workflows should be idempotent per
  `event_key`, the discipline the replay model already demands.
- **Redaction before hand-off.** Envelope payloads pass through
  `flux/security/redaction.py` when the envelope is built; `sensitive` task
  values are already `[REDACTED]` at storage and never reach the envelope.
- **Hop guard.** The envelope carries a hop count; an execution started by a
  hook stamps `hop + 1` onto its own events' envelopes. Past the cap
  (default 3) the delivery goes straight to `dead` with a loop error.
  Without this, `execution:*:*:completed` targeting any workflow is a fork
  bomb.
- **No cross-delivery ordering.** Per-execution ordering (serializing the
  drain by `execution_id`) is a possible later addition; promising it in v1
  constrains the drain for a property most receivers do not need.

## Envelope

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
    "value": { "…": "redacted event value" },
    "occurred_at": "2026-08-14T12:00:00Z"
  }
}
```

The target workflow receives the envelope as its input
(`ctx: ExecutionContext[dict]`). A notification workflow shapes the Slack
payload from it in plain Python — no template language exists in this
design; body shaping happens in task code with full expressiveness and no
server-side interpolation surface. A small library of notification tasks
(e.g. a Slack webhook task with `secret_requests` for the token) can ship
under `flux/tasks/` or `examples/` for ergonomics, but is not part of the
entity.

## Declaration paths

All three registration paths exist; each is permission-gated so none is a
privilege-escalation side door.

**1. Server-side CRUD** (the primary path): REST + CLI, admin/operator
owned. `owner_type="user"`. This is the only path that may register
selectors wider than the declarer's own scope.

**2. Workflow-declared:**

```python
@workflow.with_options(
    hooks=[
        hook.run(
            on="task:release:*:promote_prod:awaiting_approval",
            workflow="ops/notify_slack",
        ),
    ],
)
```

Registered as rows at workflow registration, `owner_type="workflow"`,
`owner_ref="ns/name"` — replaced on re-registration and deleted with the
workflow, the `<workflow>_auto` schedule lifecycle. Two constraints:

- **Scope confinement:** declared selectors must match only the declaring
  workflow (`execution:<own-ns>:<own-name>:…` /
  `task:<own-ns>:<own-name>:…`), validated at registration. A workflow may
  observe itself; observing the fleet requires an operator and path 1.
- **Permission escalation:** a registration payload containing hooks
  requires the registrant to hold hook-create permission in addition to
  `workflow:*:register` — the `requires_code_upload_permission` pattern from
  agent definitions. A hook feeds event data into another workflow under a
  stored principal; `workflow:register` alone must not mint one.

**3. Agent-declared:** `AgentDefinition` gains `hooks: list`. Same
escalation rule (the definition already computes escalation via
`payload_ships_code`; hooks join that check), same replace-on-update
lifecycle, `owner_type="agent"`. Scope confinement differs: every agent
session runs `agents/agent_chat`, so namespace/workflow segments cannot
discriminate between agents. Agent-owned hooks therefore carry an implicit
server-side filter on the owning agent's sessions — the envelope includes
the agent name for these executions, and the matcher applies the owner
filter before the selector. An agent observes its own sessions only.

## Permissions

House grammar: `hook:<name>:<verb>` with verbs `create`, `read`, `update`,
`delete`, plus `hook:deliveries:read` and `hook:deliveries:retry` for the
ops surface. Built-in roles: `admin` (via `*`), `operator` gets full hook
management; `viewer` gets `hook:*:read` + `hook:deliveries:read`; `worker`
gets nothing.

Because the only action is running a workflow, target authorization rides
on the existing RBAC rather than a new trust model: at create/update the
hook's `principal_id` must hold execute permission on `workflow_ref`, and
the same check applies at fire time — a later permission revocation
dead-letters deliveries instead of silently bypassing it. Creating a hook
still observes events and acts under a stored principal, so `hook:*:create`
sits above `workflow:*:register` in the hierarchy.

## API and CLI

```
POST   /hooks                    hook:*:create
GET    /hooks                    hook:*:read
GET    /hooks/{name}             hook:<name>:read
PUT    /hooks/{name}             hook:<name>:update
DELETE /hooks/{name}             hook:<name>:delete
POST   /hooks/{name}/test        hook:<name>:update   # fire a synthetic event
GET    /hooks/{name}/deliveries  hook:deliveries:read
POST   /hooks/{name}/deliveries/{id}/retry   hook:deliveries:retry
```

Routes live in `flux/api/hook_routes.py` as a `HookRoutesMixin`, composed by
`flux/server.py` like every other domain. CLI: `flux hook
create|list|get|update|delete|test|deliveries|retry`.

`POST /hooks/{name}/test` starts the target workflow with a synthetic
envelope — a misconfigured target or principal surfaces before an incident
does, not during one, and the response returns the started `execution_id`
for inspection.

## Testing

- Matcher: selector ↔ event tables, both domains, wildcard positions —
  pure-function tests against `_wildcard_match` semantics.
- Outbox transactionality: a rolled-back state write leaves no delivery row;
  a committed one leaves exactly one (SQLite and `postgresql`-marked).
- Drain: backoff schedule, dead-letter after `max_attempts` and on
  missing-target / revoked-principal, hop-guard cutoff, redaction applied
  to envelopes, `execution_id` recorded on delivered rows.
- Declaration paths: scope confinement rejected loudly; registration without
  hook-create permission rejected; principal lacking execute on the target
  rejected at create; owner lifecycle (replace on re-register, cascade on
  delete).
- E2E: a workflow with an approval gate + a hook targeting a recorder
  workflow; assert the awaiting-approval event starts it with the envelope
  as input, and that the hop guard stops a self-targeting hook.

## Out of scope

- Inbound hooks — ingress remains resume-with-input, approval decisions, and
  services.
- A direct `webhook` action and any body-templating language — external
  calls are the target workflow's job; the `action` enum leaves room if a
  no-execution path ever proves necessary.
- Per-execution delivery ordering.
- An "message agent session" action (post-v1, once the console's session
  surface settles).
- Console panels for hooks/deliveries — the deliveries API is designed to
  feed one later.
