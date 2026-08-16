# Outbound hooks — slice 1 implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `hooks` entity whose rows subscribe to engine events by selector and start a workflow in response, delivered through a transactional outbox drained by the scheduler tick.

**Architecture:** Enqueue is transactional with the event it reports — both execution state transitions and task events persist through one method (`context_managers.py::_save_with_session`), so a single enqueue site covers both. The drain runs in the existing scheduler loop under the cross-replica dispatch lock, builds a redacted envelope, re-checks the hook principal's permission at fire time, and creates an execution of the target workflow. Matching is a pure function over the permission wildcard matcher, fronted by an in-memory index invalidated on hook CRUD.

**Tech Stack:** Python 3.12, SQLAlchemy + Alembic, FastAPI, Click, pytest.

## Global Constraints

- Spec: `docs/specs/2026-08-14-outbound-hooks-spec.md` — binding; on conflict, the spec wins. Slice 1 is: entity + migration, selector matcher, transactional outbox enqueue, scheduler-tick drain, CRUD routes + CLI, `POST /hooks/{name}/test`. Declaration paths 2 and 3 (workflow-declared, agent-declared) and `approval_routing="notify"` are **later slices — do not build them**.
- The only action is `run_workflow`. No HTTP, no templating, no secrets on the hook row.
- Selectors reuse `flux/security/identity.py::_wildcard_match(pattern_parts, target_parts)` **verbatim** — terminal `*` matches any number of remaining segments, non-terminal `*` matches exactly one. Do not write a second matcher.
- Selector domains and their segment counts: `execution:<ns>:<workflow>:<state>` and `task:<ns>:<workflow>:<task>:<event>`. States and event types are matched by their **lower-cased enum value** (`paused`, `failed`, `awaiting_approval`, …).
- Migration discipline: new revision `0026_hooks` chained after `0025_execution_name`; update `HEAD` in BOTH `tests/flux/test_migrations.py:16` and `tests/flux/test_migrations_postgresql.py:33`. ORM columns land in the same commit as the revision.
- Permissions: `hook:<name>:<verb>` with verbs `create`, `read`, `update`, `delete`, plus `hook:deliveries:read` and `hook:deliveries:retry`. `operator` gets full hook management; `viewer` gets `hook:*:read` + `hook:deliveries:read`; `worker` gets nothing; `admin` inherits via `*`.
- At-least-once with exponential backoff; `dead` after `max_attempts`. Hop guard default 3 — an execution started by a hook stamps `hop + 1`, past the cap the delivery goes straight to `dead` with a loop error.
- Envelope payloads pass through `flux/security/redaction.py` when built.
- Repo rules: version bump in `pyproject.toml` (use `0.85.0` — feature), pre-commit before every commit (`poetry run pre-commit run mypy --all-files` once before push), comments explain *why* only, no AI attribution anywhere.
- All commits on branch `feat/outbound-hooks`.

## File structure

| File | Responsibility |
|---|---|
| `flux/hooks/__init__.py` | public names: `HookRegistry`, `match_selectors`, `event_keys_for` |
| `flux/hooks/selectors.py` | selector parse/validate + match; event-key derivation from a persisted event |
| `flux/hooks/registry.py` | enabled-hook snapshot + bucketed index, invalidation, CRUD used by routes/CLI |
| `flux/hooks/envelope.py` | envelope construction incl. redaction and hop derivation |
| `flux/hooks/drain.py` | one drain pass: claim due deliveries, authorize, create execution, record outcome |
| `flux/models.py` | `HookModel`, `HookDeliveryModel` |
| `flux/migrations/versions/0026_hooks.py` | both tables |
| `flux/api/hook_routes.py` | `HookRoutesMixin` |
| `flux/api/schemas.py` | hook request/response models |
| `flux/cli.py` | `flux hook` group |

---

### Task 1: Entity — migration 0026 and ORM models

**Files:**
- Create: `flux/migrations/versions/0026_hooks.py`
- Modify: `flux/models.py` (add `HookModel`, `HookDeliveryModel` after `ScheduleModel`, which ends at ~line 965)
- Modify: `tests/flux/test_migrations.py:16`, `tests/flux/test_migrations_postgresql.py:33` (HEAD → `0026_hooks`)
- Test: `tests/flux/test_hook_models.py`

**Interfaces:**
- Produces: `HookModel(id, name, enabled, selectors, action, workflow_ref, principal_id, owner_type, owner_ref, max_attempts, created_by, created_at, updated_at)`; `HookDeliveryModel(id, hook_id, event_key, payload, attempts, status, execution_id, next_attempt_at, last_error, created_at, delivered_at)`. `HookDeliveryModel.status` values are the literals `"pending" | "delivered" | "dead"`. Unique constraint `(hook_id, event_key)` — the enqueue relies on it for idempotency.

