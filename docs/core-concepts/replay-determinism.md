# Replay & Determinism

Flux gives workflows durability by **event sourcing**: every state
transition — task started, task completed, workflow paused — is persisted as
an `ExecutionEvent`. When an execution resumes (after a pause, an approval,
a worker crash, or an explicit `resume`), Flux does not jump to the middle
of your function. It **re-executes the workflow body from the top** and
replays the event log: every `@task` call whose result is already in the log
returns that stored result instantly instead of running its body again.

This model is what makes pause/resume, crash recovery, and cross-worker
re-dispatch work. It also comes with a contract your workflow code must
honor.

## The contract

**Everything in the workflow body outside of `@task` calls runs again on
every replay — and must reach the same task calls, in the same order, with
the same arguments.**

Task results are stable across replays because they come from the log. The
code *between* task calls is your responsibility:

```python
@workflow
async def report(ctx: ExecutionContext[str]):
    data = await fetch(ctx.input)     # ✅ replayed from the log
    rows = [r for r in data if r]     # ✅ pure transformation — same result every replay
    summary = await summarize(rows)   # ✅ same arguments -> same task identity
    return summary
```

### What breaks replay

Anything non-deterministic in the workflow body itself:

```python
@workflow
async def fragile(ctx: ExecutionContext):
    # ❌ wall clock: different value every replay — branches can flip
    if datetime.now().hour < 12:
        await morning_job()

    # ❌ randomness: different arguments -> different task identity
    await process(random.choice(items))

    # ❌ non-task I/O: runs again on every replay, results differ,
    #    side effects happen twice
    response = httpx.get("https://api.example.com/rates")
    await settle(response.json())

    # ❌ environment-dependent branching: a resumed execution may run on
    #    a different worker with different env/filesystem
    if os.path.exists("/tmp/flag"):
        await cleanup()
```

The failure mode is subtle: nothing errors at the point of divergence.
Instead, a replay takes a different branch or calls a task with different
arguments, task identities stop lining up with the log, and previously
completed work re-runs (or work that never ran is skipped as if finished).

### How to do it right

Move every non-deterministic read **into a task**. Task results are recorded
on first execution and replayed afterwards, which pins the value forever:

```python
@workflow
async def robust(ctx: ExecutionContext):
    started_at = await now()                  # built-in task: pinned on first run
    roll = await choice(["a", "b"])           # built-in task: pinned
    rates = await fetch_rates()               # your I/O wrapped in @task: pinned

    if started_at.hour < 12:                  # ✅ deterministic — replays see the
        await morning_job()                   #    same pinned value
```

Flux ships deterministic-friendly built-ins in `flux.tasks`: `now()`,
`uuid4()`, `randint(...)`, `choice(...)`, plus `sleep(...)` that is
replay-aware. Use them instead of the standard-library equivalents inside
workflow bodies.

Rules of thumb:

- **Wall clock, randomness, UUIDs** → use the built-in tasks (or your own
  `@task`).
- **Network calls, file reads, database queries** → wrap in a `@task`.
- **Pure computation on task results** (parsing, filtering, arithmetic,
  f-strings) → fine inline.
- **Logging/printing** in the body is harmless to correctness but runs again
  on every replay — expect duplicate lines.
- **Mutable module-level state** shared across executions is not replayed
  and not isolated; don't branch on it.

## What Flux guarantees underneath

The runtime's side of the contract, so you know what you can rely on:

- **Task identity is content-addressed.** A call's identity hashes the task
  name and arguments, plus a per-call occurrence counter — so two identical
  calls (`await record(1)` twice) are distinct calls, each replaying its own
  logged result in order.
- **Each retry attempt is tracked.** A workflow that pauses mid-retry
  resumes into the correct attempt rather than restarting the sequence.
- **Failure paths are logged like success paths.** Fallback and rollback
  results are events too, and replay honors them.
- **`cache=True` is separate from replay.** Task caching memoizes across
  executions by argument-derived key; replay memoizes within one execution's
  log. Caching being opt-in never weakens the replay guarantees above.

## Checking your workflow

A quick self-audit for any workflow that will pause, wait for approvals, or
run on a fleet:

1. Search the body for `datetime.`, `time.`, `random.`, `uuid`, `os.environ`,
   `open(`, `httpx.`/`requests.` outside of task definitions.
2. Ask of every `if`/`for` in the body: *would this branch identically if
   re-run tomorrow on a different machine, given the same task results?*
3. Exercise it: run the workflow with a `pause(...)` inserted mid-way, resume
   it, and verify tasks before the pause did not re-run (their side effects
   appear once) while the final output is what a straight run produces.
