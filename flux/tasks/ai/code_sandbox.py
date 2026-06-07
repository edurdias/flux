from __future__ import annotations

import ast
import builtins
import dataclasses
import hashlib


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


_SAFE_BUILTIN_NAMES = (
    "len",
    "range",
    "enumerate",
    "sum",
    "min",
    "max",
    "sorted",
    "abs",
    "zip",
    "any",
    "all",
    "round",
    "dict",
    "list",
    "set",
    "tuple",
    "str",
    "int",
    "float",
    "bool",
)
_SAFE_BUILTINS = {n: getattr(builtins, n) for n in _SAFE_BUILTIN_NAMES}


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
