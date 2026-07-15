# Spec: `docker-airgapped` runner

**Date:** 2026-07-14 · **Status:** approved for implementation (PR 1 of the
dynamic-workflows series) · **Depends on:** nothing — standalone.

## Motivation

Two consumers, one mechanism:

1. **Dynamic workflows (the series this opens).** Agents will author workflow
   source at runtime (PR 2). The author is an LLM — prompt-injectable, so the
   code must be treated as hostile. Rather than a restricted-language sandbox
   (the abandoned PR #77 approach), the isolation boundary is the runner: full
   Python inside a container whose **only I/O channel is the supervised stdio
   protocol** to the parent worker.
2. **Untrusted registered workflows, today.** SEC4 accepts that registration
   is RCE by contract. Operators currently mitigate with the subprocess
   runner (credential-less child) — but that still shares the worker's
   network, filesystem view, and unbounded CPU/memory. Pinning third-party or
   contractor workflows to `runner="docker-airgapped"` closes that gap with
   no new trust machinery.

Making this a **distinct runner name** (not a flag on the docker runner) puts
the guarantee where dispatch can see it: workers advertise runners at
registration, workflows pin them via `@workflow.with_options(runner=...)`,
and `matches_labels`/dispatch only route where they match. A workflow asking
for isolation can only land on a worker that actually enforces the locked
profile — a worker either runs it or never receives the work.

## Threat model

- **Adversary:** the workflow body (LLM-authored or third-party source),
  arbitrary Python, running inside the container.
- **Boundary:** the container. Its only capability channel is the stdio frame
  protocol to the parent worker, where every checkpoint, secret request,
  config read, approval, and `call()` hop is permission-checked against the
  execution's identity (unchanged from the base docker runner — the child
  holds no credentials).
- **In scope:** network exfiltration/SSRF, filesystem persistence/tampering,
  resource exhaustion (CPU, memory, pids, disk), privilege escalation inside
  the container, wall-clock slot squatting.
- **Out of scope (unchanged posture):** protocol-level abuse within granted
  permissions (that is what task-level authz and approval gates are for),
  container-runtime escapes (kernel/runc CVEs — deploy runtime updates;
  gVisor/Kata can be layered via the image/daemon without Flux changes),
  side channels.

“Air-gapped” means **no network namespace**, not no communication: stdin/
stdout to the parent remain, deliberately. A pleasant corollary: `pip
install` is impossible, so “generated code may only use what the image
ships” is enforced by physics, not policy.

## Design

### Runner

`AirgappedDockerRunner(DockerRunner)` in `flux/runners/docker.py`, with
`name = "docker-airgapped"`, added to `KNOWN_RUNNERS` and `create_runners`.
Same child entrypoint (`python -m flux.runners.child`), same frame protocol,
same cancellation path (SIGTERM via `--sig-proxy` → grace → `docker kill`),
same crash-to-durability mapping, same startup probe / spawn / force-kill /
reap — everything inherited.

**Reuse structure.** `DockerRunner._build_command` is refactored into a
template method (`prefix + _resource_args() + extra_args + _locked_args() +
image/entrypoint`, where `_locked_args()` returns `[]` in the base); the
airgapped subclass only overrides `_locked_args()`. The
locked-flags-after-extra-args ordering guarantee (docker's last-wins parsing
must favor the profile) is thereby structural, not conventional. Total new
runner code is ~80–100 lines: validation, the profile as data, and factory
wiring.

### Locked profile (non-configurable)

`_build_command` always emits, in addition to the base
`docker run -i --rm --name <n>`:

| Flag | Closes |
|---|---|
| `--network=none` | exfiltration, SSRF, `pip install` |
| `--read-only` | filesystem persistence/tampering |
| `--tmpfs /tmp:rw,size=<airgapped_tmp_size>` | scratch space without persistence; size-capped so /tmp can't exhaust host RAM |
| `--cap-drop=ALL` | in-container privilege use |
| `--security-opt=no-new-privileges` | setuid/priv escalation |
| `--pids-limit=<airgapped_pids_limit>` | fork bombs |
| `--memory=<airgapped_memory>` | allocation exhaustion |
| `--cpus=<airgapped_cpus>` | CPU squatting |

These are emitted from code, not config: an operator cannot accidentally
(or a config-file attacker deliberately) weaken the profile.

### Config (`[flux.workers]`, all prefixed `airgapped_`)

| Key | Default | Notes |
|---|---|---|
| `airgapped_image` | `""` | falls back to `docker_image`; one of the two required when the runner is enabled |
| `airgapped_memory` | `"512m"` | **required non-empty** — unlimited memory defeats the profile, so unlike `docker_memory` there is no "empty = unlimited" |
| `airgapped_cpus` | `1.0` | required > 0, same rationale |
| `airgapped_pids_limit` | `256` | required > 0 |
| `airgapped_tmp_size` | `"64m"` | tmpfs cap |
| `airgapped_execution_timeout` | `900` (s) | wall-clock ceiling per execution, `0` disables (discouraged; documented) |
| `airgapped_extra_args` | `[]` | veto-listed, see below |

### `extra_args` veto list

