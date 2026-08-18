"""``flux.agents``'s lazy attribute resolution (#245).

The package keeps AgentManager and AgentProcess behind ``__getattr__`` so a
lean console process can import ``flux.agents.ui.api`` without dragging in
SQLAlchemy or the whole UI stack. CLAUDE.md flags this kind of module-level
machinery as fragile -- a rename or a moved import turns a working lazy
export into an AttributeError that only shows up at the call site -- and
nothing pinned it.
"""

from __future__ import annotations

import pytest

import flux.agents


@pytest.mark.parametrize(
    ("name", "module", "attribute"),
    [
        ("AgentManager", "flux.agents.manager", "AgentManager"),
        ("AgentProcess", "flux.agents.process", "AgentProcess"),
        ("AgentDefinition", "flux.agents.types", "AgentDefinition"),
        ("ChatResponseOutput", "flux.agents.types", "ChatResponseOutput"),
        ("SessionEndOutput", "flux.agents.types", "SessionEndOutput"),
    ],
)
def test_each_lazy_export_resolves_to_its_real_object(name, module, attribute):
    import importlib

    resolved = getattr(flux.agents, name)

    assert resolved is getattr(importlib.import_module(module), attribute)


def test_every_advertised_export_resolves():
    """__all__ is what `from flux.agents import *` and tooling read, so a
    name advertised there but not handled by __getattr__ is a broken export."""
    for name in flux.agents.__all__:
        assert getattr(flux.agents, name) is not None


def test_an_unknown_attribute_raises_attribute_error():
    with pytest.raises(AttributeError, match="has no attribute 'NotAThing'"):
        flux.agents.NotAThing


def test_dir_lists_the_lazy_exports():
    listed = dir(flux.agents)

    assert set(flux.agents.__all__).issubset(listed)
    assert listed == sorted(listed)
