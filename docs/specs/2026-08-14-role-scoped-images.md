# Role-scoped published images

Status: accepted (2026-08-14). Closes #247.

## Problem

One published image serves every role. A **runner child** is the sharpest case:
it runs rootless, `--network=none`, and reaches its engine only over a granted
Unix socket, so every package in it is surface inside the sandbox. Measured on
0.81.13, published against the same Dockerfile built with `FLUX_EXTRAS=""`:

| | size | packages |
|---|---|---|
| `flux:0.81.13` | 549 MB | 126 |
| no extras | 428 MB | 96 |

The 30-package delta is `openai` (20 MB), the `google-*` stack (19 MB), the
`psycopg` family (21 MB) and `anthropic` (13 MB), plus their transitive deps.
Three vendor clients for calling hosted LLM APIs, in a container with no
network, cannot function there by construction. The Postgres driver is the
milder version of the same point: the child is deliberately denied a database
URL.

It also works against the child-import diet (#242, #233), whose stated goal is
keeping heavy dependencies out of the sandbox — and which the default image
then puts back.

## Decision

Publish three variants from the existing Dockerfile, parameterized by the
`FLUX_EXTRAS` build arg it already accepts. No new Dockerfile machinery.

| Tag | `FLUX_EXTRAS` | Intended role |
|---|---|---|
| `flux:X.Y.Z`, `X.Y`, `latest` | `postgresql,observability,ai` | unchanged default: any role, one image |
| `flux:X.Y.Z-slim`, `X.Y-slim`, `latest-slim` | *(empty)* | runner children (`docker_image`, `airgapped_image`) |
| `flux:X.Y.Z-server`, `X.Y-server`, `latest-server` | `postgresql,observability` | servers and workers that never call a hosted model |

The default stays the everything image. Making it slim would silently change
what `flux:latest` means for anyone already running it, and an agent workflow
on the new default would fail at import with no obvious cause.

## Mechanism

`docker-publish.yml`'s single build job becomes a matrix over the three
variants. Each entry carries its extras and a tag suffix, applied through
`docker/metadata-action`'s `flavor: suffix=`, so every variant gets the full
tag set (`X.Y.Z`, `X.Y`, `latest`, branch) with its suffix appended.

Two details are load-bearing:

- **Cache scope per variant.** `type=gha` without a `scope` is shared, so
  three variants would evict each other and every build would run cold.
- **Attestation per variant.** `subject-digest` differs per build, so the
  attest step belongs inside the matrix, not after it.

## The packaging guard

Intent that is only documented drifts. Each variant therefore builds
`linux/amd64` with `load: true` first, asserts its package set, and only then
runs the multi-arch build-and-push — a cache hit, so the second build is
cheap. A promoted dependency (an extra becoming a base requirement) fails the
publish instead of silently refilling the sandbox.

`docker/scripts/verify_image_variant.py` takes a variant name and asserts,
inside the built image:

| Variant | must NOT import | must import |
|---|---|---|
| `slim` | `openai`, `anthropic`, `ollama`, `google.genai`, `psycopg`, `opentelemetry.sdk`, `opentelemetry.exporter` | — |
| `server` | `openai`, `anthropic`, `ollama`, `google.genai` | `psycopg`, `opentelemetry.sdk`, `opentelemetry.exporter` |
| `full` | — | all of the above |

It checks importability with `importlib.util.find_spec`, so it is a packaging
assertion rather than an import side-effect test, and it prints the offending
module on failure.

The rules name `opentelemetry.sdk` rather than the `opentelemetry` namespace
for a reason found by running the guard against a real build: a no-extras
image still carries `opentelemetry-api`, even though the published wheel
declares it under `extra == "observability"` and no installed distribution
requires it. The measurement in #247 shows the same — its 30-package delta
lists the exporters and the SDK but not `-api`. The SDK and exporters are the
weight the extra actually adds, so those are what slim forbids. The stray
`-api` is worth a separate look; it is not this change's business.

Measured locally on amd64 against flux-core 0.81.12 (the version PyPI serves
today), building this Dockerfile with each variant's extras: slim 418 MB,
server 497 MB, full 549 MB (#247's figure).

## Rollout

The workflow runs on `main` and `v*` tags only, and nothing in PR CI exercises
it, so a broken matrix would first surface as a failed publish. A
`workflow_dispatch` input `push` (default `true`) allows one manual dry run —
build and verify every variant, push nothing — before the change goes live.

## Documentation

- `docs/getting-started/installation.md`: the variants table, and which to
  pull for which role.
- `docs/advanced-features/system-tools.md` and `airgapped-execution.md`: point
  `docker_image` / `airgapped_image` examples at `-slim`.
- `docs/production-deployment.md`: name the variant in the runner section.

## Out of scope

- Changing what `flux:latest` resolves to.
- A `-worker` variant: a worker's needs are the server's minus nothing
  measurable, and nobody has asked.
- Local Makefile targets for variant builds; the matrix is the deliverable.
