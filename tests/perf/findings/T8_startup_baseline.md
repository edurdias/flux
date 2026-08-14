# T8 — startup / image-load baseline and improvement areas

Recorded 2026-08-13, `ci` profile, after the #233 import diet (PR #237).
Numbers are medians; machine spec in the results JSONs under `results/T8/`.

## Baseline

| Role | Bare import | In-container load | Pre-diet (main @ #231) |
|---|---:|---:|---:|
| server (`flux.server`) | 927 ms | 1022 ms | 1050 ms |
| worker (`flux.worker`) | 333 ms | 408 ms | 660 ms |
| execution child (`flux.runners.child`) | 65 ms | 153 ms | 599 ms |

Container-load minus bare ≈ 90 ms per role = runc + interpreter start on this
machine; it is the floor no import work can get under.

The diet's chain cut (runners registry → loader → workflow → context_managers)
paid twice: the child dropped 599→65 ms as designed, and the worker dropped
660→333 ms as a side effect — the worker rode the same eager registry into
SQLAlchemy despite being HTTP-only by design.

## Import manifests (what each role loads at import time)

| Package | server | worker | child |
|---|---|---|---|
| sqlalchemy | yes (owns the DB) | **no** | **no** |
| fastapi / uvicorn | yes | **no** | **no** |
| pydantic | yes | yes (config) | **no** — arrives in `_run` (config) |
| httpx | yes | yes | no |
| Crypto (PyCryptodome) | yes | yes | **no** |

The child's column is import-time only, which is what the manifest records:
its 65 ms module-level cost excludes pydantic entirely — the config graph
(~196 ms including pydantic) loads inside `_run`, after the frame protocol
is already up. The improvement below targets that deferred load, not the
module-level number.

Structural invariants are enforced always-on in
`tests/flux/test_startup_import_budget.py` (server/worker) and
`tests/flux/test_child_import_graph.py` (child); T8's manifest test records
the full picture per run so a creep shows up as a results diff even when
wall time hides it in runner noise.

## Improvement areas, in value order

1. **Child `_run`-path config load: done (issue #241).** The parent now
   snapshots an allowlisted, secret-free slice of its settings into the
   child environment, and the child installs a plain-namespace stand-in as
   `flux.config` before anything resolves it — every runtime read (logging
   setup, the per-task auth gate, cache/storage paths) works unmodified
   and pydantic never loads. Total child import work: ~670 ms pre-#233 →
   260 ms post-#233 → **167 ms**, asserted end-to-end by the leanness
   probes (subprocess and container tiers).
2. **Worker residual (~333 ms): pydantic (~160 ms) + httpx (~110 ms).** Both
   genuinely used at startup (config, HTTP client). No cheap cut; a lazy
   `pydantic_settings` import inside `Configuration.load` would help only
   processes that never read config — no such process exists. Not worth
   structural change now; the number is recorded so drift is visible.
3. **Server (~927 ms): fastapi ~300 ms, sqlalchemy ~190 ms, pydantic,
   uvicorn — all load-bearing at startup.** The only structural idea with
   real headroom is deferring route-module imports until app construction,
   which buys nothing for the actual server process (it constructs the app
   immediately) and only speeds `import flux.server` in tests/tooling. Not
   recommended; the per-process cost is paid once per deployment, not per
   request.
4. **Crypto (PyCryptodome) loads in server and worker** (encryption for the
   secrets store). The child no longer loads it at import time; if the
   `_run` path pulls it regardless of whether the workflow requests
   secrets, that is a candidate rider for the config-light child work
   in (1).

## Method

- Bare: fresh interpreter per sample, `time.perf_counter()` around the
  import, medians over `samples` (profile-dependent).
- Container: same probe via `docker run --rm <image> python -c ...` against
  an image built from the working tree (`make docker-test-image`).
- Budgets are soft gates (recorded verdicts; hard only under
  `FLUX_PERF_STRICT=1`), sized ~2.5× the observed medians so shared-runner
  noise produces numbers, not red pipelines.
