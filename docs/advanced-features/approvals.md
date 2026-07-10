# Human approvals on tasks

Flux tasks can pause for explicit human approval before running. Approval is
declared on the task itself, runs through the engine uniformly, and is
visible to CLI/API/UI clients alike.

## Quick start

```python
from flux import task, workflow, ExecutionContext


@task.with_options(requires_approval=True)
async def deploy_to_prod() -> None:
    ...


@workflow
async def release(ctx: ExecutionContext):
    await deploy_to_prod()  # workflow pauses here until approved
```

Run the workflow; check pending approvals; approve:

```bash
flux execution approvals
flux execution approve <execution_id> <task_call_id> --reason "tests green"
```

## Conditional approval

Pass a callable that takes the same arguments as the task and returns `bool`
(or an awaitable bool) to gate dynamically:

```python
@task.with_options(
    requires_approval=lambda amount, customer: amount > 100,
)
async def issue_refund(amount: float, customer: str) -> None:
    ...
```

The predicate is evaluated **before** the task body runs. If it raises, the
task call fails — predicate exceptions are not silently mapped to "approve"
or "reject."

## Lifecycle

When a task with an active approval gate is called:

1. Engine emits `TASK_AWAITING_APPROVAL` and inserts an approval row.
2. Workflow pauses (same machinery as `pause()`); worker releases the slot.
3. An approver hits `POST /executions/{id}/approvals/{task_call_id}/approve`
   (via CLI, web UI, or the agent harness).
4. Workflow resumes; engine reads the verdict and either runs the body or
   raises `ApprovalRejected` at the call site.

`ApprovalRejected` propagates as a normal exception. **Retry, fallback, and
rollback chains are skipped on rejection** — the body never ran, so there is
nothing to retry, fall back from, or undo.

## Permissions

A new task-level verb: `workflow:{ns}:{wf}:task:{name}:approve`. Operators
get this by default (`workflow:*:*:task:*:approve`).

A user with `workflow:{ns}:{wf}:read` can *see* a pending approval but
cannot decide it without `task:*:approve`.

## Replay & determinism

The approval row is the durable record that the gate was triggered. On
replay or worker reclaim the engine looks up the row by `task_id` *before*
evaluating the predicate; if a row exists the gate is honored without
re-running the predicate, so non-deterministic predicates (time, random,
external state) cannot flip the verdict between runs. The approver's
verdict lives on the same row, so it isn't re-derived either.

The predicate only runs on the very first call, when no row exists yet.
A worker crash between predicate evaluation and the next event flush is
the one window where the predicate can re-run on reclaim — identical to
the restart-race contract for task bodies.

Approval rejection emits a `TASK_FAILED` event with the `ApprovalRejected`
exception persisted via the task's configured output storage. On replay,
the event log short-circuits the task call and re-raises the same
exception, so the workflow body sees identical behavior on the original
run and on every replay.

## Standing approvals (`--always`)

By default an approval covers exactly one task call. When the same gated
task runs many times in one execution — an agent's `shell` tool, a deploy
step in a loop, retry attempts — approving with a **standing grant** covers
every later gate on the same task *name* within that execution:

```bash
flux execution approve <exec_id> <task_call_id> --always
```

Semantics:

- The decided row is stored with `scope="execution"` (a plain approval is
  `scope="call"`); the scope is visible in `flux execution approvals` output.
- When a later gate on the same task name registers, the engine finds the
  grant and auto-approves **without pausing** the workflow. Each auto-approval
  materializes its own `approved` row — approver copied from the grant,
  `reason="standing grant"` — so the audit trail still shows one row per
  gated call.