Operators may still need benign knobs (`--env TZ=…`, `--user`, an extra
`--tmpfs`). But `extra_args` must not be able to re-open what the profile
closed. At **worker startup** (fail fast, same philosophy as
`_verify_docker_available`), reject any argument whose flag name is one of:

```
--network, --net, --privileged, --cap-add, --device, --volume, -v,
--mount, --volumes-from, --pid, --ipc, --uts, --userns, --security-opt,
--group-add, --add-host, --dns, --dns-search, --dns-option, --link,
--publish, -p, --publish-all, -P, --expose, --sysctl, --cgroup-parent,
--cgroupns, --device-cgroup-rule, --oom-kill-disable
```

Matching is on the token before `=` or the bare token (docker accepts both
`--network=host` and `--network host`). The check is a denylist rather than
an allowlist deliberately: an allowlist would break legitimate operator needs
we can't enumerate, while the veto list only has to protect the specific
guarantees the profile makes — each entry maps to a row in the table above
or a host-namespace/device/mount/DNS reopening.

### Wall-clock ceiling

Implemented **once in `SubprocessRunner`** as a generic `execution_timeout`
(default `0` = disabled) rather than as airgapped-specific logic — the base
already owns `term_grace` and `_force_kill`, so subprocess and plain docker
gain an optional per-execution ceiling for free; the airgapped runner merely
defaults it to `airgapped_execution_timeout` (900s). On expiry the runner
force-kills the child/container and reports a **terminal FAILED** with a
distinct error — `ExecutionTimedOut`, whose message reads
`Execution <id> exceeded the runner's execution timeout (900s) and was
killed` — for **both** durabilities. The watchdog only claims the outcome
when the child is still alive at the deadline; a child that already exited
keeps its own verdict (a result, or a crash with the crash path's
durability handling). This intentionally diverges from the crash mapping (durable →
claim release → re-dispatch): a timeout is a policy violation the next
attempt would deterministically repeat, and mapping it to release would
produce an infinite re-dispatch loop. Task-level timeouts still apply inside
as usual; a workflow that legitimately pauses (approval gates) has exited the
container while paused, so the ceiling only bounds *active* execution time.

### Startup validation

- docker CLI + daemon reachable (inherited probe).
- Effective image resolves (either `airgapped_image` or `docker_image`).
- Limits validate (`memory` non-empty, `cpus` > 0, `pids_limit` > 0).
- `extra_args` pass the veto list.

All raise `ValueError` at `create_runners` time — a misconfigured fleet fails
at worker startup, not at first dispatch.

## What this PR deliberately does not touch

- The child protocol, env sanitization, checkpoint/secret/approval relays —
  inherited unchanged from `SubprocessRunner`/`DockerRunner`.
- Dispatch: matching on advertised runner names is already generic
  (`resource_request.py`); `runner="docker-airgapped"` just works.
- No ephemeral/agent registration (PR 2), no vocabulary layer (PR 3), no
  Kubernetes runner.

## Failure semantics

| Event | Behavior |
|---|---|
| container OOM-killed / crash | inherited: durable → fenced claim release + replay-resume; transient → terminal FAILED |
| wall-clock ceiling hit | terminal FAILED (both durabilities), distinct error |
| network attempt from body | fails inside the container (no interface); surfaces as ordinary task/workflow error |
| write outside `/tmp` | `OSError` (read-only rootfs); ordinary error |
| fork bomb | pids-limit → spawn failures inside; worst case OOM path |
| cancellation | inherited: SIGTERM → `term_grace` → `docker kill` |

## Testing

**Unit (run everywhere)** — extend `tests/flux/test_docker_runner.py` style:
- every locked flag present in `_build_command` output regardless of config;
- profile flags win: nothing in config can remove/duplicate-override them
  (locked flags emitted after `extra_args` so docker's last-wins parsing
  favors the profile — assert ordering);
- veto list: each banned flag, in both `--flag=v` and `--flag v` forms,
  raises at construction; benign args (`--env`, `--user`, `--tmpfs`) pass;
- limit validation errors (empty memory, cpus=0, pids=0);
- image fallback (`airgapped_image` → `docker_image` → error);
- timeout wiring: expiry force-kills and maps to terminal FAILED, not release
  (mock the proc, assert the hook calls).

**Integration (gated on `FLUX_TEST_DOCKER_IMAGE`, like the existing docker
integration test)** — real container:
- happy path completes and checkpoints via the relay;
- `urllib.request.urlopen("http://example.com")` in the body fails
  (no network);
- write to `/app`/`/` fails, write to `/tmp` succeeds;
- a `time.sleep` body exceeding a tiny configured ceiling lands FAILED with
  the timeout error, and is **not** re-dispatched.

**Docs:** runner section in `docs/` (production-deployment security
checklist entry: "pin untrusted workflows to docker-airgapped"), DOCKER.md
image notes, decisions-log entry in the production-readiness review.

## Rollout / compatibility

Purely additive: a new runner name, new config keys with safe defaults, no
changes to existing runners or dispatch. Workers not opting in advertise
nothing new. Version bump: minor (new feature) — 0.57.0.
