# Outbound hooks

Design for outbound hooks as a first-class entity: named subscriptions that
call an external API (Slack, a GMUD system, PagerDuty) or start another
workflow when engine events matching a selector occur. Outbound only —
ingress is already covered by resume-with-input, approval decisions, and
workflow services, so no inbound hook entity exists.

Immediate consumers:

- `approval_routing="notify"` (issue #144) — its pending delivery mechanism
  becomes "whatever hooks match the approval event"; the agent loop stays
  transport-agnostic.
- Chat/ops notification for approval gates (deploy-promotion GMUD flows).
- Event-driven composition — "on any `release/*` failure, run
  `release/incident_report`" as a stored row instead of code in every
  workflow.

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
  action           "webhook" | "run_workflow"
  url              webhook only
  workflow_ref     run_workflow only ("ns/name")
  body_template    optional, webhook only (see Templates)
  auth             "hmac" | "bearer" (webhook only, default "hmac")
  secret_ref       name of a config-store entry (encrypted at rest)
  principal_id     run_workflow executes as this principal
  owner_type       "user" | "workflow" | "agent"
  owner_ref        registering principal / "ns/name" / agent name
  max_attempts, created_by, created_at, updated_at

hook_deliveries
  id, hook_id, event_key, payload, attempts
  status           "pending" | "delivered" | "dead"
  next_attempt_at, last_error, created_at, delivered_at
```

Secrets never live on the row: `secret_ref` points into the existing
encrypted config store. `auth="hmac"` signs the body
(`X-Flux-Signature: sha256=<hmac>`); `auth="bearer"` sends the referenced
secret as `Authorization: Bearer …`. Templates cannot address secrets — auth
is a typed field, not an interpolation.

## Firing: transactional outbox

Delivery must never sit in the checkpoint write path — coupling persistence
to a third party's uptime is how executions wedge. The enqueue is
transactional with the event it reports:

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

- **At-least-once.** Exponential backoff between attempts; `dead` after
  `max_attempts`. Receivers must be idempotent — the discipline the replay
  model already demands everywhere. `event_key` in the envelope is the dedup
  handle.
- **Redaction before egress.** Envelope payloads pass through
  `flux/security/redaction.py`; `sensitive` task values are already
  `[REDACTED]` at storage and never reach the wire.
- **Hop guard.** The envelope carries a hop count; an execution started by a
  `run_workflow` hook stamps `hop + 1` onto its own events' envelopes. Past
  the cap (default 3) the delivery goes straight to `dead` with a loop
  error. Without this, `execution:*:*:completed → run_workflow` is a fork
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

`run_workflow` actions receive the envelope as the child workflow's input.

## Templates

`body_template` shapes the webhook body for receivers with fixed schemas
(Slack's `{"text": …}`). It is **placeholder substitution, not a template
language**: `{{ dotted.path }}` resolved against the redacted envelope. No
expressions, no conditionals, no loops, no filters — a template engine
evaluating operator-supplied text server-side is an injection surface this
feature does not need.

Rules:

- A placeholder that is the *entire* JSON string value substitutes the raw
  value, preserving type: `"blocks": "{{ event.value }}"` embeds the object.
  A placeholder inside a longer string stringifies:
  `"text": "{{ event.workflow_name }} paused"`.
- Paths are validated against the envelope schema at hook create/update —
  unknown roots fail loudly, the `routing.py` registration-time precedent.
- A path that is schema-valid but null at fire time (e.g. `event.state` on a
  task event) renders JSON `null` (whole-string position) or the empty
  string (embedded position). Delivery proceeds; templates must not turn a
  fireable event into a dead letter.
- No template → the standard envelope is the body.

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
        hook.webhook(
            on="task:release:*:promote_prod:awaiting_approval",
            url="https://hooks.slack.com/…",
            template={"text": "{{ event.workflow_name }} awaits approval"},
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
  agent definitions. A hook is an egress channel; `workflow:register` alone
  must not mint one.

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
gets nothing. Creating a hook routes internal events to an arbitrary URL —
it is deliberately above `workflow:*:register` in the hierarchy.

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

`POST /hooks/{name}/test` renders the template against a synthetic envelope
and performs one real delivery attempt — template mistakes surface before an
incident does, not during one.

## Testing

- Matcher: selector ↔ event tables, both domains, wildcard positions —
  pure-function tests against `_wildcard_match` semantics.
- Outbox transactionality: a rolled-back state write leaves no delivery row;
  a committed one leaves exactly one (SQLite and `postgresql`-marked).
- Drain: backoff schedule, dead-letter after `max_attempts`, hop-guard
  cutoff, redaction applied to payloads.
- Templates: whole-string vs embedded substitution, creation-time path
  validation failures, null rendering.
- Declaration paths: scope confinement rejected loudly; registration without
  hook-create permission rejected; owner lifecycle (replace on
  re-register, cascade on delete).
- E2E: a workflow with an approval gate + a webhook hook against a local
  HTTP sink fixture; assert the awaiting-approval delivery arrives signed.

## Out of scope

- Inbound hooks — ingress remains resume-with-input, approval decisions, and
  services.
- Per-execution delivery ordering.
- An "message agent session" action (post-v1, once the console's session
  surface settles).
- Console panels for hooks/deliveries — the deliveries API is designed to
  feed one later.
