"""Declarative routing policies: the filter and score stages of dispatch.

Hard constraints (``requests``, ``affinity``, ``runner``, health, capacity)
filter the candidate workers; a routing policy ranks the survivors. Policies
are data, not code — the decorator factories below compile to a JSON spec
that the catalog extracts statically (AST, like ``requests``) and the server
evaluates natively inside the dispatch batch. No user code ever runs on the
server.

Two families of factories live here:

- ``score(...)`` builds the *scoring* policy for ``routing=`` (ranks eligible
  workers; event dispatch mode only).
- ``require(...)`` builds an *affinity expression* for ``affinity=`` (a hard
  per-worker filter whose terms resolve against the execution input at
  dispatch; works in both poll and event dispatch modes). See ``require``.

The dynamic constructs span both stages: ``input(...)`` values,
``label_for(...)`` dynamic keys, and ``service(...)`` work in ``require``
terms and in ``prefer()``; ``when(input(...) == const, term)`` gates a term
in either stage. The same comparison is a hard wall under ``require`` and a
soft preference under ``prefer`` — pair them for floor-plus-preference
routing.

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
    ``meta(key)``       server-held metadata (admin-written, worker-unspoofable)
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
import re
from typing import TYPE_CHECKING, Any, NamedTuple

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
# MAX_METRICS bounds a metrics provider's output; MAX_TOTAL_METRICS bounds the
# merged pong payload (provider output + built-in flux.* metrics) server-side.
MAX_METRICS = 32
MAX_TOTAL_METRICS = 64
MAX_METRIC_KEY_LENGTH = 64
# Built-in worker metrics live under this prefix; provider keys using it are
# stripped so user values can never impersonate a built-in signal.
RESERVED_METRIC_PREFIX = "flux."


class InputRef:
    """A value resolved from the execution input at dispatch time.

    ``input("region")`` compares against ``ctx.input["region"]``;
    dotted paths (``input("customer.region")``) descend nested dicts.

    Comparing an ``input(...)`` against a constant builds an
    :class:`InputCondition` for ``when(...)`` in a ``require`` expression:
    ``when(input("tier") == "dedicated", ...)``.
    """

    def __init__(self, path: str):
        if not path or not isinstance(path, str):
            raise ValueError("input() requires a non-empty string path")
        self.path = path

    def to_spec(self) -> dict[str, str]:
        return {"$input": self.path}

    def __eq__(self, other: Any) -> InputCondition:  # type: ignore[override]
        return InputCondition(self, "==", other)

    def __ne__(self, other: Any) -> InputCondition:  # type: ignore[override]
        return InputCondition(self, "!=", other)

    # Comparisons build InputConditions, so instances are deliberately
    # unhashable (same contract as Selector).
    __hash__ = None  # type: ignore[assignment]


class InputCondition:
    """A comparison of an execution-input value against a constant.

    Only usable as the ``when(...)`` condition of a ``require`` expression:
    it conditions on the requester's intent, never on worker attributes.
    """

    def __init__(self, ref: InputRef, op: str, value: Any):
        if op not in ("==", "!="):
            raise ValueError(f"input() conditions support only == and !=, got: '{op}'")
        if isinstance(value, (InputRef, Selector)):
            raise ValueError("input() can only be compared against constants")
        if not isinstance(value, (str, int, float, bool)) and value is not None:
            raise ValueError(
                f"input() comparison value must be a constant, got: {type(value).__name__}",
            )
        self.ref = ref
        self.op = op
        self.value = value


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

    # str for static selectors ("label:gpu"); DynamicLabel stores a dict spec.
    spec: str | dict[str, Any]

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
        if kind == "meta" and (len(key) > MAX_METADATA_KEY_LENGTH or not _LABEL_KEY_RE.match(key)):
            # The admin API can never write such a key
            # (validate_worker_metadata), so a term naming one would be
            # permanently unsatisfiable — fail at authoring time instead.
            raise ValueError(f"invalid metadata key: {key!r}")
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


def meta(key: str) -> Selector:
    """Server-held worker metadata (written via the admin API, never by the
    worker — the control-plane-authoritative counterpart of ``label``)."""
    return Selector("meta", key)


def resource(field: str) -> Selector:
    """Worker resource field (cpu/memory/disk totals and availables)."""
    return Selector("resource", field)


def load() -> Selector:
    """Active executions on the worker (built-in)."""
    return Selector("load")


# Resolved dynamic label keys must look like ordinary label keys: alphanumeric
# with interior ``.``/``_``/``-``, bounded length. Static keys authored in a
# dict or ``label(...)`` are not retro-validated; only keys completed from
# execution input are held to this.
MAX_LABEL_KEY_LENGTH = 128
_LABEL_KEY_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")

# Granted service sockets are advertised as ``flux.service.<name>`` labels;
# the worker rejects user labels under ``flux.``, so these cannot be spoofed.
SERVICE_LABEL_PREFIX = "flux.service."

# Service names follow the worker-side socket-name rule (enforced at worker
# startup on [flux.workers] airgapped_service_sockets, see
# flux/runners/docker.py) — validating service() terms against the same rule
# keeps a workflow from compiling an expression no real worker can satisfy.
MAX_SERVICE_NAME_LENGTH = 32
SERVICE_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


def is_valid_service_name(name: str) -> bool:
    """Whether a service-socket name is one worker registration could grant:
    lowercase letters, digits, and single hyphens, max 32 chars."""
    return (
        isinstance(name, str)
        and len(name) <= MAX_SERVICE_NAME_LENGTH
        and "--" not in name
        and bool(SERVICE_NAME_RE.match(name))
    )


class DynamicLabel(Selector):
    """A label selector whose key is completed from execution input.

    ``label_for("sku.", input("model"))`` inspects label ``sku.<model>`` on
    each candidate worker. The prefix is mandatory — the workflow author
    declares the namespace, input only completes it. Valid in ``require(...)``
    terms and in ``prefer()``; not in ``least()``/``most()``, where label
    strings have no ordering.
    """

    def __init__(self, prefix: str, ref: InputRef):
        if not prefix or not isinstance(prefix, str):
            raise ValueError("label_for() requires a non-empty string prefix")
        if not isinstance(ref, InputRef):
            raise ValueError(
                f"label_for() key must be completed from input(...), got: {type(ref).__name__}",
            )
        # The prefix must be a valid label-key head so no input value can
        # produce a valid key from an invalid namespace.
        if not _LABEL_KEY_RE.match(prefix.rstrip("._-") or ""):
            raise ValueError(f"label_for() prefix is not a valid label key prefix: '{prefix}'")
        self.spec = {"kind": "label", "prefix": prefix, "input": ref.path}


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
            f"{term}() takes a selector (label/metric/meta/resource/load), "
            f"got: {type(value).__name__}",
        )
    if not isinstance(value.spec, str):
        raise ValueError(
            f"label_for() keys resolve to label strings, which have no ordering — "
            f"use it in prefer() or require(), not in {term}()",
        )
    return value


def prefer(condition: Condition | dict, *, weight: float = 1.0) -> dict:
    """Boolean preference: 1.0 when the condition holds, else 0.

    ``prefer(label("region") == input("region"), weight=10)``

    Also accepts dynamic-key comparisons and ``service(...)`` — the soft
    counterparts of their ``require(...)`` forms:
    ``prefer(label_for("cache.", input("dataset")) == "true", weight=5)``
    prefers workers with a warm copy without excluding the rest.
    """
    if isinstance(condition, dict) and condition.get("kind") == "match":
        # service(...) compiles to a match term; re-shape it as a preference.
        return {
            "kind": "prefer",
            "selector": condition.get("selector"),
            "op": condition.get("op"),
            "value": condition.get("value"),
            "weight": _validate_weight(weight),
        }
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


_SCORE_TERM_KINDS = ("prefer", "least", "most", "sticky")


def score(*terms: dict) -> dict:
    """Compose terms into a routing policy spec (stored in workflow metadata)."""
    if not terms:
        raise ValueError("score() requires at least one term")
    for term in terms:
        if not isinstance(term, dict):
            raise ValueError(
                f"score() accepts only prefer()/least()/most()/sticky()/when() terms, "
                f"got: {term!r}",
            )
        if term.get("kind") == "when":
            then = term.get("then")
            if not isinstance(then, dict) or then.get("kind") not in _SCORE_TERM_KINDS:
                raise ValueError(
                    "when(...) in score() must wrap a prefer()/least()/most()/sticky() "
                    "term; label comparisons belong in require()",
                )
        elif term.get("kind") not in _SCORE_TERM_KINDS:
            raise ValueError(
                f"score() accepts only prefer()/least()/most()/sticky()/when() terms, "
                f"got: {term!r}",
            )
    return {"terms": list(terms)}


# ---------------------------------------------------------------------------
# require(...): affinity expressions (the filter stage)
# ---------------------------------------------------------------------------
#
#     @workflow.with_options(
#         affinity=require(
#             service(input("model")),
#             label_for("sku.", input("model")) == "true",
#             optional(label("node") == input("node")),
#             when(input("tier") == "dedicated", label("cap.dedicated") == "true"),
#         ),
#     )
#
# Terms are AND-ed, ops are == and != only, and evaluation is fail-closed: a
# term whose input cannot be resolved fails the match, except under
# optional(...) (skip on unresolved input) and when(...) (inactive on an
# unresolved condition). ``!=`` treats an absent label as passing (absent ≠
# value) — the one deliberate inversion, so maintenance-window style terms
# work without every worker carrying the label. Resolved input values are
# compared against labels/metadata as strings (bools as "true"/"false").
# ``meta(...)`` terms read the server-held metadata dict instead of labels —
# the control-plane-authoritative channel a worker cannot advertise into.

_REQUIRE_OPS = ("==", "!=")


def label_for(prefix: str, ref: InputRef) -> DynamicLabel:
    """Label selector whose key is ``prefix`` completed from execution input.

    ``label_for("sku.", input("model")) == "true"`` checks label
    ``sku.<model> == "true"`` on each candidate. Inputs never create labels,
    only test them; the resolved key must be a valid label key.
    """
    return DynamicLabel(prefix, ref)


def service(name: str | InputRef) -> dict:
    """Target workers holding a granted, runner-verified service socket.

    A hard wall as a ``require(...)`` term, a soft preference inside
    ``prefer(...)``. Sugar for
    ``label_for("flux.service.", name) == "true"``. Because the
    ``flux.`` label prefix is reserved (workers reject user labels under it),
    this is a capability grant a worker cannot fabricate.
    """
    if isinstance(name, InputRef):
        selector: Any = DynamicLabel(SERVICE_LABEL_PREFIX, name).spec
    elif isinstance(name, str) and name:
        if not is_valid_service_name(name):
            raise ValueError(
                f"service() name '{name}' is invalid: use lowercase letters, digits, "
                f"and single hyphens (max {MAX_SERVICE_NAME_LENGTH} chars) — it must "
                f"match a worker's granted socket name",
            )
        selector = f"label:{SERVICE_LABEL_PREFIX}{name}"
    else:
        raise ValueError(
            f"service() takes a service name or input(...), got: {type(name).__name__}",
        )
    return {"kind": "match", "selector": selector, "op": "==", "value": "true"}


def _compile_match(term: Any, context: str) -> dict:
    """Normalize a require term (a label Condition or a compiled dict) into
    its ``{"kind": "match", ...}`` spec."""
    if isinstance(term, dict):
        if term.get("kind") == "match":
            return dict(term)
        raise ValueError(f"{context} takes a label comparison or service(...), got: {term!r}")
    if not isinstance(term, Condition):
        raise ValueError(
            f"{context} takes a label comparison, e.g. "
            f'label("region") == input("region"), got: {type(term).__name__}',
        )
    spec = term.selector.spec
    is_matchable = (
        isinstance(spec, str) and (spec.startswith("label:") or spec.startswith("meta:"))
    ) or (isinstance(spec, dict) and spec.get("kind") == "label")
    if not is_matchable:
        raise ValueError(
            f"require() terms compare labels or metadata (label()/label_for()/meta()); "
            f"metric()/resource()/load() belong to requests= and routing=, got: '{spec}'",
        )
    if term.op not in _REQUIRE_OPS:
        raise ValueError(
            f"require() supports only == and != (ordered comparisons on labels are "
            f"stringly-typed traps; use routing= for metrics), got: '{term.op}'",
        )
    return {"kind": "match", "selector": spec, "op": term.op, "value": term.value}


def optional(term: Condition | dict) -> dict:
    """Present-or-skip: the term is skipped when its input is absent, but a
    resolved comparison that is false still fails the match — and input that
    resolves to something invalid (bad label key, non-scalar, invalid service
    name) fails and diagnoses like a bare term."""
    compiled = _compile_match(term, "optional()")
    compiled["optional"] = True
    return compiled


def when(condition: InputCondition, term: Condition | dict) -> dict:
    """Apply ``term`` only when an input condition holds.

    The condition resolves from execution input only — it gates on the
    requester's intent, never on worker attributes. An unresolved condition
    leaves the term inactive (a widening construct by design, unlike bare
    require terms, which fail closed).

    Valid in both stages: wrapping a label comparison (or ``service(...)``)
    it gates a ``require(...)`` term; wrapping a ``prefer()``/``least()``/
    ``most()``/``sticky()`` term it gates a ``score(...)`` term.
    """
    if not isinstance(condition, InputCondition):
        raise ValueError(
            "when() condition must compare input(...) against a constant, e.g. "
            f'when(input("tier") == "dedicated", ...), got: {type(condition).__name__}',
        )
    if isinstance(term, dict) and term.get("kind") in _SCORE_TERM_KINDS:
        then = dict(term)
    else:
        then = _compile_match(term, "when()")
    return {
        "kind": "when",
        "if": {"input": condition.ref.path, "op": condition.op, "value": condition.value},
        "then": then,
    }


def require(*terms: Condition | dict) -> list[dict]:
    """Compose terms into an affinity expression (stored as the workflow's
    ``affinity``). All terms must hold (AND); see the module docs for the
    fail-closed semantics."""
    if not terms:
        raise ValueError("require() requires at least one term")
    compiled: list[dict] = []
    for term in terms:
        if isinstance(term, dict) and term.get("kind") == "when":
            then = term.get("then")
            if not isinstance(then, dict) or then.get("kind") != "match":
                raise ValueError(
                    "when(...) wrapping a scoring term is only valid in score(); "
                    "require() terms are label comparisons",
                )
            compiled.append(term)
        else:
            compiled.append(_compile_match(term, "require()"))
    return compiled


# Guardrails for admin-written worker metadata. Kept beside the metrics
# validator so every writer shares one rule; unlike metrics (a hint channel),
# invalid metadata raises — it is an operator command channel.
MAX_METADATA_KEYS = 64
MAX_METADATA_KEY_LENGTH = 64
MAX_METADATA_VALUE_LENGTH = 256


def validate_worker_metadata(payload: Any) -> dict[str, str | float]:
    """Sanitize an admin-written metadata mapping into ``dict[str, str | float]``.

    Keys are label-shaped; values are strings (bounded), finite numbers
    (stored as float), or booleans (stored as "true"/"false" to match label
    conventions). Raises ``ValueError`` naming the offending key.
    """
    if not isinstance(payload, dict):
        raise ValueError("metadata must be an object of key/value pairs")
    if len(payload) > MAX_METADATA_KEYS:
        raise ValueError(f"metadata is limited to {MAX_METADATA_KEYS} keys")
    metadata: dict[str, str | float] = {}
    for key, value in payload.items():
        if (
            not isinstance(key, str)
            or not key
            or len(key) > MAX_METADATA_KEY_LENGTH
            or not _LABEL_KEY_RE.match(key)
        ):
            raise ValueError(f"invalid metadata key: {key!r}")
        if isinstance(value, bool):
            metadata[key] = "true" if value else "false"
        elif isinstance(value, (int, float)):
            value = float(value)
            if not math.isfinite(value):
                raise ValueError(f"metadata value for '{key}' must be finite")
            metadata[key] = value
        elif isinstance(value, str):
            if len(value) > MAX_METADATA_VALUE_LENGTH:
                raise ValueError(
                    f"metadata value for '{key}' exceeds {MAX_METADATA_VALUE_LENGTH} chars",
                )
            metadata[key] = value
        else:
            raise ValueError(
                f"metadata value for '{key}' must be a string, number, or boolean",
            )
    return metadata


def validate_worker_metrics(payload: Any, max_keys: int = MAX_METRICS) -> dict[str, float] | None:
    """Sanitize a worker-advertised metrics mapping. Returns None when the
    payload is unusable — metrics are a hint channel, never an error channel."""
    if not isinstance(payload, dict) or len(payload) > max_keys:
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
    if kind == "meta":
        return (getattr(worker, "metadata", None) or {}).get(key)
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
        if term.get("kind") == "when":
            # Input-gated score term: inactive conditions skip it; a
            # malformed condition degrades the whole policy like any other
            # malformed term.
            active = _when_condition_active(term, input_value)
            if isinstance(active, str):
                logger.warning(f"Malformed routing term ignored: {term!r}")
                return None
            if not active:
                continue
            term = term.get("then")
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
        if kind == "prefer" and isinstance(selector, dict):
            # Dynamic label key (label_for/service): resolve once per term
            # against the execution input. Unresolved input or an invalid
            # resolved key means the term cannot discriminate — everyone
            # scores 0 for it — while a malformed spec degrades the policy.
            key, problem = _resolve_selector_key(selector, input_value)
            if problem is not None:
                if problem.category == "malformed":
                    logger.warning(f"Malformed routing term ignored: {term!r}")
                    return None
                continue
            selector = f"label:{key}"
        elif not isinstance(selector, str):
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


# ---------------------------------------------------------------------------
# require(...) evaluation (server-side, inside the dispatch paths)
# ---------------------------------------------------------------------------

_UNRESOLVED = object()  # sentinel: input path absent (None is a legal value)


def _resolve_require_input(input_value: Any, path: str) -> Any:
    current = input_value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _UNRESOLVED
        current = current[part]
    return current


def _label_value_str(value: Any) -> str:
    # Labels are strings; input-resolved values compare as their string form,
    # with bools lowercased to match label conventions ("true"/"false").
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class _Problem(NamedTuple):
    """A term-resolution problem — an execution-level fact (worker-independent)
    by construction.

    ``category`` drives what each consumer does with it:
    - "unresolved": the input path is absent — the one condition
      ``optional(...)`` forgives.
    - "invalid": the input resolved to something no worker could ever carry
      (bad label key, non-scalar, invalid service name) — fails and
      diagnoses even under ``optional(...)``.
    - "malformed": the spec itself is broken (hand-written metadata) —
      always fails, always diagnoses, degrades a scoring policy.
    """

    category: str
    message: str


def _resolve_selector_key(selector: Any, input_value: Any) -> tuple[str, _Problem | None]:
    """Resolve a label selector (static ``"label:key"`` or dynamic
    ``{"kind": "label", "prefix", "input"}``) to its label key.

    Returns ``(key, None)`` or ``("", problem)``."""
    if isinstance(selector, str) and selector.startswith("label:"):
        return selector[len("label:") :], None
    if isinstance(selector, dict) and selector.get("kind") == "label":
        prefix, path = selector.get("prefix"), selector.get("input")
        if not isinstance(prefix, str) or not prefix or not isinstance(path, str) or not path:
            return "", _Problem("malformed", f"malformed label selector: {selector!r}")
        resolved = _resolve_require_input(input_value, path)
        if resolved is _UNRESOLVED:
            return "", _Problem(
                "unresolved",
                f"label key requires input '{path}', which is not present",
            )
        if resolved is None or isinstance(resolved, (dict, list)):
            return "", _Problem("invalid", f"label key input '{path}' must be a scalar")
        fragment = _label_value_str(resolved)
        if prefix == SERVICE_LABEL_PREFIX and not is_valid_service_name(fragment):
            # A name worker registration could never grant would otherwise
            # park the execution forever instead of failing fast.
            return "", _Problem(
                "invalid",
                f"input '{path}' resolves to an invalid service name: '{fragment}'",
            )
        key = prefix + fragment
        if len(key) > MAX_LABEL_KEY_LENGTH or not _LABEL_KEY_RE.match(key):
            return "", _Problem(
                "invalid",
                f"input '{path}' resolves to an invalid label key: '{key}'",
            )
        return key, None
    return "", _Problem("malformed", f"malformed label selector: {selector!r}")


def _resolve_require_term(term: dict, input_value: Any) -> tuple[str, str, str] | _Problem:
    """Resolve a match term's selector kind, key, and comparison value against
    the execution input. Returns ``(kind, key, value)`` — kind is ``"label"``
    or ``"meta"`` — or a :class:`_Problem`."""
    selector = term.get("selector")
    if isinstance(selector, str) and selector.startswith("meta:"):
        kind, key = "meta", selector[len("meta:") :]
    else:
        kind = "label"
        key, problem = _resolve_selector_key(selector, input_value)
        if problem is not None:
            if problem.category == "malformed":
                return problem
            return _Problem(problem.category, f"affinity {problem.message}")

    if term.get("op") not in _REQUIRE_OPS:
        return _Problem("malformed", f"malformed affinity term op: {term.get('op')!r}")

    value = term.get("value")
    if isinstance(value, dict):
        path = value.get("$input")
        if not isinstance(path, str) or not path:
            return _Problem("malformed", f"malformed affinity value: {value!r}")
        value = _resolve_require_input(input_value, path)
        if value is _UNRESOLVED:
            return _Problem(
                "unresolved",
                f"affinity term on {kind} '{key}' requires input '{path}', which is not present",
            )
    return kind, key, _label_value_str(value)


def _when_condition_active(term: dict, input_value: Any) -> bool | str:
    """Whether a when-term's condition holds. Unresolved input leaves the
    term inactive (False); a malformed condition is a problem string."""
    cond = term.get("if")
    if not isinstance(cond, dict):
        return f"malformed affinity when-condition: {cond!r}"
    path, op = cond.get("input"), cond.get("op")
    if not isinstance(path, str) or not path or op not in _REQUIRE_OPS:
        return f"malformed affinity when-condition: {cond!r}"
    resolved = _resolve_require_input(input_value, path)
    if resolved is _UNRESOLVED:
        return False
    expected = cond.get("value")
    return (resolved == expected) if op == "==" else (resolved != expected)


def require_diagnostic(terms: Any, input_value: Any) -> str | None:
    """Execution-level reason this affinity expression can never match, or None.

    Unresolved input on a non-optional term, an invalid resolved label key,
    and a malformed spec are properties of the execution alone — no worker
    could ever satisfy them — so dispatch fails the execution with this
    message instead of parking it forever. A plain label mismatch is
    fleet-dependent (a matching worker may join) and never diagnosed here.
    """
    if not isinstance(terms, (list, tuple)) or not terms:
        # require() demands at least one term, so an empty list is
        # hand-written metadata — malformed, not match-everything.
        return f"malformed affinity expression: {terms!r}"
    for term in terms:
        if not isinstance(term, dict):
            return f"malformed affinity term: {term!r}"
        if term.get("kind") == "when":
            active = _when_condition_active(term, input_value)
            if isinstance(active, str):
                return active
            if not active:
                continue
            term = term.get("then")
            if not isinstance(term, dict) or term.get("kind") != "match":
                return f"malformed affinity when-term body: {term!r}"
        elif term.get("kind") != "match":
            return f"malformed affinity term: {term!r}"
        resolved = _resolve_require_term(term, input_value)
        if isinstance(resolved, _Problem):
            # optional(...) forgives absent input — nothing else: an invalid
            # resolved key or a malformed spec diagnoses regardless.
            if resolved.category == "unresolved" and term.get("optional"):
                continue
            return resolved.message
    return None


def require_matches(
    terms: Any,
    worker_labels: dict[str, str] | None,
    input_value: Any,
    worker_metadata: dict[str, Any] | None = None,
) -> bool:
    """Whether a worker's labels and server-held metadata satisfy a
    require(...) affinity expression.

    Fail-closed: any problem (unresolved input on a non-optional term,
    invalid key, malformed spec) fails the match — mirroring
    ``require_diagnostic``, which turns those same problems into a terminal
    dispatch error so fail-closed never means queue-forever.
    """
    if not isinstance(terms, (list, tuple)) or not terms:
        return False
    labels = worker_labels or {}
    metadata = worker_metadata or {}
    for term in terms:
        if not isinstance(term, dict):
            return False
        if term.get("kind") == "when":
            active = _when_condition_active(term, input_value)
            if isinstance(active, str):
                return False
            if not active:
                continue
            term = term.get("then")
            if not isinstance(term, dict) or term.get("kind") != "match":
                return False
        elif term.get("kind") != "match":
            return False
        resolved = _resolve_require_term(term, input_value)
        if isinstance(resolved, _Problem):
            if resolved.category == "unresolved" and term.get("optional"):
                continue
            return False
        kind, key, value = resolved
        raw = metadata.get(key) if kind == "meta" else labels.get(key)
        # Metadata values may be numeric; they compare in string form like
        # everything else in require() (rank numerics in routing= instead).
        actual = None if raw is None else _label_value_str(raw)
        if term.get("op") == "==":
            if actual != value:
                return False
        # Absent ≠ value holds by design: a worker with no such label passes
        # a != term (the documented inversion of fail-closed).
        elif actual == value:
            return False
    return True
