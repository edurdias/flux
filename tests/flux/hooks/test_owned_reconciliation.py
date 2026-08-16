"""Owner-scoped hook reconciliation: create-or-replace-by-derived-name, and
delete-by-owner. The property under test throughout is that a hook still
declared across a reconcile call keeps its row (and delivery history);
only a hook that disappeared from the declaration is deleted."""

from __future__ import annotations

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
