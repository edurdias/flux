# T5b — Multi-consumer semantics on one execution's progress stream

**Status:** measured (ci profile, SQLite; deterministic parts reproduce reliably)
**Test:** `tests/perf/test_t5b_multi_consumer.py`

## What was expected

From code reading (PLAN.md §0b): `Server._progress_buffers` holds a single
`asyncio.Queue` per execution. Two concurrent SSE consumers therefore
*compete* for frames. Additionally, the SSE generator's `finally` pops the
shared buffer (`flux/server.py:605`), so we predicted the surviving consumer
would be **starved from the moment** its sibling disconnected.

## What was measured

Two consumers on one execution streaming 50 frames/s:

- **Competition confirmed.** During the overlap window consumer A received
  180 frames and consumer B 124, with **zero duplicate sequence numbers** —
  each frame reached exactly one consumer, split nondeterministically. No
  client can rely on seeing a complete progress stream while any other
  client is subscribed to the same execution.
- **Immediate starvation falsified.** After A disconnected, B received
  245 of the next ~250 frames — effectively the full stream. The
  buffer-pop in the generator's `finally` evidently does not run at
  client-disconnect time; async-generator finalization is driven by the
  server framework's cleanup/GC timing, so *when* the shared buffer
  disappears after a disconnect is nondeterministic. The failure mode is
  therefore worse than predicted in one way (it can strike late, looking
  like a random mid-stream stall for the survivor) and milder in another
  (it often doesn't strike within seconds).

## Why it matters

The motivating workload is LLM token streaming with live viewers. Two
dashboards (or one dashboard plus one CLI `--mode stream`) attached to the
same execution will each see an arbitrary interleaved subset of tokens, and
after either detaches, the other's stream continues or stalls depending on
cleanup timing.

## Proposal (sized as one issue)

Replace the single shared queue with **per-consumer fan-out**:

- `_progress_buffers[execution_id]` becomes a small registry of per-consumer
  queues (or a single broadcast structure); ingest iterates and offers the
  frame to each, keeping the existing drop-newest cap per consumer.
- Consumer registration/unregistration happens explicitly in the SSE
  generator's setup/`finally`, removing only that consumer's queue — the
  cleanup-timing hazard disappears because no shared resource is popped.
- Ingest with zero registered consumers keeps today's discard behavior.

This preserves the ephemeral contract and the bounded-memory property
(N_consumers × 10,000 frames worst case) and makes multi-consumer behavior
deterministic: every consumer sees every frame that arrives while it is
subscribed. The T5b test's assertions are written to flip loudly when this
changes.

## Related shakedown observation (unconfirmed, separate)

During one T6a run, a cancel issued while 8 subprocess children were still
starting left executions unresolved past a 120 s wait (states not
re-inspected before teardown); the identical scenario with all children
demonstrably streaming cancels cleanly in ~4 s, repeatedly. If it recurs,
the cancel-during-claim race deserves its own investigation; T6a now pins
the cancel-at-full-rate scenario explicitly.
