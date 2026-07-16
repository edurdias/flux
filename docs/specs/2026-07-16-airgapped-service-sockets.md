# Spec: airgapped service sockets — warm runtimes for sealed executions

**Date:** 2026-07-16 · **Status:** draft for review (follow-up to #130/#133)
· **Motivating use case:** long-lived local runtimes whose state takes
minutes to load, consumed from sealed, single-use executions.

## Motivation

A `docker-airgapped` execution container lives exactly as long as one
execution. Any runtime whose startup is expensive — state loaded into
(GPU) memory measured in minutes — pays that cost *per execution* if it
runs inside the container. The state must outlive executions, so it must
live outside the sandbox.

The design that preserves every current guarantee: the runtime runs as a
**long-lived, operator-managed sidecar** on the worker host, and sealed
executions reach it over a **Unix domain socket** mounted into the
container. No network stack is involved anywhere — `--network=none`
stays literally true — and the wire is point-to-point by construction:
no lateral channel between executions, no egress, nothing reachable
from outside the host.

With the heavy state out of the container, the sealed image goes back to
being slim and the measured cold start is ~1–1.5 s per execution
(container create ~0.2–0.5 s + `flux.runners.child` import ~0.6 s,
measured; no GPU hook — the sidecar owns the device).

### Alternatives considered

- **Warm container pools** — rejected: at ~1 s cold start they buy back
  sub-second docker overhead at the cost of pool lifecycle, idle-health,
  and refill machinery. If a future latency-sensitive path cares, the
  cheaper lever is trimming the child's import graph.
- **Worker-brokered `service_call` frames** — deferred, complementary:
  per-call allowlisting/audit/rate-limiting through the stdio protocol,
  at the cost of a new frame family and double serialization. Sockets
  win on protocol fidelity and latency; the broker can be layered on
  later where auditability matters more.
- **Internal docker networks** (shared or per-execution) — parked: a
  shared net creates lateral channels between untrusted executions; a
  per-execution net avoids that but reintroduces a network stack and
  churn. Only needed for TCP-only runtimes; both major local runtimes
  serve UDS natively (`llama-server --host <path>.sock`, vLLM `--uds`).
- **Long-lived pod containers** (state inside a reused container) —
  parked: executions sharing a container share its filesystem and
  process state, which is a different guarantee and therefore a
  different runner name, and a much larger protocol change
  (exec-based dispatch).

## Config

```toml
[flux.workers]
runners = ["docker-airgapped"]
airgapped_service_sockets = { inference = "/run/flux-services/inference" }
```

- Key = service name: `[a-z0-9]([a-z0-9-]*[a-z0-9])?`, ≤ 32 chars (it
  becomes a worker label; see Dispatch).
- Value = **host directory** that contains (or will contain) the
  service's socket at `<dir>/service.sock`.

### Why a directory, and the permission contract

Mounting the socket *file* pins its inode: a sidecar restart re-creates
the socket and running mounts silently point at the dead one. Mounting
the *directory* is restart-safe — but a shared writable directory would
be a dead-drop channel between untrusted executions. The contract closes
that:

- Host directory: **no write bits** (`0555` or stricter). Socket: mode
  `0666` (`service.sock`). Ownership is deliberately *not* part of the
  enforced contract — the guarantee rests on mode bits alone, which is
  what keeps non-root worker deployments and non-root sidecar recipes
  viable.
- The mount is emitted **rw** — connecting to a UDS requires write
  permission on the socket inode, and a read-only mount fails the
  connect with `EROFS`. This is a deliberate, structural exception to
  the "mounts are always read-only" rule (#133), confined to the
  service directories.
- The write-less directory is binding **even for container root**: the
  profile's `--cap-drop=ALL` removes `CAP_DAC_OVERRIDE`, which is what
  lets uid 0 ignore mode bits. Executions can connect to sockets; they
  cannot create, replace, or delete files in the directory. (Running
  `--user` non-root remains recommended defense in depth. Host-side
  root keeps the capability, so a root-managed sidecar can still
  create/replace its socket across restarts.)

The worker validates at startup (fail fast, same philosophy as the
docker probe): service-name validity, no duplicate directories, and the
directory exists with no write bits — created if absent, with creation
failures re-raised against the specific config entry. Socket problems
are *warnings*, not errors, because the sidecar may start after the
worker: missing socket, path that is not a unix socket, or a socket
that is not world-connectable each log a pointed warning.

### Mount emission

`AirgappedDockerRunner._locked_args()` emits, per service:

```
--mount type=bind,source=/run/flux-services/inference,target=/run/flux/services/inference
```

Fixed in-container prefix `/run/flux/services/<name>`. The raw mount
flags stay vetoed in `airgapped_extra_args` — the named key is the only
grant path, so `flux.toml` remains the complete audit trail.

### Knob vs. new runner name — decision for review

"No network" stays literally true and the grant is enumerable config —
knob-shaped. But "every effect passes through the permission-checked
worker" stops being strictly true — name-shaped. **Recommendation:**
ship as a knob on `docker-airgapped`, advertise granted services in the
worker registration (labels below), and revisit a distinct
`docker-enclave` name only if a mediated/unmediated distinction ever
needs to be dispatch-visible on its own. Flagged here because it is a
judgment call about what the runner name promises.

## Dispatch

Workers derive one label per granted service at registration:
`flux.service.<name> = "true"`. Workflows that need a service target it
with the existing affinity mechanism — no new matching machinery:

```python
@workflow.with_options(
    runner="docker-airgapped",
    affinity={"flux.service.inference": "true"},
)
async def sealed_generate(ctx: ExecutionContext[str]): ...
```

The `flux.` label prefix is already reserved (worker metrics, #120), so
user labels cannot spoof service grants. Dynamic workflows can have
service affinities stamped from declared needs later; v1 leaves that to
the author's `with_options`.

## Child-side surface

The runner passes the mounted map to the child through its sanitized
env (parent-set): `FLUX_SERVICE_SOCKETS={"inference":
"/run/flux/services/inference/service.sock"}`.

New helpers in `flux/tasks` (usable in any runner — inline and
subprocess executions simply see no services unless the env is set):

```python
from flux.tasks import service_client, service_socket

@task
async def generate(prompt: str) -> str:
    async with service_client("inference") as client:      # httpx over UDS
        r = await client.post("/v1/completions", json={"prompt": prompt, ...})
        return r.json()["choices"][0]["text"]
```

- `service_socket(name) -> str` — the socket path; raises a clear
  `ExecutionError` naming the service and the affinity hint when absent.
- `service_client(name, **httpx_kwargs) -> httpx.AsyncClient` —
  preconfigured UDS transport, base_url `http://flux-service` (host is
  ignored over UDS). OpenAI-compatible SDKs work by passing this client
  as their http client.

Semantics that come free from existing machinery:

- **Replay:** service calls happen inside ordinary tasks; outputs are
  checkpointed, resume short-circuits, the sidecar is never
  re-contacted for completed calls.
- **Failures:** a down sidecar is `ECONNREFUSED` → a normal task error →
  retry/fallback/rollback chain.
- **Streaming:** native HTTP/SSE over the socket (no frames, kernel
  flow control); workflow code tees tokens into `progress()` for live
  visibility, exactly as the agent loop does.
- **Cancellation/timeouts:** SIGTERM into the container ends client
  connections; the execution ceiling bounds total time.

## Security analysis (delta from today)

| Property | Today | With service sockets |
|---|---|---|
| Network stack | none | none |
| Lateral channel between executions | none | none direct; the sidecar is the only shared point |
| Egress | impossible | impossible (sidecar is UDS-bound, no egress of its own) |
| Worker mediates every effect | yes | **no** — socket traffic bypasses the broker |

Documented operator caveats:

- **Unmediated channel:** no per-call allowlist/audit/rate-limit; the
  worker never sees payloads (an observability loss and a privacy
  gain). The grant granularity is the service, not the request.
- **Sidecar-side DoS:** nothing throttles an execution hammering the
  socket except the sidecar's own limits and the container's cpu/pids
  caps.
- **Shared-runtime state:** all executions multiplex into one runtime;
  cross-request state (caches) is a potential side channel — identical
  exposure to a brokered design, noted for completeness.
- The sidecar receives **data, not code**, from processes with no other
  capabilities.

## Sidecar recipes (docs)

DOCKER.md gains a section with systemd/compose examples:

```bash
llama-server -m model.gguf --host /run/flux-services/inference/service.sock
# or
vllm serve <model> --uds /run/flux-services/inference/service.sock
```

plus the permission contract, and the note that the sidecar should be
its own hardened unit (no egress, dedicated user, GPU pinned) — it is
trusted infrastructure, like a database.

## Wiring

- `flux/config.py` — `airgapped_service_sockets: dict[str, str]`.
- `flux/runners/docker.py` — validation (names, directory contract) +
  `_locked_args()` emission + child-env injection.
- `flux/worker.py` / registration — derive `flux.service.*` labels.
- `flux/tasks/service_socket.py` — `service_socket` / `service_client`;
  exported from `flux.tasks`.
- Docs: `docs/advanced-features/airgapped-execution.md` section +
  DOCKER.md recipes; example in `examples/airgapped.py` with graceful
  inline fallback.

No server or protocol changes.

## Testing

- Name validation (shape, length, duplicates) and directory-contract
  validation (missing dir, wrong owner/mode) fail worker startup with
  pointed messages; missing socket only warns.
- `_locked_args()` emits one rw `--mount` per service at the fixed
  in-container prefix; nothing emitted when the map is empty; raw mount
  flags in `extra_args` still rejected.
- Registration carries `flux.service.<name>` labels; user labels under
  `flux.` are still rejected/stripped.
- `service_socket`/`service_client`: env parsing; absent-service error
  names the affinity hint; round-trip against a real UDS server
  (asyncio `start_unix_server` / uvicorn UDS fixture), including a
  streamed response.
- Example test: inline run with graceful fallback when no service env.

## Rollout / compatibility

Additive: empty map (default) changes nothing. Version: 0.61.0.
Future work, explicitly out of scope: worker-managed sidecar lifecycle
(command/restart/health in `flux.toml`), brokered `service_call` frames
for audited access, per-execution internal networks for TCP-only
runtimes.
