from __future__ import annotations

import ast
import asyncio
import dataclasses
import hashlib

from flux.utils import maybe_awaitable

_MAX_DEPS_DEPTH = 6


class CodeValidationError(ValueError):
    """Raised when a code-step lambda fails sandbox validation."""


# Node types permitted anywhere in a code-step expression. Dispatch-only:
# no arithmetic (BinOp/UnaryOp), no iteration/comprehension, no FunctionDef.
_ALLOWED_NODES = (
    ast.Expression,
    ast.Lambda,
    ast.arguments,
    ast.arg,
    ast.Call,
    ast.keyword,
    ast.Name,
    ast.Load,
    ast.Subscript,
    ast.Constant,
    ast.List,
    ast.Tuple,
    ast.Dict,
    ast.Set,
    ast.IfExp,
    ast.Compare,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
    ast.Slice,
)


def validate_code(code: str, allowed_names: set[str]) -> ast.Lambda:
    """Parse and validate a code-step lambda. Returns the Lambda node.

    Raises CodeValidationError on any disallowed construct or free name.
    """
    try:
        tree = ast.parse(code, mode="eval")
    except SyntaxError as ex:
        raise CodeValidationError(f"syntax error: {ex}") from ex

    if not isinstance(tree.body, ast.Lambda):
        raise CodeValidationError("code must be a single lambda expression")

    lam = tree.body
    bound = {a.arg for a in lam.args.args}  # lambda params (usually none)
    if lam.args.defaults or lam.args.kw_defaults:
        raise CodeValidationError("lambda default arguments are not allowed")

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise CodeValidationError(f"disallowed syntax: {type(node).__name__}")
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in allowed_names and node.id not in bound:
                raise CodeValidationError(f"unknown name: {node.id}")
    return lam


class _CallProxy:
    """Call-only wrapper: exposes __call__ and nothing else."""

    __slots__ = ("_fn",)

    def __init__(self, fn):
        object.__setattr__(self, "_fn", fn)

    def __call__(self, *args, **kwargs):
        return object.__getattribute__(self, "_fn")(*args, **kwargs)

    def __getattr__(self, name):
        raise AttributeError(name)

    def __setattr__(self, name, value):
        raise AttributeError(name)


def build_sandbox_globals(bindings: dict) -> dict:
    """Build eval globals: top-level callable bindings proxied, non-callables
    passed by value, __builtins__ emptied so no builtin is reachable.

    The dispatch-only grammar rejects any bare name not in `bindings`, so no
    builtin is callable from a code step regardless; __builtins__ is locked to
    {} to make that explicit instead of shipping a subset the validator never
    admits.

    Note: callables nested inside non-callable bindings (e.g. one stored in a
    deps dict) are NOT proxied. Callers must run deps through sanitize_deps so
    no live callable reaches the sandbox via deps['a']['b'](...).
    """
    g: dict = {"__builtins__": {}}
    for name, value in bindings.items():
        g[name] = _CallProxy(value) if callable(value) else value
    return g


def sanitize_deps(value: object, _depth: int = 0) -> object:
    """Return a value-only deep copy of a dependency result for the sandbox.

    Primitives pass through; list/tuple/set become lists and dict keeps its
    keys, with every element sanitized recursively; objects exposing
    model_dump() (Pydantic) or dataclass fields are dumped first. Anything else
    — a callable, or an opaque object — raises CodeValidationError.

    Value-only output serves two guarantees at once: generated code can never
    reach a live callable smuggled in via deps (the dispatch grammar permits
    deps['a']['b'](...)), and the result is deterministically serializable so
    the host task_id stays stable across replay.
    """
    if _depth > _MAX_DEPS_DEPTH:
        raise CodeValidationError("dependency value nested too deeply to sanitize")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple, set)):
        return [sanitize_deps(v, _depth + 1) for v in value]
    if isinstance(value, dict):
        return {k: sanitize_deps(v, _depth + 1) for k, v in value.items()}
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return sanitize_deps(dump(), _depth + 1)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return sanitize_deps(dataclasses.asdict(value), _depth + 1)
    raise CodeValidationError(
        f"dependency value of type {type(value).__name__} is not value-only; "
        "code steps only receive primitive/JSON-like dependency results",
    )


def code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]


async def run_code_step(
    code: str,
    bindings: dict,
    *,
    timeout: int,
    expected_hash: str | None = None,
) -> object:
    """Validate, then evaluate a code-step lambda and run its result.

    The lambda is re-validated here (defense in depth, identical to plan time).
    `expected_hash` (when given) must match the code's hash before eval.
    """
    if expected_hash is not None and code_hash(code) != expected_hash:
        raise CodeValidationError("code hash mismatch — refusing to execute")
    allowed = set(bindings.keys())
    validate_code(code, allowed)
    g = build_sandbox_globals(bindings)
    fn = eval(code, g)  # noqa: S307 — validated, locked builtins, proxied globals
    return await asyncio.wait_for(maybe_awaitable(fn()), timeout=timeout)
