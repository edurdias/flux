# Decomposing `flux/server.py` (#264)

`Server` composes the route mixins, owns process lifecycle, and carries the
in-memory state the fleet and the scheduler run on. This is the map of what
is left in it, how tightly each part is bound to that state, and the order
the parts can safely come out.

## Where it actually stands

The issue describes a ~5,400-line file. The route extraction into
`flux/api/<domain>_routes.py` mixins already happened, so the starting point
for this work was **1,915 lines**, not 5,400. Worth stating plainly: the
number in the ticket predates that refactor.

## Coupling, measured

Each group below is scored by the number of `Server` attributes its methods
touch — the state that would have to be passed, shared, or moved with it —
and by how many call sites outside `server.py` reach for the same state.

| Group | Lines | `Server` attributes touched | External references | Status |
|---|---|---|---|---|
| Hook firing | 154 | **1** (`_get_db_session`) | 2 (`hook_routes.py`) | **extracted** → `flux/hooks/dispatch.py` |
| Scheduling | 462 | 6 | 0 | **extracted** → `flux/autoschedule.py`, `flux/scheduler_loop.py` |
| Execution & dispatch | 282 | 10 | 0 | after scheduling |
| Worker fleet & heartbeat | 336 | **23** | **51** (`worker_routes.py`) | last |

The order is that table sorted by cost. Hook firing came out first because
it barely touched the server at all; the worker fleet goes last because its
state *is* the server's state — sixteen of `Server.__init__`'s attributes
are worker bookkeeping, and `worker_routes.py` reads them directly fifty-one
times. Moving it is a rewrite of the hottest request path, and it deserves
its own change rather than riding along with easier ones.

## The shape extractions take

Not mixins. The route layer uses mixins, and for routes that is right: they
are registration functions that need the app and the server's collaborators.
But a mixin shares `self`, so it moves lines out of a file without moving
any coupling — which is the opposite of what this work is for. The eventual
engine-core boundary (a native extension, per the roadmap) can only sit on
seams whose inputs are explicit.

So each extraction becomes a module of functions (or a collaborator object)
whose dependencies arrive as parameters:

```python
# flux/hooks/dispatch.py
async def start_hook_execution(create_execution, session_factory, namespace, ...)
async def authorize_hook_principal(session_factory, principal, permission)
```

`Server` binds its own methods into those parameters at the call site
(`functools.partial`), so the coupling that remains is visible in a
signature instead of implied by `self`. The drain and the hook routes each
call the module directly; neither reaches into the server any more.

## What the first extraction changed

- `flux/hooks/dispatch.py` — new, 183 lines, no `Server` import.
- `flux/server.py` — 1,915 → 1,782 lines.
- `flux/api/hook_routes.py` — calls the module functions, passing
  `self._create_execution` and `self._get_db_session`.
- Tests that patched `Server._authorize_hook` now patch the function the
  routes call; the drain's wiring test asserts *which* function was bound
  rather than the identity of a bound method.

No behavior change: the full unit suite, the hook suites, the e2e suite and
the benchmark series are unchanged.

## What the second extraction changed

Scheduling came out in two pieces, because they are not equally coupled.

`flux/autoschedule.py` (124 lines) is the `<workflow>_auto` reconciliation.
It touched **no** server state at all -- registration calls it, and a
schedule declared on a workflow is a property of that workflow, not of the
process that received it. A free function, called directly by
`workflow_routes.py`.

`flux/scheduler_loop.py` (431 lines) is the tick: `SchedulerLoop`, a
collaborator owning the lifecycle state that used to sit on `Server`
(`task`, `running`, the join-token purge clock). Its dependencies arrive
through the constructor, which makes the remaining coupling countable:

- `create_execution` and `session_factory` -- the two capabilities a tick
  genuinely needs.
- `execution_events` and `worker_queues` -- **shared dictionaries, not
  copies**. The tick wakes an execution's waiters and hands resumed work to
  a worker's queue, writing into the same objects the request handlers
  read. That sharing is the honest remainder of this extraction, and it is
  what a future engine-core boundary would have to turn into a channel.
- the two hook callables, bound by the server.

Moving the tick out also surfaced something the old placement hid: it calls
three `ContextManager` sweeps (`fail_expired_parked`,
`resolve_orphaned_cancellations`, `fire_due_wakes`) that existed only on the
concrete class, never in the interface. They are declared on the ABC now --
what the tick needs from the store should be readable in the store's own
contract rather than discovered from an implementation.

## Getting to "wiring only"

The ticket's target is `server.py` under ~500 lines. Reaching it needs all
four groups out, and the last of them is the 51-call-site worker-fleet
rewrite. That is the honest remaining cost — the first extraction removed
the cheapest 8% of the file, and the arithmetic from here is:

| After extracting | `server.py` |
|---|---|
| hooks (done) | 1,782 |
| + scheduling (done) | **1,332** |
| + execution & dispatch | ~1,050 |
| + worker fleet | ~710 |

Even with all four out, roughly 700 lines remain: `__init__`'s state,
lifecycle (`start`, scheduler/reaper start and stop), auth helpers, and app
composition. Under 500 additionally requires moving the worker-fleet *state*
out of `Server.__init__` — which is the same change as the fleet extraction,
done properly rather than as a delegating shim.
