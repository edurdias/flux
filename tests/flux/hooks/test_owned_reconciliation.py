"""Owner-scoped hook reconciliation: create-or-replace-by-derived-name, and
delete-by-owner. The property under test throughout is that a hook still
declared across a reconcile call keeps its row (and delivery history);
only a hook that disappeared from the declaration is deleted."""

from __future__ import annotations

import pytest

from flux.errors import HookNameConflictError
from flux.hooks.registry import HookRegistry
from flux.models import HookDeliveryModel, RepositoryFactory


def _spec(on: str, workflow: str = "ops/notify", principal: str = "notifier", **overrides):
    spec = {"on": on, "workflow": workflow, "principal": principal, "name": None, "max_attempts": 5}
    spec.update(overrides)
    return spec


class TestReconcileOwnedHooks:
    def test_first_reconcile_creates_rows_with_derived_names(self, isolated_db):
        registry = HookRegistry.create()

        created = registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[_spec("execution:release:pipeline:failed")],
        )

        assert len(created) == 1
        assert created[0].owner_type == "workflow"
        assert created[0].owner_ref == "release/pipeline"
        assert created[0].name  # non-empty, derived
        assert registry.get_hook(created[0].name).selectors == ["execution:release:pipeline:failed"]

    def test_multiple_specs_get_distinct_derived_names(self, isolated_db):
        registry = HookRegistry.create()

        created = registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[
                _spec("execution:release:pipeline:failed"),
                _spec("execution:release:pipeline:completed"),
            ],
        )

        names = {row.name for row in created}
        assert len(names) == 2

    def test_an_explicit_name_is_used_instead_of_a_derived_one(self, isolated_db):
        registry = HookRegistry.create()

        created = registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[_spec("execution:release:pipeline:failed", name="my-stable-name")],
        )

        assert created[0].name == "my-stable-name"

    def test_reconciling_again_with_the_same_spec_updates_not_recreates(self, isolated_db):
        registry = HookRegistry.create()
        first = registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[_spec("execution:release:pipeline:failed")],
        )
        hook_id = first[0].id

        second = registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[_spec("execution:release:pipeline:failed", max_attempts=9)],
        )

        assert second[0].id == hook_id
        assert second[0].max_attempts == 9

    def test_reconciling_preserves_delivery_history_of_a_still_declared_hook(self, isolated_db):
        registry = HookRegistry.create()
        created = registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[_spec("execution:release:pipeline:failed")],
        )
        hook_id = created[0].id
        with RepositoryFactory.create_repository().session() as session:
            session.add(
                HookDeliveryModel(
                    hook_id=hook_id,
                    event_key="exec-1:ev-1",
                    payload={},
                    status="delivered",
                ),
            )
            session.commit()

        registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[_spec("execution:release:pipeline:failed", max_attempts=2)],
        )

        with RepositoryFactory.create_repository().session() as session:
            assert session.query(HookDeliveryModel).filter_by(hook_id=hook_id).count() == 1

    def test_a_spec_removed_from_the_declaration_deletes_its_row_and_deliveries(self, isolated_db):
        registry = HookRegistry.create()
        created = registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[
                _spec("execution:release:pipeline:failed"),
                _spec("execution:release:pipeline:completed"),
            ],
        )
        removed_id = created[1].id
        with RepositoryFactory.create_repository().session() as session:
            session.add(
                HookDeliveryModel(
                    hook_id=removed_id,
                    event_key="exec-1:ev-1",
                    payload={},
                    status="delivered",
                ),
            )
            session.commit()

        registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[_spec("execution:release:pipeline:failed")],
        )

        # HookModel has no __eq__ override, so comparing full ORM objects
        # across two separately-sessioned list_hooks() calls would compare
        # Python identity, not row content -- compare ids instead.
        remaining_ids = {row.id for row in registry.list_hooks()}
        assert removed_id not in remaining_ids
        with RepositoryFactory.create_repository().session() as session:
            assert session.query(HookDeliveryModel).filter_by(hook_id=removed_id).count() == 0

    def test_reconciling_with_an_empty_spec_list_deletes_every_owned_hook(self, isolated_db):
        registry = HookRegistry.create()
        registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[_spec("execution:release:pipeline:failed")],
        )

        registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[],
        )

        assert registry.list_owned_hooks(owner_type="workflow", owner_ref="release/pipeline") == []

    def test_reconcile_never_touches_a_different_owners_hooks(self, isolated_db):
        registry = HookRegistry.create()
        registry.create_hook(
            name="user-made",
            selectors=["execution:*:*:failed"],
            workflow_ref="ops/incident",
            principal="p",
            owner_type="user",
            owner_ref="admin",
        )

        registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[_spec("execution:release:pipeline:failed")],
        )
        registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[],
        )

        assert registry.get_hook("user-made") is not None


