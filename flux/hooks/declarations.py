"""Declarative hook specs: the counterpart of ``flux.routing.score(...)``.

``hook.run(...)`` returns a plain, JSON-serializable dict rather than an
object -- like ``flux.routing.score``, so it can live in workflow metadata
and a Pydantic field (``AgentDefinition.hooks``) without a custom encoder,
and be rebuilt identically from AST-extracted literals at registration time
(``flux.catalogs.DatabaseWorkflowCatalog._extract_hooks``).
"""

from __future__ import annotations

from flux.hooks.selectors import validate_selector


class hook:
    """Namespace for hook declaration factories, used from a workflow's
    ``@workflow.with_options(hooks=[...])`` or an agent's ``hooks`` field.
    """

    @staticmethod
    def run(
        *,
        on: str,
        workflow: str,
        principal: str,
        name: str | None = None,
        max_attempts: int = 5,
    ) -> dict:
        """Declare one hook: fire ``workflow`` as ``principal`` when ``on`` matches.

        ``principal`` is required, not defaulted to the declarer's own
        identity -- whose rights a hook fires under is a decision, not a
        side effect, the same rule ``HookRequest.principal`` enforces for
        the server-side CRUD path. ``name`` is optional: when omitted, the
        owner-scoped reconciliation (``HookRegistry._derive_hook_name``)
        derives a stable one from the owner and a digest of this spec's
        ``on``/``workflow``/``principal`` -- never this spec's position in
        its ``hooks`` list, so reordering or removing sibling specs never
        changes it. Editing any of ``on``/``workflow``/``principal`` on an
        unnamed hook derives a different name and starts a new row (losing
        the old one's delivery history); give a hook an explicit ``name=``
        to keep its identity stable across such edits.
        """
        validate_selector(on)
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got: {max_attempts}")
        return {
            "on": on,
            "workflow": workflow,
            "principal": principal,
            "name": name,
            "max_attempts": max_attempts,
        }


def validate_workflow_scope(selector: str, namespace: str, name: str) -> None:
    """Raise ``ValueError`` unless ``selector`` observes only this workflow.

    Workflow-declared hooks (declaration path 2) may only watch their own
    executions/tasks -- observing the fleet requires an operator via the
    server-side CRUD path (path 1). Selector segments 1 and 2 are the
    namespace and workflow name for both domains
    (``execution:<ns>:<wf>:...`` / ``task:<ns>:<wf>:...``), so the check is
    domain-independent and does not need ``validate_selector`` to have run
    first.
    """
    parts = selector.split(":")
    if len(parts) < 3 or parts[1] != namespace or parts[2] != name:
        raise ValueError(
            f"hook selector {selector!r} must observe only the declaring "
            f"workflow ({namespace}/{name}); a workflow may observe "
            "itself, not the fleet -- subscribing to another workflow "
            "requires an operator via POST /hooks",
        )
