from __future__ import annotations

import ast
import asyncio
import dataclasses
import hashlib

from flux.utils import maybe_awaitable

_MAX_DEPS_DEPTH = 6


class CodeValidationError(ValueError):
    """Raised when a code-step lambda fails sandbox validation."""


_ALLOWED_NODES = (
    ast.Module,
    ast.AsyncFunctionDef,
    ast.arguments,
    ast.arg,
    ast.Assign,
    ast.AugAssign,
    ast.AnnAssign,
    ast.Return,
    ast.Expr,
    ast.Pass,
    ast.Break,
    ast.Continue,
    ast.For,
    ast.While,
    ast.If,
    ast.IfExp,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.comprehension,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.Await,
    ast.Call,
    ast.keyword,
    ast.Starred,
    ast.Subscript,
    ast.Slice,
    ast.Name,
    ast.Load,
    ast.Store,
    ast.Constant,
    ast.List,
    ast.Tuple,
    ast.Dict,
    ast.Set,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.LShift,
    ast.RShift,
    ast.BitOr,
    ast.BitAnd,
    ast.BitXor,
    ast.MatMult,
    ast.UAdd,
    ast.USub,
    ast.Not,
    ast.Invert,
    ast.And,
    ast.Or,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.Is,
    ast.IsNot,
    ast.In,
    ast.NotIn,
)


_DENIED_NAMES = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "open",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
        "breakpoint",
        "memoryview",
        "__import__",
    },
)


def _is_denied_name(name: str) -> bool:
    return name in _DENIED_NAMES or (name.startswith("__") and name.endswith("__"))


def _collect_bound_names(tree: ast.AST) -> set[str]:
    """Names bound inside the function: params + every Store target."""
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
    return bound


def validate_code(code: str, allowed_names: set[str]) -> ast.AsyncFunctionDef:
    """Parse and validate a code step. Returns the `step` function node.

    The source must be exactly one `async def step(deps, input)`. Raises
    CodeValidationError on any disallowed construct or unknown free name.
    """
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as ex:
        raise CodeValidationError(f"syntax error: {ex}") from ex

    body = tree.body
    if len(body) != 1 or not isinstance(body[0], ast.AsyncFunctionDef):
        raise CodeValidationError(
            "code must be exactly one 'async def step(deps, input)' function",
        )
    fn = body[0]
    if fn.name != "step":
        raise CodeValidationError(f"function must be named 'step', got '{fn.name}'")
    params = [a.arg for a in fn.args.args]
    if params != ["deps", "input"] or fn.args.vararg or fn.args.kwarg or fn.args.kwonlyargs:
        raise CodeValidationError("step must take exactly (deps, input)")
    if fn.decorator_list:
        raise CodeValidationError("decorators are not allowed on a code step")
    if fn.args.defaults or fn.args.kw_defaults or fn.args.posonlyargs:
        raise CodeValidationError("default or positional-only arguments are not allowed")

    bound = _collect_bound_names(fn)
    allowed = set(allowed_names) | bound

    for node in ast.walk(fn):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node is not fn:
            raise CodeValidationError("nested function definitions are not allowed")
        if not isinstance(node, _ALLOWED_NODES):
            raise CodeValidationError(f"disallowed syntax: {type(node).__name__}")
        if isinstance(node, ast.Name) and _is_denied_name(node.id):
            raise CodeValidationError(f"disallowed name: {node.id}")
        if isinstance(node, ast.arg) and _is_denied_name(node.arg):
            raise CodeValidationError(f"disallowed name: {node.arg}")
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in allowed:
                raise CodeValidationError(f"unknown name: {node.id}")
    return fn


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
