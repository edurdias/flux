"""Declarative routing policies: the score stage of workflow dispatch.

Hard constraints (``requests``, ``affinity``, ``runner``, health, capacity)
filter the candidate workers; a routing policy ranks the survivors. Policies
are data, not code — the decorator factories below compile to a JSON spec
that the catalog extracts statically (AST, like ``requests``) and the server
evaluates natively inside the dispatch batch. No user code ever runs on the
server.

    @workflow.with_options(
        routing=score(
            prefer(label("region") == input("region"), weight=10),
            least(metric("queue_depth"), weight=5),
            most(resource("memory_available"), weight=2),
            sticky(weight=3),
            least(load()),
        ),
    )

Selectors:
    ``label(key)``      worker label value
    ``metric(key)``     worker-advertised metric (``[flux.workers] metrics_provider``)
    ``resource(field)`` worker resource field (cpu/memory/disk totals and availables)
    ``load()``          active executions on the worker (built-in)

Each term normalizes to 0..1 across the eligible set (so an unbounded
``load`` term cannot drown a boolean ``prefer``), the weighted sum ranks the
workers, and ties break deterministically (lower load, then name). A missing
value scores 0 for that term. Event dispatch mode only — poll mode is a
per-worker pull with no cross-worker view, so policies are ignored there,
same as the sticky hint.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from flux.utils import get_logger

if TYPE_CHECKING:
    from flux.worker_registry import WorkerInfo

logger = get_logger(__name__)

_OPS = ("==", "!=", "<", "<=", ">", ">=")
_RESOURCE_FIELDS = (
    "cpu_total",
    "cpu_available",
    "memory_total",
    "memory_available",
    "disk_total",
    "disk_free",
)

# Guardrails for worker-advertised metrics (the pong payload is caller input).
MAX_METRICS = 32
MAX_METRIC_KEY_LENGTH = 64


class InputRef:
    """A value resolved from the execution input at dispatch time.

    ``input("region")`` compares against ``ctx.input["region"]``;
    dotted paths (``input("customer.region")``) descend nested dicts.
    """

    def __init__(self, path: str):
        if not path or not isinstance(path, str):
            raise ValueError("input() requires a non-empty string path")
        self.path = path

    def to_spec(self) -> dict[str, str]:
        return {"$input": self.path}


def input(path: str) -> InputRef:  # noqa: A001 - deliberate DSL name
    return InputRef(path)


class Condition:
    """A comparison produced by applying an operator to a Selector."""

    def __init__(self, selector: Selector, op: str, value: Any):
        if op not in _OPS:
            raise ValueError(f"op must be one of {_OPS}, got: '{op}'")
        if isinstance(value, Selector):
            raise ValueError("selectors can only be compared against constants or input(...)")
        if isinstance(value, InputRef):
            value = value.to_spec()
        elif not isinstance(value, (str, int, float, bool)):
            raise ValueError(
                f"value must be a constant or input(...), got: {type(value).__name__}",
            )
        self.selector = selector
        self.op = op
        self.value = value


class Selector:
    """A worker attribute usable in routing terms.

    Comparison operators build :class:`Condition` objects for ``prefer()``:
    ``label("region") == "eu-west"``, ``metric("temp") < 60``. Reversed
    comparisons (``60 > metric("temp")``) work through Python's reflected
    operator protocol.
    """

    def __init__(self, kind: str, key: str | None = None):
        if kind == "load":
            self.spec = "load"
            return
        if not key or not isinstance(key, str):
            raise ValueError(f"{kind}() requires a non-empty string key")
        if kind == "resource" and key not in _RESOURCE_FIELDS:
            raise ValueError(
                f"unknown resource field '{key}'; expected one of {_RESOURCE_FIELDS}",
            )
        self.spec = f"{kind}:{key}"

    def __eq__(self, other: Any) -> Condition:  # type: ignore[override]
        return Condition(self, "==", other)

    def __ne__(self, other: Any) -> Condition:  # type: ignore[override]
        return Condition(self, "!=", other)

    def __lt__(self, other: Any) -> Condition:
        return Condition(self, "<", other)

    def __le__(self, other: Any) -> Condition:
        return Condition(self, "<=", other)

    def __gt__(self, other: Any) -> Condition:
        return Condition(self, ">", other)

    def __ge__(self, other: Any) -> Condition:
        return Condition(self, ">=", other)

    # Comparisons build Conditions, so instances are deliberately unhashable.
    __hash__ = None  # type: ignore[assignment]


def label(key: str) -> Selector:
    """Worker label value."""
    return Selector("label", key)


def metric(key: str) -> Selector:
    """Worker-advertised metric (``[flux.workers] metrics_provider``)."""
    return Selector("metric", key)


def resource(field: str) -> Selector:
    """Worker resource field (cpu/memory/disk totals and availables)."""
    return Selector("resource", field)


def load() -> Selector:
    """Active executions on the worker (built-in)."""
    return Selector("load")


def _validate_weight(weight: Any) -> float:
    try:
        weight = float(weight)
    except (TypeError, ValueError):
        raise ValueError(f"weight must be a number, got: {weight!r}")
    if not math.isfinite(weight) or weight <= 0:
        raise ValueError(f"weight must be a positive finite number, got: {weight}")
    return weight


def _require_selector(value: Any, term: str) -> Selector:
    if not isinstance(value, Selector):
        raise ValueError(
            f"{term}() takes a selector (label/metric/resource/load), got: {type(value).__name__}",
        )
    return value


def prefer(condition: Condition, *, weight: float = 1.0) -> dict:
    """Boolean preference: 1.0 when the condition holds, else 0.

    ``prefer(label("region") == input("region"), weight=10)``
    """
    if not isinstance(condition, Condition):
        raise ValueError(
            "prefer() takes a selector comparison, e.g. "
            f'prefer(label("region") == "eu-west"), got: {type(condition).__name__}',
        )
    return {
        "kind": "prefer",
        "selector": condition.selector.spec,
        "op": condition.op,
        "value": condition.value,
        "weight": _validate_weight(weight),
    }


def least(selector: Selector, *, weight: float = 1.0) -> dict:
    """Prefer workers where the numeric selector value is lowest."""
    return {
        "kind": "least",
        "selector": _require_selector(selector, "least").spec,
        "weight": _validate_weight(weight),
    }


def most(selector: Selector, *, weight: float = 1.0) -> dict:
    """Prefer workers where the numeric selector value is highest."""
    return {
        "kind": "most",
        "selector": _require_selector(selector, "most").spec,
        "weight": _validate_weight(weight),
    }


def sticky(*, weight: float = 1.0) -> dict:
    """Opt the relay hint (X-Flux-Preferred-Worker) into the score.

    A workflow with a routing policy owns its score stage entirely — the
    hint only participates when the policy includes this term.
    """
    return {"kind": "sticky", "weight": _validate_weight(weight)}


def score(*terms: dict) -> dict:
    """Compose terms into a routing policy spec (stored in workflow metadata)."""
    if not terms:
        raise ValueError("score() requires at least one term")
    for term in terms:
        if not isinstance(term, dict) or term.get("kind") not in (
            "prefer",
            "least",
            "most",
            "sticky",
        ):
            raise ValueError(
                f"score() accepts only prefer()/least()/most()/sticky() terms, got: {term!r}",
            )
    return {"terms": list(terms)}


def validate_worker_metrics(payload: Any) -> dict[str, float] | None:
    """Sanitize a worker-advertised metrics mapping. Returns None when the
    payload is unusable — metrics are a hint channel, never an error channel."""
    if not isinstance(payload, dict) or len(payload) > MAX_METRICS:
        return None
    metrics: dict[str, float] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not key or len(key) > MAX_METRIC_KEY_LENGTH:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        value = float(value)
        if not math.isfinite(value):
            return None
        metrics[key] = value
    return metrics


# ---------------------------------------------------------------------------
# Evaluation (server-side, inside the dispatch batch)
# ---------------------------------------------------------------------------


def _resolve_input_path(input_value: Any, path: str) -> Any:
    current = input_value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _selector_value(worker: WorkerInfo, selector: str, loads: dict[str, int]) -> Any:
    if selector == "load":
        return loads.get(worker.name, 0)
    kind, _, key = selector.partition(":")
    if kind == "label":
        return (worker.labels or {}).get(key)
    if kind == "metric":
        return (getattr(worker, "metrics", None) or {}).get(key)
    if kind == "resource":
        resources = worker.resources
        return getattr(resources, key, None) if resources else None
    return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _compare(left: Any, op: str, right: Any) -> bool:
    if left is None or right is None:
        return False
    left_num, right_num = _as_float(left), _as_float(right)
    if left_num is not None and right_num is not None:
        left, right = left_num, right_num
    elif op in ("<", "<=", ">", ">="):
        return False  # ordering needs numbers
    else:
        left, right = str(left), str(right)
    if op == "==":
        return left == right
    if op == "!=":
        return left != right
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    if op == ">":
        return left > right
    return left >= right


def pick_worker(
    eligible: list[WorkerInfo],
    policy: Any,
    *,
    loads: dict[str, int],
    input_value: Any = None,
    preferred: str | None = None,
) -> WorkerInfo | None:
    """Rank eligible workers by the policy and return the winner.

    Returns None on a malformed policy so the caller can fall back to the
    default (least-loaded) selection — a bad policy must degrade, not strand
    executions.
    """
    if not eligible:
        return None
    terms = policy.get("terms") if isinstance(policy, dict) else None
    if not isinstance(terms, list) or not terms:
        logger.warning(f"Malformed routing policy ignored: {policy!r}")
        return None

    totals = {w.name: 0.0 for w in eligible}
    for term in terms:
        if not isinstance(term, dict):
            logger.warning(f"Malformed routing term ignored: {term!r}")
            return None
        kind = term.get("kind")
        weight = _as_float(term.get("weight", 1.0))
        if weight is None or weight <= 0:
            logger.warning(f"Malformed routing term ignored: {term!r}")
            return None

        if kind == "sticky":
            for w in eligible:
                if preferred and w.name == preferred:
                    totals[w.name] += weight
            continue

        selector = term.get("selector")
        if not isinstance(selector, str):
            logger.warning(f"Malformed routing term ignored: {term!r}")
            return None

        if kind == "prefer":
            op = term.get("op")
            if op not in _OPS:
                logger.warning(f"Malformed routing term ignored: {term!r}")
                return None
            right = term.get("value")
            if isinstance(right, dict) and "$input" in right:
                right = _resolve_input_path(input_value, right["$input"])
            for w in eligible:
                if _compare(_selector_value(w, selector, loads), op, right):
                    totals[w.name] += weight
            continue

        if kind in ("least", "most"):
            values = {w.name: _as_float(_selector_value(w, selector, loads)) for w in eligible}
            present = [v for v in values.values() if v is not None]
            if not present:
                continue  # nobody has the value; the term cannot discriminate
            lo, hi = min(present), max(present)
            for w in eligible:
                v = values[w.name]
                if v is None:
                    continue  # missing scores 0
                if hi == lo:
                    normalized = 0.5  # equal values cannot discriminate
                else:
                    normalized = (v - lo) / (hi - lo)
                if kind == "least":
                    normalized = 1.0 - normalized
                totals[w.name] += weight * normalized
            continue

        logger.warning(f"Malformed routing term ignored: {term!r}")
        return None

    # Deterministic winner: highest score, then lower load, then name.
    return min(
        eligible,
        key=lambda w: (-totals[w.name], loads.get(w.name, 0), w.name),
    )
