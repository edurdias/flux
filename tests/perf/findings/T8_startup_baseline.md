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

## Import manifests (what each role loads)

| Package | server | worker | child |
|---|---|---|---|
| sqlalchemy | yes (owns the DB) | **no** | **no** |
| fastapi / uvicorn | yes | **no** | **no** |
| pydantic | yes | yes (config) | yes (config) |
| httpx | yes | yes | no |
| Crypto (PyCryptodome) | yes | yes | yes |

Structural invariants are enforced always-on in
`tests/flux/test_startup_import_budget.py` (server/worker) and
`tests/flux/test_child_import_graph.py` (child); T8's manifest test records
the full picture per run so a creep shows up as a results diff even when
wall time hides it in runner noise.

## Improvement areas, in value order

1. **Child residual (~65 ms bare / ~153 ms container): pydantic via
   `flux.config`.** The child reads Configuration for loader cache settings
   and runner knobs — a handful of scalars. A config-light child (parent
   passes the needed values over the frame protocol, as it already does for
   secrets/configs) would cut most of the residual and drop pydantic from
   the sandbox entirely. Medium effort; per-execution payoff for container
   runners only.
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
4. **Crypto (PyCryptodome) loads in all three roles** via the secret-manager
   ABC chain. Small (~10 ms), but the child needs it only when a workflow
   actually requests secrets — a candidate rider for the config-light child
   work in (1).

## Method

- Bare: fresh interpreter per sample, `time.perf_counter()` around the
  import, medians over `samples` (profile-dependent).
- Container: same probe via `docker run --rm <image> python -c ...` against
  an image built from the working tree (`make docker-test-image`).
- Budgets are soft gates (recorded verdicts; hard only under
  `FLUX_PERF_STRICT=1`), sized ~2.5× the observed medians so shared-runner
  noise produces numbers, not red pipelines.