- [ ] **Step 1: Write the failing test** in `tests/flux/test_hook_models.py` (fixture pattern: copy the `isolated_db` usage from `tests/flux/test_execution_name.py`):

```python
class TestHookModels:
    def test_hook_round_trips_with_its_selector_list(self, isolated_db):
        repo = RepositoryFactory.create_repository()
        with repo.session() as session:
            session.add(
                HookModel(
                    name="notify-approvals",
                    selectors=["task:release:*:promote_prod:awaiting_approval"],
                    workflow_ref="ops/notify_slack",
                    principal_id="p-1",
                    owner_type="user",
                    owner_ref="admin",
                ),
            )
            session.commit()

        with repo.session() as session:
            row = session.query(HookModel).filter_by(name="notify-approvals").one()
            assert row.selectors == ["task:release:*:promote_prod:awaiting_approval"]
            assert row.action == "run_workflow"
            assert row.enabled is True
            assert row.max_attempts == 5
            assert row.id and row.created_at

    def test_hook_names_are_unique(self, isolated_db):
        repo = RepositoryFactory.create_repository()
        with repo.session() as session:
            session.add(HookModel(name="dup", selectors=[], workflow_ref="a/b",
                                  principal_id="p", owner_type="user", owner_ref="admin"))
            session.commit()
        with pytest.raises(IntegrityError):
            with repo.session() as session:
                session.add(HookModel(name="dup", selectors=[], workflow_ref="a/b",
                                      principal_id="p", owner_type="user", owner_ref="admin"))
                session.commit()

    def test_one_delivery_per_hook_and_event(self, isolated_db):
        """The enqueue is idempotent by construction: replays and retries
        cannot fan a single event into duplicate deliveries."""
        repo = RepositoryFactory.create_repository()
        with repo.session() as session:
            hook = HookModel(name="h", selectors=[], workflow_ref="a/b",
                             principal_id="p", owner_type="user", owner_ref="admin")
            session.add(hook)
            session.commit()
            hook_id = hook.id

        with repo.session() as session:
            session.add(HookDeliveryModel(hook_id=hook_id, event_key="e-1", payload={}))
            session.commit()
        with pytest.raises(IntegrityError):
            with repo.session() as session:
                session.add(HookDeliveryModel(hook_id=hook_id, event_key="e-1", payload={}))
                session.commit()

    def test_deleting_a_hook_takes_its_deliveries(self, isolated_db):
        repo = RepositoryFactory.create_repository()
        with repo.session() as session:
            hook = HookModel(name="h", selectors=[], workflow_ref="a/b",
                             principal_id="p", owner_type="user", owner_ref="admin")
            session.add(hook)
            session.commit()
            session.add(HookDeliveryModel(hook_id=hook.id, event_key="e-1", payload={}))
            session.commit()
            session.delete(hook)
            session.commit()
            assert session.query(HookDeliveryModel).count() == 0
```

- [ ] **Step 2: Run — must fail** (`poetry run pytest tests/flux/test_hook_models.py -q`; expect ImportError on `HookModel`).
- [ ] **Step 3: ORM models** in `flux/models.py`, copying `ScheduleModel`'s shape (`models.py:905-965`) for id/timestamps/`__table_args__`:

```python
class HookModel(Base):
    """A named subscription: when an engine event matches one of ``selectors``,
    start ``workflow_ref`` as ``principal_id``.

    Nothing about *how* the outside world is reached lives here — no URL, no
    secret, no template. The target workflow owns all of that, so a hook row
    only says when to fire, what to start, and as whom.
    """

    __tablename__ = "hooks"

    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: uuid4().hex)
    name = Column(String, nullable=False, unique=True)
    enabled = Column(Boolean, nullable=False, default=True)
    selectors = Column(JSON, nullable=False, default=list)
    # Enum-shaped for a future no-execution variant; "run_workflow" is the
    # only value slice 1 accepts.
    action = Column(String, nullable=False, default="run_workflow")
    workflow_ref = Column(String, nullable=False)
    principal_id = Column(String, nullable=False)
    owner_type = Column(String, nullable=False, default="user")
    owner_ref = Column(String, nullable=False)
    max_attempts = Column(Integer, nullable=False, default=5)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    deliveries = relationship("HookDeliveryModel", back_populates="hook", cascade="all, delete-orphan")


class HookDeliveryModel(Base):
    """One hook's obligation to react to one event.

    Written in the same transaction as the event it reports (the outbox), and
    drained later by the scheduler tick — so no delivery blocks a checkpoint
    and no event is missed.
    """

    __tablename__ = "hook_deliveries"

    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: uuid4().hex)
    hook_id = Column(String, ForeignKey("hooks.id", ondelete="CASCADE"), nullable=False)
    event_key = Column(String, nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    attempts = Column(Integer, nullable=False, default=0)
    status = Column(String, nullable=False, default="pending")
    execution_id = Column(String, nullable=True)
    next_attempt_at = Column(DateTime, nullable=True)
    last_error = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    delivered_at = Column(DateTime, nullable=True)

    hook = relationship("HookModel", back_populates="deliveries")

    __table_args__ = (
        # The enqueue writes blind and lets the constraint dedupe: a replayed
        # or retried save cannot fan one event into two deliveries.
        UniqueConstraint("hook_id", "event_key", name="uq_hook_delivery_event"),
        Index("ix_hook_deliveries_due", "status", "next_attempt_at"),
    )
```

