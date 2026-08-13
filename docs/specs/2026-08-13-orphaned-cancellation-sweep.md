# Orphaned-cancellation sweep

**Issue:** #225 · **Status:** implemented

## Problem

The cancel route writes `CANCELLING` for any unfinished execution and relies on
delivery to a worker for resolution: `next_cancellations_batch` matches rows by
`worker_name` against *currently connected* workers. Two orphan classes never
resolve:

1. **`worker_name IS NULL`** — cancellation requested while the execution was
   parked (`CREATED`, never dispatched). No worker ever owned it; NULL matches
   no connected worker; dispatch skips non-`CREATED` rows, so nobody ever
   claims it either. The row is stranded the moment it is written. A
   `mode=sync` cancel of a parked execution hangs the HTTP request
   indefinitely (`while not ctx.has_finished` re-polls forever).
2. **`worker_name` names a worker that never returns** — eviction deliberately
   leaves `CANCELLING` rows untouched (`unclaim` recovers only
   initial/resume states), so the row keeps pointing at the dead worker and
   re-delivery targets nobody.

#222 fixed every variant where a live worker receives the delivery. This is
the remaining hole: rows whose delivery target is gone.

## Design

A sweep in the scheduler tick, alongside the park-TTL and pause-wake passes,
under the same cross-replica dispatch lock:

- **NULL-owner rows resolve immediately.** Nothing was ever asked to resolve
  them, so there is nobody to defer to. Any dispatched row has `worker_name`
  stamped at dispatch time, so NULL cannot be a claim in flight (#222's
  deferral window applies only to named rows).
- **Named rows resolve only when the worker is not connected AND the row has
  been `CANCELLING` longer than a grace period** (`[flux.workers]
  cancellation_orphan_grace`, default 300s, 0 disables the sweep for named
  rows). Connected workers keep the existing re-delivery path — they resolve
  within a dispatch cycle. The grace covers reconnect backoff: a
  disconnected-but-alive worker that returns inside it still resolves its own
  row, which is preferable because it actually interrupts the running body
  rather than abandoning it.
- **Age is measured from the newest `WORKFLOW_CANCELLING` event**, which is
  written in the same transaction as the `CANCELLING` state — no schema
  change, no clock skew beyond what the event log already carries.

Resolution appends `WORKFLOW_CANCELLED` and writes terminal `CANCELLED` —
the operator asked for cancellation, so completing it is the correct outcome
(unlike the park sweep, which fails rows with a diagnostic). The write is
guarded the same way as the park sweep: `state == CANCELLING` filter plus
`with_for_update(skip_locked=True)`, so a worker checkpoint landing
concurrently wins the row lock race and the sweep skips; a sweep write landing
first makes the worker's late write a no-op via `_accept_state_write`.

## Non-goals

- Interrupting a partitioned-but-alive worker's running body. If it outlives
  the grace, its checkpoints are rejected after the sweep resolves; the body
  runs to waste but the row is correct. The alternative — waiting forever —
  is the bug this fixes.
- Reaping `CANCELLING` rows whose worker is connected. That path is owned by
  re-delivery (#222).

## Test surface

- Unit: NULL-owner resolved immediately; named+connected untouched;
  named+disconnected untouched inside grace, resolved past it; grace 0
  disables named sweeps but not NULL sweeps; terminal rows untouched;
  resolution appends `WORKFLOW_CANCELLED`.
- E2E: cancel a parked execution (affinity matches no worker) and observe it
  reach `CANCELLED` — red before this change (stuck `CANCELLING`).
