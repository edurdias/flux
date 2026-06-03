from __future__ import annotations

import ast


class CodeValidationError(ValueError):
    """Raised when a code-step lambda fails sandbox validation."""


# Node types permitted anywhere in a code-step expression. Dispatch-only:
# no arithmetic (BinOp/UnaryOp), no iteration/comprehension, no FunctionDef.
_ALLOWED_NODES = (
    ast.Expression, ast.Lambda, ast.arguments, ast.arg,
    ast.Call, ast.keyword, ast.Name, ast.Load,
    ast.Subscript, ast.Constant, ast.List, ast.Tuple, ast.Dict, ast.Set,
    ast.IfExp, ast.Compare, ast.BoolOp, ast.And, ast.Or,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
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


_SAFE_BUILTINS = {
    n: __builtins__[n] if isinstance(__builtins__, dict) else getattr(__builtins__, n)
    for n in (
        "len", "range", "enumerate", "sum", "min", "max", "sorted",
        "dict", "list", "set", "tuple", "str", "int", "float", "bool",
        "abs", "zip", "any", "all", "round",
    )
}


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
    """Build eval globals: callables proxied, non-callables passed by value,
    __builtins__ locked to the safe subset."""
    g: dict = {"__builtins__": dict(_SAFE_BUILTINS)}
    for name, value in bindings.items():
        g[name] = _CallProxy(value) if callable(value) else value
    return g