- [ ] **Step 4: Migration** `flux/migrations/versions/0026_hooks.py` — copy the guarded shape of `flux/migrations/versions/0011_worker_join_tokens.py` (inspect table names, return early if present; `downgrade()` mirrors with `op.drop_table`). `revision = "0026_hooks"`, `down_revision = "0025_execution_name"`. Create both tables with the columns above, the unique constraint and the index.
- [ ] **Step 5: Green + update both HEAD constants; run** `poetry run pytest tests/flux/test_hook_models.py tests/flux/test_migrations.py -q`.
- [ ] **Step 6: Commit** `feat(hooks): hooks and hook_deliveries tables`.

---

### Task 2: Selector matching and event keys

**Files:**
- Create: `flux/hooks/__init__.py`, `flux/hooks/selectors.py`
- Test: `tests/flux/hooks/__init__.py`, `tests/flux/hooks/test_selectors.py`

**Interfaces:**
- Consumes: `flux/security/identity.py::_wildcard_match`.
- Produces:
```python
DOMAINS = ("execution", "task")          # segment counts: 4 and 5

def validate_selector(selector: str) -> None
    # raises ValueError naming the problem; used by the routes' 400 path

def selector_matches(selector: str, event_key: str) -> bool

@dataclass(frozen=True)
class HookEvent:
    domain: str            # "execution" | "task"
    key: str               # the matchable string, e.g. "task:release:promote:promote_prod:awaiting_approval"
    execution_id: str
    workflow_namespace: str
    workflow_name: str
    event_id: str          # the persisted ExecutionEvent id -- also the delivery's event_key
    type: str              # lower-cased state or event-type value
    task_name: str | None
    task_call_id: str | None
    value: Any
    occurred_at: str       # ISO-8601

def events_from_save(ctx, new_events) -> list[HookEvent]
    # ctx: ExecutionContext, new_events: the ExecutionEvent rows this save persists
```

- [ ] **Step 1: Failing tests** in `tests/flux/hooks/test_selectors.py`:

```python
@pytest.mark.parametrize(
    "selector,key,expected",
    [
        ("execution:*", "execution:release:promote:failed", True),
        ("execution:*:*:failed", "execution:release:promote:failed", True),
        ("execution:*:*:failed", "execution:release:promote:completed", False),
        ("execution:release:*:paused", "execution:release:promote:paused", True),
        ("execution:release:*:paused", "execution:ops:promote:paused", False),
        # non-terminal * matches exactly one segment
        ("execution:*:promote:failed", "execution:release:promote:failed", True),
        ("task:release:*:promote_prod:awaiting_approval",
         "task:release:pipeline:promote_prod:awaiting_approval", True),
        ("task:release:*:*:rejected", "task:release:pipeline:promote_prod:rejected", True),
        # domains do not cross
        ("execution:*", "task:release:pipeline:promote_prod:rejected", False),
    ],
)
def test_selector_matching(selector, key, expected):
    assert selector_matches(selector, key) is expected


@pytest.mark.parametrize(
    "selector",
    [
        "",
        "workflow:*",                    # unknown domain
        "execution",                     # no segments
        "execution:a:b",                 # too few for the domain
        "execution:a:b:c:d",             # too many
        "task:a:b:c",                    # too few for the task domain
    ],
)
def test_invalid_selectors_are_rejected(selector):
    with pytest.raises(ValueError):
        validate_selector(selector)


def test_valid_selectors_are_accepted():
    for selector in ("execution:*", "task:*", "execution:ns:wf:paused",
                     "task:ns:wf:task_name:awaiting_approval"):
        validate_selector(selector)


def test_events_from_save_yields_one_event_per_persisted_event():
    """A save persists a state transition and any new task events; each is a
    separately matchable event, keyed by the persisted event's own id so the
    delivery is idempotent."""
    ctx = _ctx(namespace="release", name="pipeline", execution_id="exec-1")
    events = [
        _event(ExecutionEventType.WORKFLOW_PAUSED, event_id="ev-1", name="pipeline"),
        _event(ExecutionEventType.TASK_AWAITING_APPROVAL, event_id="ev-2", name="promote_prod",
               value={"task_call_id": "call-9", "task_name": "promote_prod"}),
    ]

    produced = events_from_save(ctx, events)

    assert [e.key for e in produced] == [
        "execution:release:pipeline:paused",
        "task:release:pipeline:promote_prod:awaiting_approval",
    ]
    assert [e.event_id for e in produced] == ["ev-1", "ev-2"]
    assert produced[1].task_call_id == "call-9"
```

