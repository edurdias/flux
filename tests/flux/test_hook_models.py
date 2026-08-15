from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from flux.models import HookDeliveryModel, HookModel, RepositoryFactory


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
            session.add(
                HookModel(
                    name="dup",
                    selectors=[],
                    workflow_ref="a/b",
                    principal_id="p",
                    owner_type="user",
                    owner_ref="admin",
                ),
            )
            session.commit()
        with pytest.raises(IntegrityError):
            with repo.session() as session:
                session.add(
                    HookModel(
                        name="dup",
                        selectors=[],
                        workflow_ref="a/b",
                        principal_id="p",
                        owner_type="user",
                        owner_ref="admin",
                    ),
                )
                session.commit()

    def test_one_delivery_per_hook_and_event(self, isolated_db):
        """The enqueue is idempotent by construction: replays and retries
        cannot fan a single event into duplicate deliveries."""
        repo = RepositoryFactory.create_repository()
        with repo.session() as session:
            hook = HookModel(
                name="h",
                selectors=[],
                workflow_ref="a/b",
                principal_id="p",
                owner_type="user",
                owner_ref="admin",
            )
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
            hook = HookModel(
                name="h",
                selectors=[],
                workflow_ref="a/b",
                principal_id="p",
                owner_type="user",
                owner_ref="admin",
            )
            session.add(hook)
            session.commit()
            session.add(HookDeliveryModel(hook_id=hook.id, event_key="e-1", payload={}))
            session.commit()
            session.delete(hook)
            session.commit()
            assert session.query(HookDeliveryModel).count() == 0