- The grant matches on task **name**, not call id, so it also covers retry
  attempts of gated tasks (their call ids differ, the name doesn't).
- Grants never cross executions, and a grant cannot be created from a
  rejection (`scope="execution"` with reject is a validation error).
- Rejection remains per-call; there is no standing reject.
- **No revocation (v1).** A grant lasts for the rest of the execution. If
  approvals must stay per-call, don't use `--always`.

## Retries and approval

Each retry attempt is a fresh task call: the predicate re-evaluates and a
new approval is required. Reasoning: the previous attempt may have had
partial side effects, and the approver should reconsider. "Approve once,
run forever" is a footgun — which is why covering later gates requires the
explicit `--always` standing grant described above.

## Cancellation

If a workflow is cancelled while paused on approval, all pending approval
rows for that execution transition to `cancelled`. They do *not* emit
`TASK_REJECTED` — rejection implies an approver acted; cancellation does
not. Cancellation handling at the workflow level takes over from there.

## Limitations (v1)

- **Parallel approval-gated calls in a single execution** are not supported —
  `asyncio.gather(approve_a(), approve_b())` where both gate will only
  surface the first approval. Lifted by a follow-up spec on parallel-pause
  coordination.
- **No timeouts.** Tasks pause forever until acted on. Set a task
  `timeout` (`@task.with_options(timeout=...)`) if a deadline matters.
- **Single approver.** No N-of-M policies; no role-scoped approver lists.

## CLI

```bash
flux execution approvals                                # list pending
flux execution approvals --status all --age 1h          # all at least 1h old
flux execution approve <exec_id> <task_call_id> --reason "..."
flux execution approve <exec_id> <task_call_id> --always   # standing grant
flux execution reject  <exec_id> <task_call_id> --reason "..."
flux execution show    <exec_id>                        # appends pending approvals on stderr
flux workflow status   <wf> <exec_id>                   # appends 'Blocked on N' on stderr
```

The `Pending approvals:` block in `execution show` and the
`Blocked on N approval(s)` line in `workflow status` are written to **stderr**,
so callers piping stdout to `json.loads` continue to work unchanged.

## HTTP API

```
GET  /approvals[?status=&execution_id=&workflow_namespace=&workflow_name=&task_name=&age_min=&limit=&offset=]
GET  /executions/{execution_id}/approvals
GET  /executions/{execution_id}/approvals/{task_call_id}
POST /executions/{execution_id}/approvals/{task_call_id}/approve
POST /executions/{execution_id}/approvals/{task_call_id}/reject
```

POST body: `{"reason": "optional", "always": false}` — `always: true` on
approve creates a standing grant (ignored on reject). `200` returns the
post-decision row (including its `scope`);
`409` returns `{"error": "already_decided", "current_status": ..., "decided_at": ...}`
without leaking the winning approver's identity.

## Agent harness

When an agent's tool is gated, the harness pauses with the engine-level
approval payload and surfaces it through the same UI channel as elicitations:

- **Terminal mode:** prints the task name and pauses on
  `[a] approve  [A] always  [r] reject`; the keypress resolves the decision
  (`A` issues a standing grant, so the same tool won't prompt again this
  session).
- **Textual mode:** mounts a system message with the same `[a]/[A]/[r]`
  hint in the status bar; the keypress resolves the pending approval.
- **API mode:** the SSE stream emits an `approval_required` event; the
  consumer posts the decision to `POST /approval/{task_call_id}?session=...`
  on the agent API, which decides the approval against the Flux server and
  resumes the event stream with the events produced after the workflow
  continues.
- **Web mode:** the bundled web client renders an inline approve/reject
  prompt (with an optional reason) when a gated tool pauses the workflow,
  posts the decision, and resumes the chat in place.

Setting `approval_mode="autonomous"` on `agent(...)` runs each tool in the
batch as a non-gated `with_options(requires_approval=False)` variant, so the
engine-level approval gate is skipped for those tool invocations.

## Migration from the old wrapper

If your code uses `flux.tasks.ai.approval.requires_approval` (removed in
v0.36.0), migrate to per-task `with_options`:

```diff
-from flux.tasks.ai.approval import requires_approval
-tools = requires_approval(system_tools("."), only=["shell"])
+tools_raw = system_tools(".")
+tools = [
+    t.with_options(requires_approval=True)
+    if (t.func.__name__ if hasattr(t, "func") else t.__name__) == "shell"
+    else t
+    for t in tools_raw
+]
```

The agent harness's tool-approval prompt still works the same way — only
the underlying mechanism changed.