- [ ] **Step 2: Run — must fail** (module missing).
- [ ] **Step 3: Implement** `flux/hooks/selectors.py`. `selector_matches` splits both sides on `:` and defers to `_wildcard_match(selector_parts, key_parts)`. `validate_selector` checks the domain is known and the segment count matches the domain's width **unless** the selector ends in a terminal `*` (which legitimately covers the remainder). `events_from_save` maps `WORKFLOW_*` events to the `execution` domain using `ctx.state.value.lower()`, and `TASK_*` events to the `task` domain using the event type with its `TASK_` prefix stripped and lower-cased; `task_name` comes from the event's `name`, `task_call_id` from `source_id`.
- [ ] **Step 4: Green.**
- [ ] **Step 5: Commit** `feat(hooks): selector grammar over the permission matcher`.

---

### Task 3: Registry — enabled-hook index and CRUD

**Files:**
- Create: `flux/hooks/registry.py`
- Test: `tests/flux/hooks/test_registry.py`

**Interfaces:**
- Consumes: Task 1's models, Task 2's `selector_matches`/`validate_selector`.
- Produces:
```python
class HookRegistry:
    @classmethod
    def create(cls) -> HookRegistry            # process-wide singleton, like ContextManager.create()
    def snapshot(self) -> tuple[HookIndexEntry, ...]   # enabled hooks only, cached
    def invalidate(self) -> None                        # called by every CRUD write
    def matches(self, event: HookEvent) -> list[HookIndexEntry]
    def has_any(self) -> bool                           # the enqueue fast path
    # CRUD used by routes and CLI
    def create_hook(self, *, name, selectors, workflow_ref, principal_id, owner_type="user",
                    owner_ref, max_attempts=5, created_by=None) -> HookModel
    def list_hooks(self, *, enabled_only: bool = False) -> list[HookModel]
    def get_hook(self, name: str) -> HookModel          # raises HookNotFoundError
    def update_hook(self, name: str, **fields) -> HookModel
    def delete_hook(self, name: str) -> None

@dataclass(frozen=True)
class HookIndexEntry:
    id: str; name: str; selectors: tuple[str, ...]; workflow_ref: str
    principal_id: str; max_attempts: int
```
- `HookNotFoundError` lives in `flux/errors.py` beside the other domain errors.

- [ ] **Step 1: Failing tests** in `tests/flux/hooks/test_registry.py`:

```python
class TestRegistry:
    def test_matches_returns_only_hooks_whose_selector_fires(self, isolated_db):
        registry = HookRegistry.create()
        registry.create_hook(name="on-fail", selectors=["execution:*:*:failed"],
                             workflow_ref="ops/incident", principal_id="p", owner_ref="admin")
        registry.create_hook(name="on-pause", selectors=["execution:*:*:paused"],
                             workflow_ref="ops/nudge", principal_id="p", owner_ref="admin")

        matched = registry.matches(_event("execution:release:pipeline:failed"))

        assert [entry.name for entry in matched] == ["on-fail"]

    def test_a_hook_matches_once_even_when_two_selectors_fire(self, isolated_db):
        """Two selectors on one row are an OR, not a fan-out: one hook, one
        delivery."""
        registry = HookRegistry.create()
        registry.create_hook(name="broad", selectors=["execution:*", "execution:*:*:failed"],
                             workflow_ref="ops/incident", principal_id="p", owner_ref="admin")

        matched = registry.matches(_event("execution:release:pipeline:failed"))

        assert len(matched) == 1

    def test_disabled_hooks_never_match(self, isolated_db):
        registry = HookRegistry.create()
        registry.create_hook(name="off", selectors=["execution:*"], workflow_ref="ops/x",
                             principal_id="p", owner_ref="admin")
        registry.update_hook("off", enabled=False)

        assert registry.matches(_event("execution:a:b:failed")) == []

    def test_crud_invalidates_the_snapshot(self, isolated_db):
        registry = HookRegistry.create()
        assert registry.has_any() is False

        registry.create_hook(name="h", selectors=["execution:*"], workflow_ref="ops/x",
                             principal_id="p", owner_ref="admin")

        assert registry.has_any() is True
        registry.delete_hook("h")
        assert registry.has_any() is False

    def test_create_rejects_an_invalid_selector(self, isolated_db):
        with pytest.raises(ValueError):
            HookRegistry.create().create_hook(name="bad", selectors=["nope:*"],
                                              workflow_ref="ops/x", principal_id="p",
                                              owner_ref="admin")

    def test_get_missing_hook_raises(self, isolated_db):
        with pytest.raises(HookNotFoundError):
            HookRegistry.create().get_hook("absent")
```

