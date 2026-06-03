from __future__ import annotations

import pytest

from flux.tasks.ai.code_sandbox import validate_code, CodeValidationError

ALLOWED = {"now", "parallel", "deps", "delegate"}


def test_accepts_dispatch_lambda():
    validate_code("lambda: now()", ALLOWED)
    validate_code('lambda: parallel(now(), now())', ALLOWED)
    validate_code('lambda: deps["fetch"]', ALLOWED)
    validate_code('lambda: now() if deps["x"] else now()', ALLOWED)


@pytest.mark.parametrize(
    "code",
    [
        "lambda: __import__('os')",
        "lambda: open('/etc/passwd')",
        "lambda: (1).__class__",
        "lambda: sum(range(10**18))",
        "lambda: [0] * 10**9",
        "lambda: [x for x in deps]",
        "lambda: now.__globals__",
        "now()",
        "lambda: missing()",
        'lambda: deps["x"].format(now())',   # str.format MRO-walk via attribute
        "lambda: now().something",            # attribute access on call result
    ],
)
def test_rejects_unsafe(code):
    with pytest.raises(CodeValidationError):
        validate_code(code, ALLOWED)
