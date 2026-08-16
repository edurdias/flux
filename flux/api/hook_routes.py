"""Outbound hook routes (`/hooks*`).

Part of the ``flux.api`` route modules extracted from ``flux/server.py``. The
routes are defined inside a mixin method so handler closures keep their access
to ``self`` (the ``Server`` instance) and the shared per-app dependencies.

The operator surface over `flux/hooks/`: CRUD on the subscriptions, a
synthetic test fire, and the two deliveries endpoints an on-call operator
needs when something did not arrive.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from sqlalchemy.exc import IntegrityError

from flux.catalogs import WorkflowCatalog, resolve_workflow_ref
from flux.errors import HookNotFoundError, WorkflowNotFoundError
from flux.hooks.envelope import build_envelope
from flux.hooks.registry import HookIndexEntry, HookRegistry
from flux.hooks.selectors import HookEvent, validate_selector
from flux.models import HookDeliveryModel, HookModel, RepositoryFactory
from flux.security.dependencies import get_identity, require_permission
from flux.security.identity import FluxIdentity
from flux.utils import get_logger
from flux.api.schemas import (
    HookDeliveryResponse,
    HookListResponse,
    HookRequest,
    HookResponse,
    HookTestResponse,
    HookUpdateRequest,
)

logger = get_logger(__name__)


if TYPE_CHECKING:
    from flux.server import Server


# Stand-ins for the wildcard segments of the selector being tested. A
# synthetic event has to be concrete, and these read as obviously synthetic
# in the started execution's input.
_SYNTHETIC_SEGMENTS = {
    "execution": ("test_namespace", "test_workflow", "completed"),
    "task": ("test_namespace", "test_workflow", "test_task", "completed"),
}


def _hook_model_to_response(hook: HookModel) -> HookResponse:
    """Convert HookModel to HookResponse"""
    return HookResponse(
        id=hook.id,
        name=hook.name,
        enabled=hook.enabled,
        selectors=list(hook.selectors or []),
        action=hook.action,
        workflow_ref=hook.workflow_ref,
        principal=hook.principal,
        owner_type=hook.owner_type,
        owner_ref=hook.owner_ref,
        max_attempts=hook.max_attempts,
        created_by=hook.created_by,
        created_at=hook.created_at.isoformat(),
        updated_at=hook.updated_at.isoformat(),
    )


def _delivery_model_to_response(delivery: HookDeliveryModel) -> HookDeliveryResponse:
    """Convert HookDeliveryModel to HookDeliveryResponse"""
    return HookDeliveryResponse(
        id=delivery.id,
        hook_id=delivery.hook_id,
        event_key=delivery.event_key,
        status=delivery.status,
        attempts=delivery.attempts,
        execution_id=delivery.execution_id,
        next_attempt_at=delivery.next_attempt_at.isoformat() if delivery.next_attempt_at else None,
        last_error=delivery.last_error,
        created_at=delivery.created_at.isoformat(),
        delivered_at=delivery.delivered_at.isoformat() if delivery.delivered_at else None,
        payload=delivery.payload,
    )


def _synthetic_event(hook: HookModel) -> tuple[str, HookEvent]:
    """A concrete event matching the hook's first selector, for the test fire.

    Built from the selector rather than from a fixed template so the envelope
    the operator inspects has the shape *this* hook will actually receive:
    wildcards are filled in, concrete segments are kept.
    """
    selector = (list(hook.selectors or []) or ["execution:*"])[0]
    parts = selector.split(":")
    domain = parts[0] if parts[0] in _SYNTHETIC_SEGMENTS else "execution"

    segments = []
    for index, placeholder in enumerate(_SYNTHETIC_SEGMENTS[domain], start=1):
        value = parts[index] if index < len(parts) else "*"
        segments.append(placeholder if value == "*" else value)

    return selector, HookEvent(
        domain=domain,
        key=":".join([domain, *segments]),
        execution_id=f"hook-test-{uuid4().hex}",
        workflow_namespace=segments[0],
        workflow_name=segments[1],
        event_id=f"hook-test-{uuid4().hex}",
        type=segments[-1],
        task_name=segments[2] if domain == "task" else None,
        task_call_id=f"hook-test-{uuid4().hex}" if domain == "task" else None,
        value={"synthetic": True, "hook": hook.name},
        occurred_at=datetime.now(timezone.utc).isoformat(),
    )


class HookRoutesMixin:
    def _register_hook_routes(  # type: ignore[misc]
        self: Server,
        api,
        *,
        auth_config,
        auth_service,
        principal_registry,
        limiter,
    ):
        def _get_hook(name: str) -> HookModel:
            try:
                return HookRegistry.create().get_hook(name)
            except HookNotFoundError as e:
                raise HTTPException(status_code=404, detail=str(e))

        async def _require_hook_permission(identity: FluxIdentity, permission: str) -> None:
            """The per-name half of the hook grammar.

            ``require_permission`` can only carry a fixed string, so routes
            addressing one hook resolve ``hook:<name>:<verb>`` here instead.
            """
            if auth_service is None or not auth_config.enabled:
                return
            if not await auth_service.is_authorized(identity, permission):
                raise HTTPException(
                    status_code=403,
                    detail=f"Permission denied: requires '{permission}'",
                )

        def _validate_selectors(selectors: list[str]) -> None:
            for selector in selectors:
                try:
                    validate_selector(selector)
                except ValueError as e:
                    raise HTTPException(status_code=400, detail=str(e))

        def _resolve_target(workflow_ref: str) -> tuple[str, str]:
            try:
                return resolve_workflow_ref(workflow_ref)
            except ValueError as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid workflow reference '{workflow_ref}': {e}",
                )

        def _require_registered(
            namespace: str,
            workflow_name: str,
            *,
            status_code: int,
            detail: str,
        ) -> None:
            """The target must be in the catalog, whether or not auth is on.

            Run authorization is derived from the catalog entry — task-level
            and nested-workflow requirements come from its metadata — so a
            missing target must fail closed rather than be authorized against
            nothing.
            """
            try:
                WorkflowCatalog.create().get(namespace, workflow_name)
            except WorkflowNotFoundError:
                raise HTTPException(status_code=status_code, detail=detail)

        def _require_bindable_principal(principal: str) -> None:
            """A usable service account, not merely a subject that resolves.

            A hook names its principal by *subject*, as a schedule names its
            service account and as the permission grammar and the principals
            API do. Each way of being unusable gets its own answer, because
            they have different fixes: a subject nobody issued is a typo, a
            human principal is the wrong kind of identity for unattended
            work, and a disabled one is a state someone can restore. Reporting
            any of them as "lacks permission" sends an operator hunting for a
            grant that was never the issue.
            """
            found = principal_registry.find(principal, "flux") if principal_registry else None
            if found is None or getattr(found, "type", None) != "service_account":
                raise HTTPException(
                    status_code=400,
                    detail=f"Service account '{principal}' not found",
                )
            if not getattr(found, "enabled", True):
                raise HTTPException(
                    status_code=400,
                    detail=f"Service account '{principal}' is disabled",
                )
            if getattr(found, "banned", False):
                raise HTTPException(
                    status_code=400,
                    detail=f"Service account '{principal}' is banned",
                )

        async def _require_may_fire_as(identity: FluxIdentity, principal: str) -> None:
            """**Any route that can cause an execution to run as the hook's
            principal requires the right to borrow it; disabling and deleting
            never can.**

            That sentence is the whole authorization model for this file, and
            a new route belongs on one side of it or the other. A hook fires
            its target under the bound principal's roles with an execution
            token minted for it, so ``hook:*`` — which the built-in operator
            role carries — must not decide which identity runs what. Naming
            the principal is only the most obvious way to reach that: today
            create, rebind, re-aim (``workflow_ref``/``selectors``), re-enable,
            test-fire and re-queuing a dead delivery all do, and all take
            ``principal:<subject>:impersonate``, the same grant and grammar as
            a schedule's service account. Reads, ``enabled=false`` and
            ``DELETE`` do not, because a stop must never need a permission the
            emergency it is for might have taken away.

            With auth off there is no principal to resolve and nothing ever
            dereferences the stored value, so the whole check stands down —
            again as the schedule path does.
            """
            if not auth_config.enabled or auth_service is None:
                return
            _require_bindable_principal(principal)
            required = f"principal:{principal}:impersonate"
            if not await auth_service.is_authorized(identity, required):
                raise HTTPException(
                    status_code=403,
                    detail=f"Permission denied: requires '{required}'",
                )

        async def _require_principal_can_run(
            principal: str,
            namespace: str,
            workflow_name: str,
        ) -> None:
            """The create-time half of fire-time authorization.

            The drain re-checks this before every delivery, so a hook whose
            principal cannot run its target is not a security hole — it is a
            hook that can only ever dead-letter. Refusing it here is what
            turns a 3am silent non-delivery into an error at the door. On the
            test fire it is the security check too: that route really does
            start the target as this principal.
            """
            permission = f"workflow:{namespace}:{workflow_name}:run"
            if not await self._authorize_hook(principal, permission):
                raise HTTPException(
                    status_code=403,
                    detail=f"Principal '{principal}' lacks permission '{permission}'",
                )

        async def _require_runnable_target(principal: str, workflow_ref: str) -> None:
            """A hook's (principal, target) pair as a client supplies it."""
            namespace, workflow_name = _resolve_target(workflow_ref)
            _require_registered(
                namespace,
                workflow_name,
                status_code=404,
                detail=f"Workflow '{workflow_ref}' not found",
            )
            await _require_principal_can_run(principal, namespace, workflow_name)

        @api.post("/hooks", response_model=HookResponse)
        async def create_hook(
            request: HookRequest,
            identity: FluxIdentity = Depends(require_permission("hook:*:create")),
        ):
            """Create an outbound hook."""
            logger.info(f"Creating hook '{request.name}' targeting '{request.workflow_ref}'")

            _validate_selectors(request.selectors)
            # Whose rights the hook fires under is settled before anything is
            # asked about the target: a caller who may not borrow this
            # principal learns nothing about the catalog from trying.
            await _require_may_fire_as(identity, request.principal)
            await _require_runnable_target(request.principal, request.workflow_ref)

            try:
                hook = HookRegistry.create().create_hook(
                    name=request.name,
                    selectors=request.selectors,
                    workflow_ref=request.workflow_ref,
                    principal=request.principal,
                    owner_type="user",
                    owner_ref=identity.subject,
                    max_attempts=request.max_attempts,
                    created_by=identity.subject,
                )
            except IntegrityError:
                # The unique index on `name` is the arbiter, not a prior read:
                # two concurrent creates of the same name both pass a check
                # and only one passes the constraint.
                raise HTTPException(
                    status_code=409,
                    detail=f"Hook '{request.name}' already exists",
                )

            logger.info(f"Successfully created hook '{hook.name}' with ID '{hook.id}'")
            return _hook_model_to_response(hook)

        @api.get("/hooks", response_model=HookListResponse)
        async def list_hooks(
            enabled_only: bool = False,
            identity: FluxIdentity = Depends(require_permission("hook:*:read")),
        ):
            """List hooks, optionally only the enabled ones."""
            hooks = HookRegistry.create().list_hooks(enabled_only=enabled_only)
            responses = [_hook_model_to_response(hook) for hook in hooks]
            return HookListResponse(hooks=responses, total=len(responses))

        @api.get("/hooks/{name}", response_model=HookResponse)
        async def get_hook(
            name: str,
            identity: FluxIdentity = Depends(get_identity),
        ):
            """Get a single hook by name."""
            await _require_hook_permission(identity, f"hook:{name}:read")
            return _hook_model_to_response(_get_hook(name))

        @api.put("/hooks/{name}", response_model=HookResponse)
        async def update_hook(
            name: str,
            request: HookUpdateRequest,
            identity: FluxIdentity = Depends(get_identity),
        ):
            """Update a hook. Fields left unset keep their stored value."""
            await _require_hook_permission(identity, f"hook:{name}:update")
            hook = _get_hook(name)

            fields = request.model_dump(exclude_unset=True, exclude_none=True)
            if not fields:
                return _hook_model_to_response(hook)

            if "selectors" in fields:
                _validate_selectors(fields["selectors"])

            # Which edits can lead to a fire, per the rule on
            # `_require_may_fire_as`. Re-aiming runs the principal the hook
            # already carries against a target the caller chose; re-enabling
            # resumes automatic firing for every matching event, since a
            # disabled hook is inert (the registry indexes only enabled ones).
            # Turning one *off* is the exception in the other direction: it is
            # what an operator reaches for when a hook is misbehaving, so it
            # must not need a grant that the misbehaviour may be about. A
            # client re-sending values a hook already has — its principal, or
            # `enabled: true` on a live hook — is choosing nothing and changes
            # nothing, so neither counts.
            rebound = "principal" in fields and fields["principal"] != hook.principal
            re_aimed = "workflow_ref" in fields or "selectors" in fields
            re_enabled = fields.get("enabled") is True and not hook.enabled
            if rebound or re_aimed or re_enabled:
                await _require_may_fire_as(identity, fields.get("principal", hook.principal))

            if "workflow_ref" in fields or "principal" in fields:
                await _require_runnable_target(
                    fields.get("principal", hook.principal),
                    fields.get("workflow_ref", hook.workflow_ref),
                )

            updated = HookRegistry.create().update_hook(name, **fields)
            logger.info(f"Successfully updated hook '{name}'")
            return _hook_model_to_response(updated)

        @api.delete("/hooks/{name}")
        async def delete_hook(
            name: str,
            identity: FluxIdentity = Depends(get_identity),
        ):
            """Delete a hook and, by cascade, its deliveries."""
            await _require_hook_permission(identity, f"hook:{name}:delete")
            registry = HookRegistry.create()
            try:
                registry.delete_hook(name)
            except HookNotFoundError as e:
                raise HTTPException(status_code=404, detail=str(e))

            logger.info(f"Successfully deleted hook '{name}'")
            return {"status": "success", "message": f"Hook '{name}' deleted successfully"}

        @api.post("/hooks/{name}/test", response_model=HookTestResponse)
        async def test_hook(
            name: str,
            identity: FluxIdentity = Depends(get_identity),
        ):
            """Fire the hook once with a synthetic event.

            Deliberately no delivery row: this is not the hook reacting to
            something that happened, so it must not enter the outbox, be
            retried, or land in the deliveries history as if it had.

            ``enabled`` is not consulted either — a hook is tested precisely
            while it is off, before it is trusted with real events.

            Impersonation is: unlike every other route here this one is not
            configuration, it starts a real execution as the hook's principal
            with a token minted for it, and firing someone else's identity is
            precisely the act the grant governs. Without it, the guards on
            create and update would be a lock beside an open door.
            """
            await _require_hook_permission(identity, f"hook:{name}:update")
            hook = _get_hook(name)
            await _require_may_fire_as(identity, hook.principal)

            namespace, workflow_name = _resolve_target(hook.workflow_ref)
            # The hook exists; its target may no longer. A 404 here would read
            # as "no such hook", so the stored row's broken reference is a
            # conflict — which is exactly what a test fire exists to surface.
            _require_registered(
                namespace,
                workflow_name,
                status_code=409,
                detail=(
                    f"Cannot test hook '{name}': target workflow "
                    f"'{hook.workflow_ref}' is not in the catalog"
                ),
            )
            await _require_principal_can_run(hook.principal, namespace, workflow_name)

            selector, event = _synthetic_event(hook)
            envelope = build_envelope(
                HookIndexEntry(
                    id=hook.id,
                    name=hook.name,
                    selectors=tuple(hook.selectors or []),
                    workflow_ref=hook.workflow_ref,
                    principal=hook.principal,
                    max_attempts=hook.max_attempts,
                ),
                selector,
                event,
                # Names a delivery that does not exist: the target sees a
                # well-formed envelope, and nothing can later mistake this
                # for a real delivery's id.
                delivery_id=f"hook-test-{uuid4().hex}",
                attempt=1,
                hop=0,
            )

            # The same door a real delivery goes through, principal stamping
            # and execution token included — a test that skipped it would
            # pass for configurations that fail in production.
            execution_id = await self._create_hook_execution(
                namespace,
                workflow_name,
                envelope,
                principal=hook.principal,
                on_behalf_of=f"hook:{hook.name}:test",
            )

            logger.info(f"Test fire of hook '{name}' started execution '{execution_id}'")
            return HookTestResponse(hook=hook.name, execution_id=execution_id, envelope=envelope)

        @api.get("/hooks/{name}/deliveries", response_model=list[HookDeliveryResponse])
        async def list_hook_deliveries(
            name: str,
            # Bounded, as the execution listings are: a delivery row carries a
            # whole event payload, so an unbounded page turns
            # `hook:deliveries:read` — a viewer's grant — into a single-request
            # dump of every event value the hook has ever seen.
            limit: int = Query(default=50, ge=1, le=200),
            identity: FluxIdentity = Depends(require_permission("hook:deliveries:read")),
        ):
            """List a hook's deliveries, newest first."""
            hook = _get_hook(name)
            with RepositoryFactory.create_repository().session() as session:
                deliveries = (
                    session.query(HookDeliveryModel)
                    .filter_by(hook_id=hook.id)
                    .order_by(HookDeliveryModel.created_at.desc())
                    .limit(limit)
                    .all()
                )
                return [_delivery_model_to_response(delivery) for delivery in deliveries]

        @api.post(
            "/hooks/{name}/deliveries/{delivery_id}/retry",
            response_model=HookDeliveryResponse,
        )
        async def retry_hook_delivery(
            name: str,
            delivery_id: str,
            identity: FluxIdentity = Depends(require_permission("hook:deliveries:retry")),
        ):
            """Hand a dead-lettered delivery back to the drain.

            A retry is a fire: the next tick reads the hook's principal and
            target live and starts an execution as that principal. Dead rows
            are routine — a transient failure, an exhausted budget — so
            without the grant this route would hand anyone holding the
            (hook-wide) ``hook:deliveries:retry`` permission a one-call
            execution under whatever principal the hook carries.
            """
            hook = _get_hook(name)
            await _require_may_fire_as(identity, hook.principal)
            with RepositoryFactory.create_repository().session() as session:
                delivery = (
                    session.query(HookDeliveryModel)
                    .filter_by(id=delivery_id, hook_id=hook.id)
                    .one_or_none()
                )
                if delivery is None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Delivery '{delivery_id}' not found for hook '{name}'",
                    )
                if delivery.status != "dead":
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Delivery '{delivery_id}' is '{delivery.status}', not 'dead'; "
                            "only a dead-lettered delivery can be retried"
                        ),
                    )

                # A full reset, not one more attempt: the row was given up on,
                # and the operator retrying it is saying the reason it died
                # has been dealt with, so it gets its whole budget back.
                delivery.status = "pending"
                delivery.attempts = 0
                delivery.next_attempt_at = None
                delivery.last_error = None
                session.commit()
                session.refresh(delivery)

                logger.info(f"Delivery '{delivery_id}' of hook '{name}' queued for retry")
                return _delivery_model_to_response(delivery)
