# Airgapped Execution

The `docker-airgapped` runner executes workflows you do not trust — above
all, model-authored (dynamic) workflows — and workflows over data that must
not leave the box. Each execution runs in its own container whose **only
capability channel is the stdio protocol to the parent worker**:
checkpoints, secrets, configs, and approval-gate operations all flow
through the worker, where every request is permission-checked. Everything
else is closed.

## The locked profile

| Surface | Enforcement |
|---|---|
| Network | `--network=none` — no egress, no DNS, no `pip install` |
| Filesystem | `--read-only` rootfs; size-capped tmpfs `/tmp` |
| Privileges | `--cap-drop=ALL`, `--security-opt=no-new-privileges` |
| Resources | memory / cpus / pids limits (required, never unlimited) |
| Time | wall-clock ceiling; on expiry the container is killed and the execution fails terminally for both durabilities |
| Credentials | none — the child holds no worker or fleet credentials |

The profile is emitted from code *after* any operator `extra_args`, so
docker's last-wins flag parsing keeps it authoritative; `extra_args` that
would re-open a closed surface are rejected at worker startup.

## Enabling the runner

```toml
[flux.workers]
runners = ["docker-airgapped"]
airgapped_image = "<registry>/flux:<version>"   # falls back to docker_image

airgapped_memory = "512m"          # required non-empty
airgapped_cpus = 1.0
airgapped_pids_limit = 256
airgapped_tmp_size = "64m"
airgapped_execution_timeout = 900  # seconds; 0 disables (discouraged)
```

The image contract is minimal: it must run `python -m flux.runners.child`,
i.e. have `flux-core` installed at a worker-compatible version. The
official image satisfies this when its tag matches the worker (see
DOCKER.md).

Pin workflows to the sealed runner with
`@workflow.with_options(runner="docker-airgapped")` — this also constrains
dispatch, so the workflow only reaches workers advertising the runner:

```python
@workflow.with_options(runner="docker-airgapped")
async def sealed_keyword_count(ctx: ExecutionContext[str]):
    ...
```

See `examples/airgapped.py` for runnable examples (sealed text processing
with read-only asset mounts and graceful fallback, and privacy-preserving
redaction of sensitive data).

## Capability knobs

Three capabilities can be granted. Each is grantable **only through its
named config key** — the raw flags (`--gpus`, `--shm-size`, and all mount
flags) are rejected in `airgapped_extra_args` — so a grep of `flux.toml`
for `airgapped_` is the complete audit trail of opened surfaces:

| Key | Grants | Why it's safe to grant |
|---|---|---|
| `airgapped_gpus` | `--gpus all` / `"device=0"` | a compute device; no data path out |
| `airgapped_mounts` | read-only bind mounts | an *input* channel — data can enter, results still leave only via the stdio protocol |
| `airgapped_shm_size` | `/dev/shm` sizing | RAM allocation (large inter-process buffers); accounted next to `airgapped_memory` |

`airgapped_mounts` entries are `"/host/path:/container/path"`. Read-only
is **forced by the runner** regardless of what the entry says; `rw`,
relative paths, missing host paths, and duplicate targets fail worker
startup. Mounted content is readable by every airgapped workflow on the
worker — mount reference datasets and static assets, never directories
containing secrets.

Capability channels are **never** knobs: network, DNS, published ports,
privileges, host namespaces, and writable mounts cannot be enabled on this
runner. Operators who need them switch to the plain `docker` runner —
changing the runner *name*, and therefore the guarantee that dispatch and
the dynamic-workflows server rely on.

## Sizing for data- and compute-heavy workloads

The profile's defaults are sized for untrusted glue code. Heavier sealed
workloads — numeric simulation, media processing, large dataset
transforms — raise the limits and grant what they need (image recipe,
sizing guidance, and cache environment variables for the read-only rootfs
are documented in DOCKER.md under "Sizing the airgapped runner for heavy
workloads"):

```toml
[flux.workers]
runners = ["docker-airgapped"]
airgapped_image = "my-registry/flux-compute:0.60.0"
airgapped_gpus = "all"                        # GPU-accelerated compute
airgapped_mounts = ["/srv/datasets:/data"]    # reference data, read-only
airgapped_shm_size = "8g"                     # large inter-process buffers
airgapped_memory = "32g"
airgapped_tmp_size = "8g"
airgapped_execution_timeout = 3600
```

`--network=none` keeps the container's own loopback, so a workflow can
spawn helper processes and talk to them on `127.0.0.1` *inside* the
sandbox — nothing is reachable from outside.

## Dynamic workflows run here by default

Workflows registered by agents through the dynamic-registration endpoint
get `runner="docker-airgapped"` stamped **server-side**
(`[flux.dynamic_workflows] require_runner`), overriding anything the
authored source declares. The author is the adversary in that model; the
sealed runner is what makes accepting model-authored code tenable. Relax
`require_runner` only in development.

## Failure semantics

- **Crash** (OOM kill, segfault, hard exit): durable executions are
  released for re-dispatch and deterministic replay resumes from the last
  checkpoint; transient executions fail terminally.
- **Wall-clock timeout** (`ExecutionTimedOut`): terminal FAILED for *both*
  durabilities — a deterministic timeout would re-dispatch forever, so it
  is a policy violation, not a crash.
- **Cancellation**: SIGTERM is forwarded into the container
  (`docker run` sig-proxying), with a grace period before the container is
  killed.
