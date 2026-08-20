"""Firing a hook: starting its target workflow, and re-checking its principal.

Extracted from ``flux.server`` (#264). Both operations are server-initiated
work performed under a *stored* principal rather than a caller's identity,
which is what makes them worth their own module: the drain and the hook
routes each need them, and neither should have to reach into the server
object to get them.

The dependencies are passed in rather than imported: ``create_execution``
is whatever creates an execution (the server's own path today), and
``session_factory`` opens a database session. That keeps this module
testable without a server and makes the coupling visible in the signature
instead of hidden behind ``self``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from flux.catalogs import WorkflowCatalog
from flux.config import Configuration
from flux.security.auth_service import AuthService
from flux.security.identity import FluxIdentity
from flux.utils import get_logger

logger = get_logger(__name__)


async def start_hook_execution(
    create_execution: Callable[..., Any],
    session_factory: Callable[[], Any],
    namespace: str,
    workflow_name: str,
    input_data: Any,
    *,
    principal: str | None = None,
    on_behalf_of: str | None = None,
) -> str:
    """Start a hook's target workflow, for the drain.

    The drain deals in execution ids and awaits its creator, so this
    adapts the server's own creation path to that shape rather than
    letting the drain reach into the server.

    The execution is stamped with the principal it runs as and carries
    its own execution token, exactly as a scheduled one is: without them
    a hook-started workflow calling back into the server is anonymous —
    and since a task-level permission check downstream would then fail
    closed, the identity has to travel with the execution rather than
    stop at the authorization above it.
    """
    ctx = create_execution(namespace, workflow_name, input_data)

    auth_config = Configuration.get().settings.security.auth
    if not auth_config.enabled or not principal:
        return ctx.execution_id

    try:
        from flux.security.execution_token import mint_execution_token
        from flux.security.principals import PrincipalRegistry

        principal_row = PrincipalRegistry(session_factory=session_factory).find(
            principal,
            "flux",
        )
        if principal_row is None:
            raise ValueError(f"principal '{principal}' disappeared after authorization")

        exec_token = mint_execution_token(
            subject=principal_row.subject,
            principal_issuer=principal_row.external_issuer,
            execution_id=ctx.execution_id,
            on_behalf_of=on_behalf_of or "hook",
        )

        # One session, one commit: token and provenance are a single
        # fact about the row, as on the schedule path.
        session = session_factory()
        try:
            from flux.models import ExecutionContextModel as _ECM_HOOK

            exec_row = session.get(_ECM_HOOK, ctx.execution_id)
            if exec_row:
                exec_row.exec_token = exec_token
                exec_row.scheduling_subject = principal_row.subject
                exec_row.scheduling_principal_issuer = principal_row.external_issuer
                session.commit()
        finally:
            session.close()
    except Exception as e:
        # Deliberately not raised: the execution already exists, and the
        # drain would read a raise as a transient failure and start a
        # second one on the next tick. An unstamped execution is a
        # degraded run — anything it calls back fails closed — where a
        # duplicated one is a wrong one.
        logger.error(
            f"Execution {ctx.execution_id} started by {on_behalf_of or 'a hook'} "
            f"could not be stamped with its principal: {e}",
            exc_info=True,
        )

    return ctx.execution_id


async def authorize_hook_principal(
    session_factory: Callable[[], Any],
    principal: str,
    permission: str,
) -> bool:
    """Re-check a hook's principal at fire time, for the drain.

    A hook outlives the grant it was created under: the principal may
    have been disabled, banned, or had the role carrying this permission
    revoked since, so roles are read fresh here. With auth off there is
    nothing to check and everything is permitted, as everywhere else in
    the server.

    The check is the *whole* run authorization the schedule path
    performs — every task's ``:execute`` and every nested workflow, not
    only ``:run`` — because a hook is server-initiated work under a
    stored principal just as a schedule is, and two doors into the same
    room should not need different keys. ``WorkflowNotFoundError`` from
    the catalog read is left to propagate: the drain dead-letters a
    vanished target on it, which is the truthful reason.
    """
    auth_config = Configuration.get().settings.security.auth
    if not auth_config.enabled:
        return True

    # The drain names the permission it needs; the deeper check needs the
    # workflow that permission is about, so it is read back out of it
    # rather than widening the injected contract with a second spelling
    # of the same fact.
    parts = permission.split(":")
    if len(parts) != 4 or parts[0] != "workflow" or parts[3] != "run":
        logger.warning(f"Hook authorization asked for an unexpected permission: {permission}")
        return False
    namespace, workflow_name = parts[1], parts[2]

    from flux.security.principals import PrincipalRegistry

    registry = PrincipalRegistry(session_factory=session_factory)
    # By subject, the way the hook row names it — the same resolution the
    # schedule path does for its service account.
    principal_row = registry.find(principal, "flux")
    if principal_row is None or not principal_row.enabled or principal_row.banned:
        logger.warning(
            f"Hook principal '{principal}' is missing, disabled or banned; refusing delivery",
        )
        return False

    workflow = WorkflowCatalog.create().get(namespace, workflow_name)
    workflow_metadata = getattr(workflow, "metadata", None) or {}

    identity = FluxIdentity(
        subject=principal_row.subject,
        roles=frozenset(registry.get_roles(principal_row.id)),
        metadata={
            "token_type": "service_account",
            "issuer": principal_row.external_issuer,
            "principal_id": principal_row.id,
            "via": "hook",
        },
    )
    auth_service = AuthService(
        config=auth_config,
        session_factory=session_factory,
        registry=registry,
    )
    result = await auth_service.authorize(
        identity,
        namespace,
        workflow_name,
        workflow_metadata,
    )
    if not result.ok:
        logger.warning(
            f"Hook principal '{principal_row.subject}' lacks permissions for "
            f"{namespace}/{workflow_name}: {result.missing_permissions}",
        )
        return False
    return True
