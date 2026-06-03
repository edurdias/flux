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