class TestNameDerivationIsContentStableNotPositional:
    """The derived name must track a spec's content, not its index in the
    ``specs`` list -- position-keying let removing or reordering an earlier
    spec silently hand a still-declared spec's row (and delivery history) to
    whatever spec happened to land on that index's name."""

    def test_removing_the_first_of_two_specs_keeps_the_seconds_identity(self, isolated_db):
        registry = HookRegistry.create()
        created = registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[
                _spec("execution:release:pipeline:failed"),
                _spec("execution:release:pipeline:completed"),
            ],
        )
        first_id, second_id = created[0].id, created[1].id
        with RepositoryFactory.create_repository().session() as session:
            session.add(
                HookDeliveryModel(
                    hook_id=second_id,
                    event_key="exec-1:ev-1",
                    payload={},
                    status="delivered",
                ),
            )
            session.commit()

        # Reconcile again with only the SECOND spec -- the first disappeared.
        after = registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[_spec("execution:release:pipeline:completed")],
        )

        assert len(after) == 1
        assert after[0].id == second_id
        assert after[0].id != first_id
        with RepositoryFactory.create_repository().session() as session:
            assert session.query(HookDeliveryModel).filter_by(hook_id=second_id).count() == 1

    def test_reordering_two_specs_does_not_swap_hook_identities(self, isolated_db):
        registry = HookRegistry.create()
        created = registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[
                _spec("execution:release:pipeline:failed"),
                _spec("execution:release:pipeline:completed"),
            ],
        )
        id_by_selector = {row.selectors[0]: row.id for row in created}

        # Same two specs, reversed order -- must not delete+recreate or swap.
        after = registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[
                _spec("execution:release:pipeline:completed"),
                _spec("execution:release:pipeline:failed"),
            ],
        )

        after_by_selector = {row.selectors[0]: row.id for row in after}
        assert after_by_selector == id_by_selector


class TestNameDerivationIsOwnerQualified:
    """The derived name must incorporate the full owner (type + ref), not
    just ``owner_ref``'s trailing path segment -- otherwise two owners whose
    ref happens to share a trailing segment derive the identical name and
    collide on ``HookModel.name``'s global uniqueness."""

    def test_two_workflow_namespaces_with_the_same_workflow_name_derive_distinct_names(
        self,
        isolated_db,
    ):
        registry = HookRegistry.create()

        release_hooks = registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[_spec("execution:release:pipeline:failed", workflow="release/notify")],
        )
        ops2_hooks = registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="ops2/pipeline",
            specs=[_spec("execution:ops2:pipeline:failed", workflow="ops2/notify")],
        )

        assert release_hooks[0].name != ops2_hooks[0].name
        assert registry.get_hook(release_hooks[0].name).owner_ref == "release/pipeline"
        assert registry.get_hook(ops2_hooks[0].name).owner_ref == "ops2/pipeline"

    def test_a_workflow_and_an_agent_sharing_a_name_derive_distinct_names(self, isolated_db):
        registry = HookRegistry.create()

        workflow_hooks = registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/helper",
            specs=[_spec("execution:release:helper:failed", workflow="release/notify")],
        )
        agent_hooks = registry.reconcile_owned_hooks(
            owner_type="agent",
            owner_ref="helper",
            specs=[_spec("execution:agents:agent_chat:completed", workflow="agents/notify")],
        )

        assert workflow_hooks[0].name != agent_hooks[0].name
        assert registry.get_hook(workflow_hooks[0].name).owner_type == "workflow"
        assert registry.get_hook(agent_hooks[0].name).owner_type == "agent"


class TestNameConflictSurfacesACleanErrorType:
    def test_a_genuine_name_collision_raises_hook_name_conflict_not_integrity_error(
        self,
        isolated_db,
    ):
        registry = HookRegistry.create()
        registry.create_hook(
            name="already-taken",
            selectors=["execution:*:*:failed"],
            workflow_ref="ops/incident",
            principal="p",
            owner_type="user",
            owner_ref="admin",
        )

        with pytest.raises(HookNameConflictError) as exc_info:
            registry.reconcile_owned_hooks(
                owner_type="workflow",
                owner_ref="release/pipeline",
                specs=[_spec("execution:release:pipeline:failed", name="already-taken")],
            )

        assert "already-taken" in str(exc_info.value)
        # The conflicting create must not leave a half-applied reconcile
        # behind: no row for this owner should have been committed.
        assert registry.list_owned_hooks(owner_type="workflow", owner_ref="release/pipeline") == []


class TestDeleteOwnedHooks:
    def test_deletes_every_hook_the_owner_declared(self, isolated_db):
        registry = HookRegistry.create()
        registry.reconcile_owned_hooks(
            owner_type="workflow",
            owner_ref="release/pipeline",
            specs=[
                _spec("execution:release:pipeline:failed"),
                _spec("execution:release:pipeline:completed"),
            ],
        )

        removed = registry.delete_owned_hooks(owner_type="workflow", owner_ref="release/pipeline")

        assert removed == 2
        assert registry.list_owned_hooks(owner_type="workflow", owner_ref="release/pipeline") == []

    def test_is_a_no_op_for_an_owner_with_no_hooks(self, isolated_db):
        registry = HookRegistry.create()

        assert registry.delete_owned_hooks(owner_type="workflow", owner_ref="release/none") == 0
