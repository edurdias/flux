"""The drain: turning a pending delivery row into a running workflow.

The outbox half guarantees a delivery is *recorded*; this half is where one
is actually made, and it is the only place a hook can start work. So the
tests below pin the four ways a delivery can end -- delivered, retried,
dead-lettered, or refused before it starts -- rather than the happy path
alone. The two refusals matter most: authorization is re-checked at fire
time (a hook created months ago must not outlive its principal's rights),
and the hop guard is what keeps a hook whose target re-triggers it from
being a fork bomb.

``drain_once`` takes its execution creator and its authorizer as arguments
precisely so these can run without a server: the callables here stand in for
``Server._create_execution`` and the auth service.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flux.config import Configuration
from flux.errors import WorkflowNotFoundError
from flux.hooks.drain import drain_once
from flux.hooks.registry import HookRegistry
from flux.models import DatabaseRepository, HookDeliveryModel, HookModel, RepositoryFactory
from tests.flux.test_scheduler_dispatch_lock import _cm, _run_one_scheduler_cycle

# Fixed and naive: SQLite hands datetimes back without a tzinfo, so a row
# read after the drain stays comparable to the `now` the drain was given.
_NOW = datetime(2026, 1, 1, 12, 0, 0)


def _now() -> datetime:
    return _NOW


def _hook(
    *,
    workflow_ref: str = "ops/notify",
    max_attempts: int = 5,
    name: str = "notify",
    principal: str = "p-1",
    enabled: bool = True,
) -> HookModel:
    registry = HookRegistry.create()
    hook = registry.create_hook(
        name=name,
        selectors=["execution:*:*:completed"],
        workflow_ref=workflow_ref,
        principal=principal,
        owner_ref="admin",
        max_attempts=max_attempts,
    )
    if enabled:
        return hook
    # Disabled after the fact, as an operator does it: the backlog under
    # test is exactly the deliveries enqueued while the hook was still on.
    return registry.update_hook(name, enabled=False)


def _envelope(hook: HookModel, event_key: str, hop: Any) -> dict:
    """What the enqueue stored: a whole envelope, not the pieces to rebuild one."""
    return {
        "hook": hook.name,
        "selector": "execution:*:*:completed",
        "delivery_id": f"d-{event_key}",
        "event_key": event_key,
        "attempt": 1,
        "hop": hop,
        "event": {
            "domain": "execution",
            "type": "completed",
            "execution_id": "parent-1",
            "workflow_namespace": "release",
            "workflow_name": "pipeline",
            "value": {"ok": True},
        },
    }


def _pending(
    *,
    hook: HookModel,
    event_key: str,
    payload_hop: Any = 0,
    next_attempt_at: datetime | None = None,
    created_at: datetime | None = None,
    payload: Any = None,
) -> None:
    with RepositoryFactory.create_repository().session() as session:
        session.add(
            HookDeliveryModel(
                hook_id=hook.id,
                event_key=event_key,
                payload=_envelope(hook, event_key, payload_hop) if payload is None else payload,
                status="pending",
                next_attempt_at=next_attempt_at,
                created_at=created_at or _NOW - timedelta(minutes=1),
            ),
        )
        session.commit()


def _deliveries() -> list[HookDeliveryModel]:
    with RepositoryFactory.create_repository().session() as session:
        return session.query(HookDeliveryModel).order_by(HookDeliveryModel.event_key).all()


def _creator(*, returns: str = "exec-1", raises: Exception | None = None):
    async def create_execution(
        namespace: str,
        workflow_name: str,
        input_data: Any,
        **identity: Any,
    ) -> str:
        if raises is not None:
            raise raises
        return returns

    return create_execution


class _RecordingCreator:
    def __init__(self, returns: str = "exec-1"):
        self.calls: list[tuple[str, str, Any]] = []
        # The identity the delivery runs as travels beside the input, so the
        # server can stamp it onto the execution it just started.
        self.identities: list[dict[str, Any]] = []
        self._returns = returns

    async def __call__(
        self,
        namespace: str,
        workflow_name: str,
        input_data: Any,
        **identity: Any,
    ) -> str:
        self.calls.append((namespace, workflow_name, input_data))
        self.identities.append(identity)
        return self._returns


def _recording_creator() -> _RecordingCreator:
    return _RecordingCreator()


async def _allow(principal: str, permission: str) -> bool:
    return True


async def _deny(principal: str, permission: str) -> bool:
    return False


class TestDrain:
    async def test_a_pending_delivery_starts_the_target_and_records_it(self, isolated_db):
        _pending(hook=_hook(workflow_ref="ops/notify"), event_key="ev-1")

        handled = await drain_once(
            _creator(returns="exec-9"),
            now=_now(),
            batch_size=10,
            hop_limit=3,
            authorize=_allow,
        )

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

        await drain_once(
            _creator(raises=RuntimeError("db busy")),
            now=_now(),
            batch_size=10,
            hop_limit=3,
            authorize=_allow,
        )

        [row] = _deliveries()
        assert row.status == "pending"
        assert row.attempts == 1
        assert row.next_attempt_at > _now()
        assert "db busy" in row.last_error

    async def test_dead_letters_after_max_attempts(self, isolated_db):
        _pending(hook=_hook(max_attempts=1), event_key="ev-1")

        await drain_once(
            _creator(raises=RuntimeError("nope")),
            now=_now(),
            batch_size=10,
            hop_limit=3,
            authorize=_allow,
        )

        assert _deliveries()[0].status == "dead"

    async def test_a_revoked_principal_dead_letters_rather_than_bypassing(self, isolated_db):
        """Fire-time authorization: a permission removed after the hook was
        created must stop the delivery, not silently run it."""
        _pending(hook=_hook(workflow_ref="ops/notify"), event_key="ev-1")

        await drain_once(
            _creator(returns="x"),
            now=_now(),
            batch_size=10,
            hop_limit=3,
            authorize=_deny,
        )

        [row] = _deliveries()
        assert row.status == "dead"
        assert "permission" in row.last_error.lower()

    async def test_a_missing_target_dead_letters(self, isolated_db):
        _pending(hook=_hook(workflow_ref="ops/gone"), event_key="ev-1")

        await drain_once(
            _creator(raises=WorkflowNotFoundError("ops/gone")),
            now=_now(),
            batch_size=10,
            hop_limit=3,
            authorize=_allow,
        )

        assert _deliveries()[0].status == "dead"

    async def test_the_hop_guard_stops_a_loop(self, isolated_db):
        """Without this, `execution:*:*:completed` targeting a workflow is a
        fork bomb."""
        _pending(hook=_hook(), event_key="ev-1", payload_hop=3)

        await drain_once(
            _creator(returns="x"),
            now=_now(),
            batch_size=10,
            hop_limit=3,
            authorize=_allow,
        )

        [row] = _deliveries()
        assert row.status == "dead"
        assert "hop" in row.last_error.lower()

    async def test_deliveries_not_yet_due_are_left_alone(self, isolated_db):
        _pending(hook=_hook(), event_key="ev-1", next_attempt_at=_now() + timedelta(minutes=5))

        assert (
            await drain_once(
                _creator(returns="x"),
                now=_now(),
                batch_size=10,
                hop_limit=3,
                authorize=_allow,
            )
            == 0
        )

    async def test_the_hop_guard_refuses_before_authorizing_or_creating(self, isolated_db):
        """A looping chain must cost nothing but the row update."""
        _pending(hook=_hook(), event_key="ev-1", payload_hop=9)
        creator = _recording_creator()

        async def _explode(principal: str, permission: str) -> bool:
            raise AssertionError("authorization must not be reached past the hop limit")

        await drain_once(creator, now=_now(), batch_size=10, hop_limit=3, authorize=_explode)

        assert creator.calls == []
        assert _deliveries()[0].status == "dead"

    async def test_a_payload_without_a_usable_hop_counts_as_the_first(self, isolated_db):
        """A hand-written or pre-hop delivery row fires rather than raising."""
        _pending(hook=_hook(), event_key="ev-1", payload_hop="not-a-number")

        await drain_once(
            _creator(returns="x"),
            now=_now(),
            batch_size=10,
            hop_limit=3,
            authorize=_allow,
        )

        assert _deliveries()[0].status == "delivered"

    async def test_a_negative_hop_in_the_payload_counts_as_the_first(self, isolated_db):
        """A stored hop below zero is a forgery, not a chain with room left:
        floored here so `-999 >= hop_limit` cannot answer False forever."""
        _pending(hook=_hook(), event_key="ev-1", payload_hop=-999)

        await drain_once(
            _creator(returns="x"),
            now=_now(),
            batch_size=10,
            hop_limit=0,
            authorize=_allow,
        )

        [row] = _deliveries()
        assert row.status == "dead"
        assert "hop" in row.last_error.lower()

    async def test_the_envelope_carries_the_attempt_being_made(self, isolated_db):
        """The drain re-reads the stored envelope and refreshes only `attempt`:
        the event data inside it was redacted at enqueue and is never rebuilt."""
        hook = _hook(max_attempts=3)
        _pending(hook=hook, event_key="ev-1")
        await drain_once(
            _creator(raises=RuntimeError("boom")),
            now=_now(),
            batch_size=10,
            hop_limit=3,
            authorize=_allow,
        )

        creator = _recording_creator()
        await drain_once(
            creator,
            now=_now() + timedelta(minutes=1),
            batch_size=10,
            hop_limit=3,
            authorize=_allow,
        )

        assert creator.calls[0][2]["attempt"] == 2
        assert creator.calls[0][2]["event"]["value"] == {"ok": True}

    async def test_a_batch_is_bounded_and_taken_oldest_first(self, isolated_db):
        hook = _hook()
        _pending(hook=hook, event_key="ev-new", created_at=_NOW - timedelta(minutes=1))
        _pending(hook=hook, event_key="ev-old", created_at=_NOW - timedelta(hours=1))
        creator = _recording_creator()

        handled = await drain_once(
            creator,
            now=_now(),
            batch_size=1,
            hop_limit=3,
            authorize=_allow,
        )

        assert handled == 1
        statuses = {row.event_key: row.status for row in _deliveries()}
        assert statuses == {"ev-old": "delivered", "ev-new": "pending"}

    async def test_a_settled_delivery_is_never_drained_twice(self, isolated_db):
        _pending(hook=_hook(), event_key="ev-1")
        await drain_once(
            _creator(returns="exec-9"),
            now=_now(),
            batch_size=10,
            hop_limit=3,
            authorize=_allow,
        )

        creator = _recording_creator()
        handled = await drain_once(
            creator,
            now=_now() + timedelta(hours=1),
            batch_size=10,
            hop_limit=3,
            authorize=_allow,
        )

        assert handled == 0
        assert creator.calls == []
        assert _deliveries()[0].execution_id == "exec-9"

    async def test_a_creator_writing_to_the_same_database_is_not_blocked(self, isolated_db):
        """The claimed rows stay locked while the target is created, and on
        the real path that creation is another session against this same
        database -- so the two must not contend."""
        _pending(hook=_hook(), event_key="ev-1")

        async def create_execution(
            namespace: str,
            workflow_name: str,
            input_data: Any,
            **identity: Any,
        ) -> str:
            with RepositoryFactory.create_repository().session() as session:
                session.add(
                    HookModel(
                        name="written-mid-drain",
                        selectors=[],
                        workflow_ref="ops/other",
                        principal="p-2",
                        owner_ref="admin",
                    ),
                )
                session.commit()
            return "exec-7"

        handled = await drain_once(
            create_execution,
            now=_now(),
            batch_size=10,
            hop_limit=3,
            authorize=_allow,
        )

        assert handled == 1
        assert _deliveries()[0].execution_id == "exec-7"

    async def test_a_row_the_drain_cannot_read_does_not_take_the_batch_down(self, isolated_db):
        """A poison row must cost itself and nothing else.

        Left to escape the loop, it would discard the batch's commit —
        including rows whose executions already started — and `created_at`
        ordering would re-claim it, and re-fire them, on every tick after.
        """
        hook = _hook()
        _pending(
            hook=hook,
            event_key="ev-a-poison",
            payload=["not", "an", "object"],
            created_at=_NOW - timedelta(hours=1),
        )
        _pending(hook=hook, event_key="ev-b-good")

        handled = await drain_once(
            _creator(returns="exec-9"),
            now=_now(),
            batch_size=10,
            hop_limit=3,
            authorize=_allow,
        )

        assert handled == 2
        poison, good = _deliveries()
        assert poison.status == "dead" and poison.last_error
        assert good.status == "delivered" and good.execution_id == "exec-9"

    async def test_a_disabled_hook_dead_letters_its_backlog(self, isolated_db):
        """`enabled=false` is the stop button; a backlog that keeps draining
        past it is not one."""
        _pending(hook=_hook(enabled=False), event_key="ev-1")
        creator = _recording_creator()

        await drain_once(creator, now=_now(), batch_size=10, hop_limit=3, authorize=_allow)

        [row] = _deliveries()
        assert row.status == "dead"
        assert "disabled" in row.last_error.lower()
        assert creator.calls == []

    async def test_the_last_permitted_hop_still_fires(self, isolated_db):
        """The limit counts links: at 3, hops 0, 1 and 2 are all deliverable."""
        _pending(hook=_hook(), event_key="ev-1", payload_hop=2)

        await drain_once(
            _creator(returns="exec-9"),
            now=_now(),
            batch_size=10,
            hop_limit=3,
            authorize=_allow,
        )

        assert _deliveries()[0].status == "delivered"

    async def test_the_creator_is_told_which_principal_the_delivery_runs_as(self, isolated_db):
        """The started execution has to carry the hook's identity, so the
        identity has to reach the creator."""
        _pending(hook=_hook(name="nightly", principal="p-42"), event_key="ev-1")
        creator = _recording_creator()

        await drain_once(creator, now=_now(), batch_size=10, hop_limit=3, authorize=_allow)

        assert creator.identities[0] == {
            "principal": "p-42",
            "on_behalf_of": "hook:nightly",
        }


@pytest.fixture
def real_config(tmp_path):
    """A real Configuration over a throwaway database.

    The scheduler-loop tests below read ``[flux.hooks]`` for real: against
    ``isolated_db``'s MagicMock every attribute is truthy, so the disabled
    branch could never be exercised and the batch size would arrive as a
    Mock. The autouse ``_seed_required_config`` fixture resets the singleton
    at teardown.
    """
    Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'drain.db'}")
    DatabaseRepository._engines.clear()
    yield
    DatabaseRepository._engines.clear()


def _dispatching_manager() -> MagicMock:
    manager = MagicMock()
    manager.dispatch_lock = _cm(True)
    manager.get_due_schedules.return_value = []
    return manager


def _auth_on() -> None:
    Configuration.get().override(
        security={
            "auth": {"enabled": True, "api_keys": {"enabled": True}},
            "execution_token_secret": "test-execution-token-secret",
        },
    )


def _register_target(
    namespace: str = "ops",
    name: str = "notify",
    task_names: tuple[str, ...] = ("send",),
) -> None:
    """Put the hook's target in the catalog.

    ``task_names`` is what makes the deeper authorization bite: run rights
    alone do not cover a workflow's tasks.
    """
    from flux.catalogs import WorkflowCatalog, WorkflowInfo

    WorkflowCatalog.create().save(
        [
            WorkflowInfo(
                id="",
                name=name,
                namespace=namespace,
                imports=[],
                source=b"",
                metadata={"task_names": list(task_names)},
            ),
        ],
    )


def _principal(
    server,
    subject: str,
    permissions: list[str] | None = None,
    *,
    enabled: bool = True,
    banned: bool = False,
):
    from flux.security.models import RoleModel
    from flux.security.principals import PrincipalRegistry

    registry = PrincipalRegistry(session_factory=server._get_db_session)
    principal = registry.create(
        type="service_account",
        subject=subject,
        external_issuer="flux",
        enabled=enabled,
    )
    if permissions is not None:
        role_name = f"role-{subject}"
        with RepositoryFactory.create_repository().session() as session:
            session.add(RoleModel(name=role_name, permissions=permissions))
            session.commit()
        registry.assign_role(principal.id, role_name)
    if banned:
        registry.set_banned(principal.id, True)
    return principal


class TestSchedulerWiring:
    """The drain only exists if the tick runs it, under the same lock."""

    async def test_the_tick_drains_with_the_configured_bounds(self, real_config):
        from flux.server import Server

        Configuration.get().override(
            hooks={"enabled": True, "drain_batch_size": 7, "hop_limit": 2},
        )
        server = Server("127.0.0.1", 0)

        with patch("flux.server.drain_once", new_callable=AsyncMock, return_value=1) as drain:
            await _run_one_scheduler_cycle(server, _dispatching_manager())

        assert drain.await_count == 1
        assert drain.await_args.args[0] == server._create_hook_execution
        assert drain.await_args.kwargs["batch_size"] == 7
        assert drain.await_args.kwargs["hop_limit"] == 2
        assert drain.await_args.kwargs["authorize"] == server._authorize_hook

    async def test_hooks_disabled_drains_nothing(self, real_config):
        from flux.server import Server

        Configuration.get().override(hooks={"enabled": False})
        server = Server("127.0.0.1", 0)

        with patch("flux.server.drain_once", new_callable=AsyncMock) as drain:
            await _run_one_scheduler_cycle(server, _dispatching_manager())

        drain.assert_not_awaited()

    async def test_a_failing_drain_does_not_take_the_tick_down(self, real_config):
        """One sweep's failure must not cost the others their cycle."""
        from flux.server import Server

        Configuration.get().override(hooks={"enabled": True})
        server = Server("127.0.0.1", 0)
        server._purge_join_tokens = MagicMock()

        with patch(
            "flux.server.drain_once",
            new_callable=AsyncMock,
            side_effect=RuntimeError("drain exploded"),
        ):
            await _run_one_scheduler_cycle(server, _dispatching_manager())

        # The sweep that runs after the drain still got its turn.
        server._purge_join_tokens.assert_called_once()

    async def test_authorization_passes_when_auth_is_disabled(self, real_config):
        from flux.server import Server

        Configuration.get().override(security={"auth": {"enabled": False}})
        server = Server("127.0.0.1", 0)

        assert await server._authorize_hook("p-1", "workflow:ops:notify:run") is True

    async def test_an_unknown_principal_is_refused(self, real_config):
        from flux.server import Server

        _auth_on()
        server = Server("127.0.0.1", 0)

        assert await server._authorize_hook("nobody", "workflow:ops:notify:run") is False

    async def test_a_principal_holding_the_rights_is_authorized(self, real_config):
        """The affirmative path: without it, a defect in the identity built
        here would dead-letter every delivery in production as 'lacks
        permission' and no test would notice."""
        from flux.server import Server

        _auth_on()
        server = Server("127.0.0.1", 0)
        _register_target()
        principal = _principal(
            server,
            "hook-sa",
            ["workflow:ops:notify:run", "workflow:ops:notify:task:send:execute"],
        )

        assert await server._authorize_hook(principal.subject, "workflow:ops:notify:run") is True

    async def test_run_rights_alone_do_not_cover_the_target_s_tasks(self, real_config):
        """The same depth the schedule path applies: two doors into the same
        room must not need different keys."""
        from flux.server import Server

        _auth_on()
        server = Server("127.0.0.1", 0)
        _register_target()
        principal = _principal(server, "hook-sa", ["workflow:ops:notify:run"])

        assert await server._authorize_hook(principal.subject, "workflow:ops:notify:run") is False

    async def test_a_disabled_or_banned_principal_is_refused(self, real_config):
        from flux.server import Server

        _auth_on()
        server = Server("127.0.0.1", 0)
        _register_target()
        disabled = _principal(server, "off-sa", ["*"], enabled=False)
        banned = _principal(server, "banned-sa", ["*"], banned=True)

        assert await server._authorize_hook(disabled.subject, "workflow:ops:notify:run") is False
        assert await server._authorize_hook(banned.subject, "workflow:ops:notify:run") is False

    async def test_a_vanished_target_is_not_reported_as_a_permission_problem(self, real_config):
        """The catalog miss propagates, so the drain dead-letters it with the
        truthful reason instead of blaming the principal."""
        from flux.server import Server

        _auth_on()
        server = Server("127.0.0.1", 0)
        principal = _principal(server, "hook-sa", ["*"])

        with pytest.raises(WorkflowNotFoundError):
            await server._authorize_hook(principal.subject, "workflow:ops:gone:run")

    async def test_a_started_execution_carries_the_hook_s_identity(self, real_config):
        """Without the token a hook-started workflow calling back is
        anonymous, and every task-level check it meets fails closed."""
        import jwt

        from flux.models import ExecutionContextModel
        from flux.server import Server

        _auth_on()
        server = Server("127.0.0.1", 0)
        _register_target()
        principal = _principal(server, "hook-sa", ["*"])

        execution_id = await server._create_hook_execution(
            "ops",
            "notify",
            {"hook": "nightly", "hop": 0},
            principal=principal.subject,
            on_behalf_of="hook:nightly",
        )

        with RepositoryFactory.create_repository().session() as session:
            row = session.get(ExecutionContextModel, execution_id)
            assert row.scheduling_subject == "hook-sa"
            assert row.scheduling_principal_issuer == "flux"
            claims = jwt.decode(
                row.exec_token,
                "test-execution-token-secret",
                algorithms=["HS256"],
                issuer="flux-server",
            )

        assert claims["sub"] == "hook-sa"
        assert claims["exec_id"] == execution_id
        assert claims["act"]["on_behalf_of"] == "hook:nightly"

    async def test_an_unstamped_execution_is_not_re_fired(self, real_config):
        """The execution already exists: a raise here would read as transient
        and start a second one next tick."""
        from flux.models import ExecutionContextModel
        from flux.server import Server

        _auth_on()
        server = Server("127.0.0.1", 0)
        _register_target()

        with patch(
            "flux.security.execution_token.mint_execution_token",
            side_effect=RuntimeError("no secret"),
        ):
            execution_id = await server._create_hook_execution(
                "ops",
                "notify",
                {"hook": "nightly"},
                principal=_principal(server, "hook-sa", ["*"]).subject,
                on_behalf_of="hook:nightly",
            )

        with RepositoryFactory.create_repository().session() as session:
            row = session.get(ExecutionContextModel, execution_id)
            # Degraded, deliberately: unstamped and running, not duplicated.
            assert row is not None
            assert row.exec_token is None
