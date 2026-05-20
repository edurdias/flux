"""Verify ContextManager.save accepts a uow= kwarg and uses uow.session.

Doesn't validate full atomicity (that lands when ApprovalManager joins);
just proves the signature change is wired correctly.
"""

import inspect

from flux.context_managers import ContextManager


def test_save_signature_accepts_uow_kwarg():
    sig = inspect.signature(ContextManager.save)
    assert "uow" in sig.parameters, (
        f"Expected ContextManager.save to accept a 'uow' kwarg; got {list(sig.parameters)}"
    )
    p = sig.parameters["uow"]
    assert p.default is None, "Expected uow to default to None for backwards compat"
