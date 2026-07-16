# Spec: airgapped capability knobs — GPUs, read-only mounts, shared memory

**Date:** 2026-07-16 · **Status:** draft for review (follow-up to the
docker-airgapped runner, #130) · **Motivating use case:** data- and
compute-heavy workloads inside the airgapped sandbox.

## Motivation

The `docker-airgapped` runner exists so untrusted (often model-authored)
workflows can run with the stdio protocol as their *only* capability
channel. Sealed processing of sensitive data is a natural workload for
it — the whole point is that inputs and intermediate state never leave the
box — but today the profile can't express what heavier workloads need:

- **GPUs** work only by accident: `--gpus` is absent from
  `_AIRGAPPED_VETOED_FLAGS`, so it slips through `airgapped_extra_args`.
  Granting a capability by denylist omission is the wrong mechanism — it is
  invisible, unaudited, and would be silently revoked if the flag were ever
  added to the veto list.
- **Reference data** must be baked into the image: `--network=none` rules
  out runtime downloads and `--volume`/`--mount` are vetoed. Large
  datasets change more often than code; forcing an image rebuild per
  refresh pushes operators toward the unprotected `docker` runner — the
  opposite of what the profile is for.
- **Shared memory**: workloads that pass large buffers between processes
  go through `/dev/shm`, and Docker's default is 64 MB. `--shm-size` also
  slips through `extra_args` today — RAM allocation in disguise,
  unaccounted next to `airgapped_memory`.

## Design principle: named knobs, not veto opt-outs

The runner name is a dispatch contract — the dynamic-workflows server
stamps `require_runner="docker-airgapped"` and trusts any worker
advertising it. Two rules keep the name meaningful:

1. **The knob set is enumerated in code, not generic.** Each knob is a
   deliberate judgment that the isolation *output* guarantee survives it.
   Capability channels (network, DNS, publish, privileged, caps, host
   namespaces, writable mounts) are never knobs — operators who need those
   switch to the plain `docker` runner, changing the *name* and therefore
   the dispatched guarantee.
2. **Each capability is grantable only through its named config key.** The
   raw flags (`--gpus`, `--shm-size`, and the already-vetoed mount flags)
   are rejected in `airgapped_extra_args`, so a grep of `flux.toml` for
   `airgapped_` shows exactly which surfaces were opened. Config choice,
   not runtime surface: workflow authors (the adversary in this model)
   never touch worker config.

CPU/RAM/pids/tmpfs/timeout already follow this pattern
(`airgapped_memory`, `airgapped_cpus`, `airgapped_pids_limit`,
`airgapped_tmp_size`, `airgapped_execution_timeout`) — resizable, never
unset. This PR adds the three missing knobs.

## New config keys (`[flux.workers]`)

| Key | Type / default | Emitted as |
|---|---|---|
| `airgapped_gpus` | `str`, `""` (off) | `--gpus <value>` (e.g. `all`, `"device=0"`) |
| `airgapped_mounts` | `list[str]`, `[]` | `--mount type=bind,source=<src>,target=<dst>,readonly` per entry |
| `airgapped_shm_size` | `str`, `""` (docker default, 64m) | `--shm-size <value>` |

All three are emitted from `_locked_args()` — after `extra_args`, so
docker's last-wins parsing keeps the profile authoritative.

### `airgapped_mounts` semantics

- Entry format `"/host/path:/container/path"`. Both paths must be
  absolute. A trailing `:ro` is accepted (and redundant); any other
  option — `rw` above all — is rejected at worker startup.
- **Read-only is forced by the runner**, not trusted from config: the
  emitted `--mount` always carries `readonly`. A read-only bind is an
  *input* channel — data can enter the sandbox but still cannot leave
  except through the worker-mediated stdio protocol.
- Validation at worker startup (fail fast, same philosophy as the docker
  probe): absolute paths, host path exists, no duplicate targets, target
  is not `/`.
- Security note (documented): mounted content is readable by **every**
  airgapped workflow on that worker. Mount reference data and static
  assets; never mount directories containing secrets.

### `airgapped_gpus` semantics

- String passed through to `--gpus`. Empty = no GPU (today's effective
  default, now explicit).
- A GPU is a compute device, not an exfiltration path; it is the knob that
  makes GPU-accelerated sealed compute possible at all. Documented caveat:
  GPUs are a shared-hardware side channel in the strictest threat models —
  operators who care isolate at the worker level (one airgapped worker per
  GPU tenant).

### Veto list additions

`--gpus` and `--shm-size` join `_AIRGAPPED_VETOED_FLAGS`. This is the
counterpart of making them knobs: the named key becomes the *only* grant
path. (Both currently pass the veto — for `--gpus` that is exactly the
accident this spec removes.)

## Wiring

- `flux/config.py` — three new fields on the workers config.
- `flux/runners/docker.py` — constructor params + validation +
  `_locked_args()` emission; veto list additions.
- `flux/runners/__init__.py` — pass the new config through (same shape as
  the existing airgapped params).

No server, dispatch, or protocol changes: the runner name and its
guarantee semantics are unchanged.

## DOCKER.md: heavy-workload profile guidance

New section covering the sized-up setup end to end:

- Image recipe: `FROM flux:<version>` + the workload's libraries;
  reference data comes from `airgapped_mounts`, not the image.
- Sizing: raise `airgapped_memory` / `airgapped_cpus` /
  `airgapped_tmp_size` / `airgapped_execution_timeout`; set
  `airgapped_shm_size` for workloads that pass large buffers between
  processes. **tmpfs pages (`/tmp`, `/dev/shm`) count against the
  container's memory cgroup** — `airgapped_memory` must cover process +
  caches + shm.
- Read-only rootfs: point cache locations (`XDG_CACHE_HOME`, ...) at
  `/tmp` via `airgapped_extra_args = ["--env", ...]`.
- Helper processes: `--network=none` keeps the container's own loopback,
  so a workflow may spawn a helper server on `127.0.0.1` *inside* the
  container — nothing is reachable from outside, and results still flow
  only through the stdio protocol.

## Testing

- Veto: `airgapped_extra_args` containing `--gpus` / `--shm-size` /
  `--mount` (and `=`-joined forms) rejected at construction.
- Mounts: relative host or container path rejected; `rw` option rejected;
  trailing `:ro` accepted; duplicate targets rejected; missing host path
  rejected; emitted `--mount` string always contains `readonly`.
- `_locked_args()` contains `--gpus`/`--shm-size`/`--mount` exactly when
  configured, in the locked section (after extra_args in the assembled
  command).
- Config plumbing: runner built from `[flux.workers]` keys carries the
  values (mirrors existing airgapped construction tests).

## Rollout / compatibility

Additive config; default behavior identical except that `--gpus` /
`--shm-size` in `airgapped_extra_args` now fail worker startup with a
pointer to the named key (previously they silently worked — any operator
relying on that moves the value to the new key). Version: 0.60.0.