- [ ] **Step 2: Run — must fail.**
- [ ] **Step 3: Implement.** The snapshot is a module-level cache guarded by a `threading.Lock`, rebuilt on first use after `invalidate()`. Every CRUD method calls `invalidate()` after committing. `matches` iterates the snapshot and returns each entry whose *any* selector matches (never twice).
- [ ] **Step 4: Green.**
- [ ] **Step 5: Commit** `feat(hooks): registry with a cached enabled-hook snapshot`.

---

### Task 4: Transactional outbox enqueue

**Files:**
- Modify: `flux/context_managers.py` (`_save_with_session`, around the `session.add_all(self._get_additional_events(ctx, session))` calls at lines ~477 and ~558)
- Modify: `flux/config.py` (add a `hooks` settings section: `enabled: bool = True`, `hop_limit: int = 3`, `drain_batch_size: int = 20`)
- Test: `tests/flux/hooks/test_outbox.py`

**Interfaces:**
- Consumes: `HookRegistry.has_any/matches`, `events_from_save`.
- Produces: `flux/hooks/outbox.py::enqueue(session, ctx, new_events) -> int` (returns rows added; called inside the caller's transaction, never commits).

- [ ] **Step 1: Failing tests** in `tests/flux/hooks/test_outbox.py`:

```python
class TestOutbox:
    def test_a_committed_state_write_leaves_one_delivery(self, isolated_db):
        _hook(selectors=["execution:*:*:paused"], workflow_ref="ops/notify")
        manager = ContextManager.create()
        ctx = _paused_execution()

        manager.save(ctx)

        rows = _deliveries()
        assert len(rows) == 1
        assert rows[0].status == "pending"
        assert rows[0].payload["event"]["type"] == "paused"

    def test_a_rolled_back_write_leaves_none(self, isolated_db):
        """The enqueue is in the caller's transaction: no event, no delivery."""
        _hook(selectors=["execution:*"], workflow_ref="ops/notify")
        with UnitOfWork() as uow:
            ContextManager.create().save(_paused_execution(), uow=uow)
            uow.rollback()

        assert _deliveries() == []

    def test_saving_twice_does_not_duplicate(self, isolated_db):
        """Checkpoints re-send events; the unique constraint absorbs it."""
        _hook(selectors=["execution:*"], workflow_ref="ops/notify")
        ctx = _paused_execution()
        ContextManager.create().save(ctx)
        ContextManager.create().save(ctx)

        assert len(_deliveries()) == 1

    def test_no_hooks_means_no_work(self, isolated_db, monkeypatch):
        """The fast path must not query or match when nothing subscribes."""
        calls = []
        monkeypatch.setattr(HookRegistry, "matches", lambda self, e: calls.append(e) or [])

        ContextManager.create().save(_paused_execution())

        assert calls == []

    def test_task_events_enqueue_under_the_task_domain(self, isolated_db):
        _hook(selectors=["task:*:*:promote_prod:awaiting_approval"], workflow_ref="ops/notify")

        ContextManager.create().save(_execution_with_awaiting_approval("promote_prod"))

        [row] = _deliveries()
        assert row.payload["event"]["task_name"] == "promote_prod"

    def test_disabled_by_config_enqueues_nothing(self, isolated_db):
        _hook(selectors=["execution:*"], workflow_ref="ops/notify")
        Configuration.get().override(hooks={"enabled": False})

        ContextManager.create().save(_paused_execution())

        assert _deliveries() == []
```

- [ ] **Step 2: Run — must fail.**
- [ ] **Step 3: Implement** `flux/hooks/outbox.py::enqueue`, called from `_save_with_session` immediately after the `session.add_all(self._get_additional_events(...))` line in **both** branches (update and insert), passing the same event list. Guard order matters for the hot path: return immediately when hooks are disabled in config or `registry.has_any()` is false, before building any event. Insert each `(hook, event)` pair with a `session.begin_nested()` savepoint per row so a duplicate-key violation from the unique constraint skips that row without poisoning the caller's transaction.
- [ ] **Step 4: Green + run `poetry run pytest tests/flux/ -q`** (this touches the hottest write path in the engine — a regression here is a regression everywhere).
- [ ] **Step 5: Commit** `feat(hooks): enqueue deliveries in the transaction that records the event`.

---

### Task 5: Envelope

**Files:**
- Create: `flux/hooks/envelope.py`
- Test: `tests/flux/hooks/test_envelope.py`

**Interfaces:**
- Produces: `build_envelope(hook: HookIndexEntry, selector: str, event: HookEvent, *, delivery_id: str, attempt: int, hop: int) -> dict` — the exact shape in the spec's Envelope section; `parent_hop(execution_input: Any) -> int` returning the `hop` of a hook-started execution's input, else `-1`.

- [ ] **Step 1: Failing tests** in `tests/flux/hooks/test_envelope.py`:

```python
def test_envelope_carries_the_spec_shape():
    envelope = build_envelope(_entry(name="notify-approvals"),
                              selector="task:release:*:promote_prod:awaiting_approval",
                              event=_task_event(), delivery_id="d-1", attempt=1, hop=0)

    assert envelope["hook"] == "notify-approvals"
    assert envelope["selector"] == "task:release:*:promote_prod:awaiting_approval"
    assert envelope["delivery_id"] == "d-1"
    assert envelope["attempt"] == 1 and envelope["hop"] == 0
    assert envelope["event"]["domain"] == "task"
    assert envelope["event"]["task_name"] == "promote_prod"
    assert envelope["event"]["state"] is None
    assert envelope["event"]["occurred_at"].endswith("Z") or "T" in envelope["event"]["occurred_at"]


def test_secret_values_are_redacted_in_the_envelope(isolated_db):
    """Redaction happens when the envelope is built, before anything else can
    read it."""
    SecretManager.current().save("api_key", "s3cr3t")
    envelope = build_envelope(_entry(), selector="execution:*",
                              event=_event_with_value({"token": "s3cr3t"}),
                              delivery_id="d", attempt=1, hop=0)

    assert "s3cr3t" not in json.dumps(envelope)


def test_parent_hop_reads_a_hook_started_execution():
    assert parent_hop({"hook": "h", "hop": 2, "event": {}}) == 2


def test_parent_hop_of_an_ordinary_execution_is_minus_one():
    for value in (None, "text", {"anything": "else"}, [1, 2]):
        assert parent_hop(value) == -1
```

- [ ] **Step 2: fail → Step 3: implement → Step 4: green.** Redaction goes through `flux/security/redaction.py`'s value-identity scrub, the same call the execution read path uses.
- [ ] **Step 5: Commit** `feat(hooks): redacted delivery envelope with hop accounting`.

---

### Task 6: Drain

**Files:**
- Create: `flux/hooks/drain.py`
- Modify: `flux/server.py` (`_scheduler_loop`, inside the `dispatch_lock` block after the wake pass at ~line 1156)
- Test: `tests/flux/hooks/test_drain.py`

**Interfaces:**
- Consumes: envelope, registry, `Server._create_execution`.
- Produces: `async def drain_once(create_execution: Callable[..., Awaitable[str]], *, now: datetime, batch_size: int, hop_limit: int, authorize: Callable[[str, str], Awaitable[bool]]) -> int` — returns deliveries handled. `create_execution(namespace, workflow_name, input_data)` returns the execution id; `authorize(principal_id, permission)` returns whether the hook's principal may run the target.

- [ ] **Step 1: Failing tests** in `tests/flux/hooks/test_drain.py`:

```python
class TestDrain:
    async def test_a_pending_delivery_starts_the_target_and_records_it(self, isolated_db):
        _pending(hook=_hook(workflow_ref="ops/notify"), event_key="ev-1")

        handled = await drain_once(_creator(returns="exec-9"), now=_now(), batch_size=10,
                                   hop_limit=3, authorize=_allow)

        assert handled == 1
        [row] = _deliveries()
        assert row.status == "delivered"
        assert row.execution_id == "exec-9"
        assert row.delivered_at is not None

    async def test_the_target_receives_the_envelope_as_its_input(self, isolated_db):
        _pending(hook=_hook(workflow_ref="ops/notify"), event_key="ev-1")
        creator = _recording_creator()

        await drain_once(creator, now=_now(), batch_size=10, hop_limit=3, authorize=_allow)

        namespace, workflow, payload = creator.calls[0]
        assert (namespace, workflow) == ("ops", "notify")
        assert payload["hook"] and payload["event"]

    async def test_a_transient_failure_backs_off_and_retries(self, isolated_db):
        _pending(hook=_hook(max_attempts=3), event_key="ev-1")

        await drain_once(_creator(raises=RuntimeError("db busy")), now=_now(), batch_size=10,
                         hop_limit=3, authorize=_allow)

        [row] = _deliveries()
        assert row.status == "pending"
        assert row.attempts == 1
        assert row.next_attempt_at > _now()
        assert "db busy" in row.last_error

    async def test_dead_letters_after_max_attempts(self, isolated_db):
        _pending(hook=_hook(max_attempts=1), event_key="ev-1")

        await drain_once(_creator(raises=RuntimeError("nope")), now=_now(), batch_size=10,
                         hop_limit=3, authorize=_allow)

        assert _deliveries()[0].status == "dead"

    async def test_a_revoked_principal_dead_letters_rather_than_bypassing(self, isolated_db):
        """Fire-time authorization: a permission removed after the hook was
        created must stop the delivery, not silently run it."""
        _pending(hook=_hook(workflow_ref="ops/notify"), event_key="ev-1")

        await drain_once(_creator(returns="x"), now=_now(), batch_size=10, hop_limit=3,
                         authorize=_deny)

        [row] = _deliveries()
        assert row.status == "dead"
        assert "permission" in row.last_error.lower()

    async def test_a_missing_target_dead_letters(self, isolated_db):
        _pending(hook=_hook(workflow_ref="ops/gone"), event_key="ev-1")

        await drain_once(_creator(raises=WorkflowNotFoundError("ops/gone")), now=_now(),
                         batch_size=10, hop_limit=3, authorize=_allow)

        assert _deliveries()[0].status == "dead"

    async def test_the_hop_guard_stops_a_loop(self, isolated_db):
        """Without this, `execution:*:*:completed` targeting a workflow is a
        fork bomb."""
        _pending(hook=_hook(), event_key="ev-1", payload_hop=3)

        await drain_once(_creator(returns="x"), now=_now(), batch_size=10, hop_limit=3,
                         authorize=_allow)

        [row] = _deliveries()
        assert row.status == "dead"
        assert "hop" in row.last_error.lower()

    async def test_deliveries_not_yet_due_are_left_alone(self, isolated_db):
        _pending(hook=_hook(), event_key="ev-1", next_attempt_at=_now() + timedelta(minutes=5))

        assert await drain_once(_creator(returns="x"), now=_now(), batch_size=10,
                                hop_limit=3, authorize=_allow) == 0
```

- [ ] **Step 2: fail → Step 3: implement.** Backoff is `2 ** attempts` seconds capped at 300. The drain claims a batch (`status="pending"` and `next_attempt_at` null-or-past, ordered by `created_at`, limited to `batch_size`).
- [ ] **Step 4: Green.**
- [ ] **Step 5: Wire into the scheduler loop** in `flux/server.py` inside the existing `dispatch_lock` block, in its own try/except like the sibling sweeps, passing `self._create_execution` and an `authorize` closure over `auth_service.is_authorized` with permission `workflow:{ns}:{wf}:run`. Skip entirely when `hooks.enabled` is false.
- [ ] **Step 6: Commit** `feat(hooks): scheduler-tick drain with backoff, dead-lettering and the hop guard`.

---

### Task 7: REST routes, schemas and permissions

**Files:**
- Create: `flux/api/hook_routes.py` (`HookRoutesMixin`, pattern: `flux/api/schedule_routes.py:39-73`)
- Modify: `flux/api/schemas.py` (`HookRequest`, `HookResponse`, `HookListResponse`, `HookDeliveryResponse`, `HookTestResponse`)
- Modify: `flux/server.py` (add the mixin to the `Server` bases at ~line 92 and call `self._register_hook_routes(...)` beside `self._register_schedule_routes(...)` at ~line 1617)
- Modify: `flux/security/auth_service.py:24-53` (`BUILT_IN_ROLES`: `operator` gains `"hook:*"`, `"hook:deliveries:read"`, `"hook:deliveries:retry"`; `viewer` gains `"hook:*:read"`, `"hook:deliveries:read"`)
- Test: `tests/flux/test_hook_routes.py`, `tests/security/test_hook_authz.py`

**Interfaces:**
- Routes and their permissions, exactly as the spec's API section: `POST /hooks` (`hook:*:create`), `GET /hooks` (`hook:*:read`), `GET /hooks/{name}` (`hook:{name}:read`), `PUT /hooks/{name}` (`hook:{name}:update`), `DELETE /hooks/{name}` (`hook:{name}:delete`), `POST /hooks/{name}/test` (`hook:{name}:update`), `GET /hooks/{name}/deliveries` (`hook:deliveries:read`), `POST /hooks/{name}/deliveries/{id}/retry` (`hook:deliveries:retry`).

- [ ] **Step 1: Failing route tests** (TestClient harness: `tests/flux/test_worker_release_route.py`): create returns 200 with the row; create with an invalid selector returns 400 naming the selector; create with a target the principal cannot run returns 403; duplicate name returns 409; get/list/update/delete round-trip; delete of a missing hook returns 404; `POST /hooks/{name}/test` starts the target with a synthetic envelope and returns `{"execution_id": ...}`; deliveries list returns rows newest-first; retry resets a `dead` row to `pending` with `attempts=0`.
- [ ] **Step 2: Failing authz tests** in `tests/security/test_hook_authz.py`: `viewer` may read hooks and deliveries but not create, update, delete or retry; `worker` may do none of it; `operator` may do all of it. (Pattern: `tests/security/test_schedule_execution_authz.py`.)
- [ ] **Step 3: fail → Step 4: implement → Step 5: green.** Create/update validate every selector through `validate_selector` and check the hook principal holds `workflow:{ns}:{wf}:run` on `workflow_ref` at write time (the spec's create-time half of fire-time authorization).
- [ ] **Step 6: Commit** `feat(hooks): REST surface, response models and role permissions`.

---

### Task 8: CLI, retention, docs and e2e

**Files:**
- Modify: `flux/cli.py` (`flux hook` group, pattern: the `schedule` group at `cli.py:1852-2100`)
- Modify: `flux/retention.py` (`_delete_batch` at `:114-156` — prune `hook_deliveries` in terminal statuses past the cutoff)
- Create: `docs/advanced-features/hooks.md`; modify `mkdocs.yml` nav
- Create: `tests/e2e/test_hooks.py`
- Modify: `pyproject.toml` (0.85.0)

- [ ] **Step 1: CLI tests** (CliRunner, pattern `tests/flux/test_cli.py::TestAgentStop`): `flux hook create NAME --on SELECTOR --workflow ns/name` posts the right body; `--on` is repeatable and all selectors reach the payload; `list`/`get` render `--format json`; `delete` calls DELETE; `test` prints the started execution id; `deliveries` renders status and attempts; a non-2xx response exits 1 with the server's message.
- [ ] **Step 2: fail → Step 3: implement → Step 4: green.**
- [ ] **Step 5: Retention** — add `HookDeliveryModel` rows in `delivered`/`dead` status older than the cutoff to the retention sweep, with a test asserting `pending` rows are never pruned regardless of age.
- [ ] **Step 6: E2E** `tests/e2e/test_hooks.py` (harness: `tests/e2e/test_approvals.py` for a gated workflow, plus a recorder workflow whose only task records its input): register both workflows; create a hook on `task:*:*:<gated task>:awaiting_approval` targeting the recorder; run the gated workflow; assert the recorder execution starts, its input is the envelope, and the delivery row moves to `delivered` with that `execution_id`. Second case: a hook targeting the *recorder itself* on `execution:*:*:completed` stops at the hop limit with a `dead` delivery rather than looping.
- [ ] **Step 7: Docs** `docs/advanced-features/hooks.md` — what a hook is, the selector grammar with the spec's examples, why the only action is a workflow, the permissions table, at-least-once + idempotency guidance, the hop guard, and the inline-execution caveat (deliveries enqueued by `wf.run()` are drained only when a server is running). Add to `mkdocs.yml` nav.
- [ ] **Step 8: Full gates, version bump, commit, PR** — `poetry run pytest tests/flux/ -q`, the e2e file, `pre-commit run --all-files` (cold mypy). PR body: spec + plan links, the slice boundary (paths 2/3 and `notify` are later), verification matrix.

---

## Self-review

- Spec coverage: entity/storage→T1, selector grammar→T2, matching index→T3, transactional outbox→T4, envelope+redaction+hop→T5, delivery semantics/drain/dead-letter/fire-time authz→T6, API+permissions→T7, CLI+retention+docs+e2e→T8. Declaration paths 2–3, `notify` wiring, ordering, console panels: explicitly out of slice 1. ✓
- No placeholders: every task carries real test code and real interfaces. ✓
- Type consistency: `HookEvent`, `HookIndexEntry`, `build_envelope`, `drain_once`, `enqueue` signatures match across T2–T8. ✓
