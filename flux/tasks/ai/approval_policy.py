"""Approval policy resolution for agents (issue #146).

``approval_mode`` conflated two independent concerns behind one unchecked
string: *how much the agent may do without asking* and *where the human is
reached when a gate fires*. The split:

- **Autonomy ceiling** — a validated ladder deciding *whether* gates fire:
  ``strict`` gates every tool call; ``default`` gates what declares
  ``requires_approval`` (today's behavior); ``autonomous`` removes the
  gates (the old ``approval_mode="autonomous"``).
- **Approval routing** — *how* a fired gate reaches a human. ``inline`` is
  the only mode: it pauses on the current surface (today's behavior).
  An out-of-band ``notify`` mode was declared (issue #144) but never
  wired to a delivery mechanism; #144 closed as not-planned and the
  value was removed rather than left as a silent no-op. The field stays
  so a future delivery design has a validated place to declare into.

The invariant, enforced here by construction: routing never changes the
ceiling, and the ceiling never changes routing — they are resolved and
validated independently. ``approval_mode`` remains accepted everywhere it
was accepted (call sites, stored agent rows, YAML definitions) and maps
onto the ladder; explicit ``autonomy``/``approval_routing`` win over it.
"""

from __future__ import annotations

import logging
import warnings

logger = logging.getLogger(__name__)

AUTONOMY_LEVELS = ("strict", "default", "autonomous")
ROUTING_MODES = ("inline",)


def resolve_approval_policy(
    approval_mode: str | None = None,
    autonomy: str | None = None,
    approval_routing: str | None = None,
) -> tuple[str, str]:
    """Resolve ``(autonomy, routing)`` from new and legacy parameters.

    Explicit ``autonomy`` / ``approval_routing`` are validated against
    their ladders and win outright. A legacy ``approval_mode`` maps:
    ``"autonomous"`` → autonomy ``autonomous``; every other value —
    including the typo'd ones that historically meant "default" silently —
    maps to ``default``, now with a warning instead of silence. Unset
    everything resolves to ``("default", "inline")``, today's behavior.
    """
    if autonomy is not None and autonomy not in AUTONOMY_LEVELS:
        raise ValueError(
            f"autonomy must be one of {AUTONOMY_LEVELS}, got: '{autonomy}'",
        )
    if approval_routing is not None and approval_routing not in ROUTING_MODES:
        raise ValueError(
            f"approval_routing must be one of {ROUTING_MODES}, got: '{approval_routing}'",
        )

    if approval_mode is not None:
        warnings.warn(
            "approval_mode is deprecated; use autonomy= (strict/default/"
            "autonomous) and approval_routing= (inline) instead",
            DeprecationWarning,
            stacklevel=3,
        )
        if autonomy is None:
            if approval_mode == "autonomous":
                autonomy = "autonomous"
            else:
                if approval_mode != "default":
                    logger.warning(
                        f"Unknown approval_mode '{approval_mode}' treated as "
                        "'default' (the historic silent behavior); use "
                        "autonomy= for validated values",
                    )
                autonomy = "default"

    return autonomy or "default", approval_routing or "inline"
