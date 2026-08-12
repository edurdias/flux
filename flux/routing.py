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
``label_for(...)``/``meta_for(...)`` dynamic keys, and ``service(...)`` work
in ``require`` terms and in ``prefer()``; ``when(input(...) == const, term)``
gates a term in either stage. The same comparison is a hard wall under ``require`` and a
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

    # The compiled spec has three encodings for a value reference, and each
    # reads one of these keys. Subclassing for routing_input() rather than
    # branching keeps every isinstance(..., InputRef) check working and makes
    # a missed encoding a KeyError at authoring rather than a silent read of
    # the wrong source (#211).
    spec_key = "input"
    wrapper_key = "$input"
    factory_name = "input"

    def __init__(self, path: str):
        if not path or not isinstance(path, str):
            raise ValueError(f"{self.factory_name}() requires a non-empty string path")
        self.path = path

    def to_spec(self) -> dict[str, str]:
        return {self.wrapper_key: self.path}

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


class RoutingInputRef(InputRef):
    """A value resolved from the execution's routing input at dispatch time.

    Identical to :class:`InputRef` in every way except where the value comes
    from: routing input is stored on its own column and never delivered to the
    worker, so an expression can discriminate on a value the target cannot
    observe (issue #211).
    """

    spec_key = "routing_input"
    wrapper_key = "$routing_input"
    factory_name = "routing_input"


def routing_input(path: str) -> RoutingInputRef:
    """Reference a routing-only value: matched by dispatch, never delivered.

    Named for the column, header, schedule field, ``call()`` kwarg and CLI flag
    that set it — ``routing(...)`` would collide with the scoring policy that
    ``@workflow.with_options(routing=...)`` takes.
    """
    return RoutingInputRef(path)


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


# The only selectors a when() condition may gate on. Both resolve from the
# server's own execution count rather than a worker-supplied value — utilization
# also divides by the worker's advertised capacity, but _has_free_slot already
# trusts that number as a hard admission gate, so it grants no new leverage.
_SERVER_EVALUATED_SELECTORS = ("load", "utilization")


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
        if kind in _SERVER_EVALUATED_SELECTORS:
            self.spec = kind
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


def utilization() -> Selector:
    """Active executions as a fraction of the worker's advertised capacity.

    ``load()`` is an absolute count, so a threshold tuned for a 4-slot worker
    means something else on a 32-slot one. This is the portable form for a
    mixed fleet. Resolves to ``None`` — matching nothing, rather than reading
    as idle — on a worker that advertises no ``max_concurrent_executions``.
    """
    return Selector("utilization")


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
        self.spec = {"kind": "label", "prefix": prefix, ref.spec_key: ref.path}


class DynamicMeta(Selector):
    """A metadata selector whose key is completed from execution input.

    ``meta_for("approved.", input("artefact"))`` inspects server-held
    metadata key ``approved.<artefact>`` on each candidate worker — the
    control-plane-authoritative twin of :class:`DynamicLabel` (issue #158).
    Same prefix-is-mandatory rule; the resolved key is held to the metadata
    key shape and bound (``validate_worker_metadata``), so a term can never
    name a key the admin API could not have written. Valid in
    ``require(...)`` terms and in ``prefer()``; not in ``least()``/
    ``most()`` (the dynamic-key numeric variant stays out of scope).
    """

    def __init__(self, prefix: str, ref: InputRef):
        if not prefix or not isinstance(prefix, str):
            raise ValueError("meta_for() requires a non-empty string prefix")
        if not isinstance(ref, InputRef):
            raise ValueError(
                f"meta_for() key must be completed from input(...), got: {type(ref).__name__}",
            )
        # Same rule as label_for: the prefix must be a valid key head so no
        # input value can produce a valid key from an invalid namespace.
        if not _LABEL_KEY_RE.match(prefix.rstrip("._-") or ""):
            raise ValueError(f"meta_for() prefix is not a valid metadata key prefix: '{prefix}'")
        # A prefix at or past the metadata key bound leaves no room for the
        # input fragment — no resolution could ever produce a valid key, so
        # fail at authoring time (mirrors meta(key) validation).
        if len(prefix) >= MAX_METADATA_KEY_LENGTH:
            raise ValueError(
                f"meta_for() prefix exceeds the metadata key bound "
                f"({MAX_METADATA_KEY_LENGTH} chars): '{prefix}'",
            )
        self.spec = {"kind": "meta", "prefix": prefix, ref.spec_key: ref.path}


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
            f"label_for()/meta_for() dynamic keys are valid in prefer() and "
            f"require(), not in {term}()",
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
# ``meta(...)``/``meta_for(...)`` terms read the server-held metadata dict
# instead of labels — the control-plane-authoritative channel a worker cannot
# advertise into.

_REQUIRE_OPS = ("==", "!=")


def label_for(prefix: str, ref: InputRef) -> DynamicLabel:
    """Label selector whose key is ``prefix`` completed from execution input.

    ``label_for("sku.", input("model")) == "true"`` checks label
    ``sku.<model> == "true"`` on each candidate. Inputs never create labels,
    only test them; the resolved key must be a valid label key.
    """
    return DynamicLabel(prefix, ref)


def meta_for(prefix: str, ref: InputRef) -> DynamicMeta:
    """Metadata selector whose key is ``prefix`` completed from execution input.

    ``meta_for("approved.", input("artefact")) == "true"`` checks server-held
    metadata ``approved.<artefact>`` on each candidate — the authoritative
    counterpart of ``label_for`` for facts the control plane asserts about a
    worker (workers have no write path into metadata). Inputs never create
    metadata, only test it; the resolved key must be a valid metadata key.
    Comparison semantics follow ``meta(...)``: typed values, ordered ops
    allowed, and ``!=`` treats an absent key as passing.
    """
    return DynamicMeta(prefix, ref)


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
    is_meta = (isinstance(spec, str) and spec.startswith("meta:")) or (
        isinstance(spec, dict) and spec.get("kind") == "meta"
    )
    is_matchable = (
        is_meta
        or (isinstance(spec, str) and spec.startswith("label:"))
        or (isinstance(spec, dict) and spec.get("kind") == "label")
    )
    if not is_matchable:
        raise ValueError(
            f"require() terms compare labels or metadata "
            f"(label()/label_for()/meta()/meta_for()); "
            f"metric()/resource()/load() belong to requests= and routing=, got: '{spec}'",
        )
    # Labels are stringly-typed, so ordered comparisons stay out; metadata is
    # properly typed (str|number), so meta() terms may use the ordered ops.
    allowed_ops = _OPS if is_meta else _REQUIRE_OPS
    if term.op not in allowed_ops:
        raise ValueError(
            f"require() label terms support only == and != (ordered comparisons on labels "
            f"are stringly-typed traps); use meta() or routing=, got: '{term.op}'",
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


def when(condition: Any, term: Condition | dict) -> dict:
    """Apply ``term`` only when ``condition`` holds.

    The condition may be ``input(...)`` (requester intent, per-execution,
    ``==``/``!=`` only), a ``meta(...)``/``metric(...)`` comparison (dynamic
    worker state, per-worker, any operator), or — in the score stage only —
    ``load()``/``utilization()``.

    ``label()`` and ``resource()`` are not valid conditions: their values are
    worker-asserted with no server-side counterpart, so gating on them invites
    a self-dodge. ``load()`` is counted by the server from its own execution
    table; ``utilization()`` divides that count by the worker's advertised
    capacity, which dispatch already trusts as a hard admission gate.

    Valid in both stages: wrapping a require match term it gates a
    ``require(...)`` term; wrapping a ``prefer()``/``least()``/``most()``/
    ``sticky()`` term it gates a ``score(...)`` term.
    """
    is_score_term = isinstance(term, dict) and term.get("kind") in _SCORE_TERM_KINDS
    if isinstance(condition, InputCondition):
        cond_spec = {
            condition.ref.spec_key: condition.ref.path,
            "op": condition.op,
            "value": condition.value,
        }
    elif isinstance(condition, Condition):
        spec = condition.selector.spec
        server_computed = spec in _SERVER_EVALUATED_SELECTORS
        if not (
            isinstance(spec, str)
            and (spec.startswith("meta:") or spec.startswith("metric:") or server_computed)
        ):
            raise ValueError(
                "when() worker conditions use meta(), metric(), load() or utilization(); "
                "label()/resource() are not valid when() conditions",
            )
        if server_computed and not is_score_term:
            # Skipping a require term makes a worker match MORE easily, so a
            # load-gated hard constraint would drop its requirement on exactly
            # the busy workers it was meant to steer away from.
            raise ValueError(
                f"{spec}() gates score terms only — prefer()/least()/most()/sticky(); "
                "it cannot gate a require() term",
            )
        if isinstance(condition.value, dict) and "$input" in condition.value:
            raise ValueError("when() conditions compare against a constant, not input(...)")
        cond_spec = {"selector": spec, "op": condition.op, "value": condition.value}
    else:
        raise ValueError(
            "when() condition must be input(...), meta(...), metric(...), load() or "
            f"utilization() compared against a constant, got: {type(condition).__name__}",
        )
    if is_score_term:
        then = dict(term)  # type: ignore[arg-type]
    else:
        then = _compile_match(term, "when()")
    return {"kind": "when", "if": cond_spec, "then": then}


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


_WRAPPED_REF_KEYS = ("$input", "$routing_input")
_BARE_REF_KEYS = ("input", "routing_input")


def _ref_path(spec: dict, keys: tuple[str, ...]) -> tuple[str | None, bool]:
    """``(path, from_routing_input)`` for a value reference in ``spec``.

    Every reader goes through here so adding an encoding cannot leave one
    resolving against ``input`` — which would silently read the wrong source
    for a value that exists to be unreadable (#211).
    """
    for key in keys:
        if key in spec:
            path = spec[key]
            return (path if isinstance(path, str) and path else None), key.endswith("routing_input")
    return None, False


def _ref_source(from_routing: bool, input_value: Any, routing_value: Any) -> Any:
    return routing_value if from_routing else input_value


def _resolve_input_path(input_value: Any, path: str) -> Any:
    current = input_value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _worker_utilization(worker: WorkerInfo, loads: dict[str, int]) -> float | None:
    """Active executions over advertised capacity, or None when unlimited.

    None rather than 0.0 on purpose: a worker that advertises no capacity is
    unknown, not idle, and reading it as idle would make one legacy worker
    look permanently free and collect every preference in the fleet.
    """
    cap = getattr(worker, "max_concurrent_executions", None)
    if not cap:
        return None
    return loads.get(worker.name, 0) / cap


def _selector_value(worker: WorkerInfo, selector: str, loads: dict[str, int]) -> Any:
    if selector == "load":
        return loads.get(worker.name, 0)
    if selector == "utilization":
        return _worker_utilization(worker, loads)
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
    routing_value: Any = None,
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

        applies_to = eligible
        if term.get("kind") == "when":
            cond = term.get("if")
            then = term.get("then")
            if not isinstance(then, dict):
                logger.warning(f"Malformed routing term ignored: {term!r}")
                return None
            if isinstance(cond, dict) and "input" in cond:
                # Input-gated score term: inactive conditions skip it; a
                # malformed condition degrades the whole policy like any
                # other malformed term.
                active = _when_condition_active(term, input_value, routing_value)
                if isinstance(active, str):
                    logger.warning(f"Malformed routing term ignored: {term!r}")
                    return None
                if not active:
                    continue
            else:
                # Worker-state (meta/metric) condition: evaluate per worker,
                # scoring only over the subset it holds for.
                applies_to = []
                for w in eligible:
                    a = _when_condition_active(
                        term,
                        input_value,
                        routing_value,
                        worker_labels=w.labels or {},
                        worker_metadata=getattr(w, "metadata", None) or {},
                        worker_metrics=getattr(w, "metrics", None) or {},
                        loads=loads,
                        worker=w,
                        has_worker=True,
                    )
                    if isinstance(a, str):
                        logger.warning(f"Malformed routing term ignored: {term!r}")
                        return None
                    if a:
                        applies_to.append(w)
                if not applies_to:
                    continue
            term = then

        kind = term.get("kind")
        weight = _as_float(term.get("weight", 1.0))
        if weight is None or weight <= 0:
            logger.warning(f"Malformed routing term ignored: {term!r}")
            return None

        if kind == "sticky":
            for w in applies_to:
                if preferred and w.name == preferred:
                    totals[w.name] += weight
            continue

        selector = term.get("selector")
        if kind == "prefer" and isinstance(selector, dict):
            # Dynamic key (label_for/meta_for/service): resolve once per term
            # against the execution input. Unresolved input or an invalid
            # resolved key means the term cannot discriminate — everyone
            # scores 0 for it — while a malformed spec degrades the policy.
            selector_kind, key, problem = _resolve_selector_key(
                selector,
                input_value,
                routing_value,
            )
            if problem is not None:
                if problem.category == "malformed":
                    logger.warning(f"Malformed routing term ignored: {term!r}")
                    return None
                continue
            selector = f"{selector_kind}:{key}"
        elif not isinstance(selector, str):
            logger.warning(f"Malformed routing term ignored: {term!r}")
            return None

        if kind == "prefer":
            op = term.get("op")
            if op not in _OPS:
                logger.warning(f"Malformed routing term ignored: {term!r}")
                return None
            right = term.get("value")
            if isinstance(right, dict):
                ref_path, from_routing = _ref_path(right, _WRAPPED_REF_KEYS)
                if ref_path:
                    right = _resolve_input_path(
                        _ref_source(from_routing, input_value, routing_value),
                        ref_path,
                    )
            for w in applies_to:
                if _compare(_selector_value(w, selector, loads), op, right):
                    totals[w.name] += weight
            continue

        if kind in ("least", "most"):
            values = {w.name: _as_float(_selector_value(w, selector, loads)) for w in applies_to}
            present = [v for v in values.values() if v is not None]
            if not present:
                continue  # nobody has the value; the term cannot discriminate
            lo, hi = min(present), max(present)
            for w in applies_to:
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


def _routing_blind(from_routing: bool, category: str, message: str) -> _Problem:
    """Problems from a routing reference say nothing about it.

    ``_fail_undispatchable`` writes these into ``executions.output``, which the
    execution read API returns to anything holding ``execution:*:read`` — the
    ``worker`` built-in role included. Naming the value would hand the worker
    the thing routing input exists to keep from it, and naming the key alone
    still distinguishes "absent" from "present but unmatched" (#211).
    """
    return _Problem(category, "routing constraint unsatisfied" if from_routing else message)


def _resolve_selector_key(
    selector: Any,
    input_value: Any,
    routing_value: Any = None,
) -> tuple[str, str, _Problem | None]:
    """Resolve a label/meta selector (static ``"label:key"`` or dynamic
    ``{"kind": "label"|"meta", "prefix", "input"}``) to its kind and key.

    Returns ``(kind, key, None)`` or ``(kind, "", problem)``."""
    if isinstance(selector, str) and selector.startswith("label:"):
        return "label", selector[len("label:") :], None
    if isinstance(selector, str) and selector.startswith("meta:"):
        # Callers usually branch on static meta selectors before reaching
        # here; handled anyway so the resolver's contract holds for every
        # selector shape it documents.
        return "meta", selector[len("meta:") :], None
    if isinstance(selector, dict) and selector.get("kind") in ("label", "meta"):
        kind = selector["kind"]
        prefix = selector.get("prefix")
        path, from_routing = _ref_path(selector, _BARE_REF_KEYS)
        if not isinstance(prefix, str) or not prefix or not path:
            return kind, "", _Problem("malformed", f"malformed {kind} selector: {selector!r}")
        resolved = _resolve_require_input(
            _ref_source(from_routing, input_value, routing_value),
            path,
        )
        if resolved is _UNRESOLVED:
            return (
                kind,
                "",
                _routing_blind(
                    from_routing,
                    "unresolved",
                    f"{kind} key requires input '{path}', which is not present",
                ),
            )
        if resolved is None or isinstance(resolved, (dict, list)):
            return (
                kind,
                "",
                _routing_blind(
                    from_routing,
                    "invalid",
                    f"{kind} key input '{path}' must be a scalar",
                ),
            )
        fragment = _label_value_str(resolved)
        if kind == "label" and prefix == SERVICE_LABEL_PREFIX:
            if not is_valid_service_name(fragment):
                # A name worker registration could never grant would otherwise
                # park the execution forever instead of failing fast.
                return (
                    kind,
                    "",
                    _routing_blind(
                        from_routing,
                        "invalid",
                        f"input '{path}' resolves to an invalid service name: '{fragment}'",
                    ),
                )
        key = prefix + fragment
        # Metadata keys are held to the admin-API bound (validate_worker_metadata)
        # so a term can never name a key that channel could not have written.
        max_length = MAX_LABEL_KEY_LENGTH if kind == "label" else MAX_METADATA_KEY_LENGTH
        if len(key) > max_length or not _LABEL_KEY_RE.match(key):
            return (
                kind,
                "",
                _Problem(
                    "invalid",
                    f"input '{path}' resolves to an invalid {kind} key: '{key}'",
                ),
            )
        return kind, key, None
    return "label", "", _Problem("malformed", f"malformed label selector: {selector!r}")


def _resolve_require_term(
    term: dict,
    input_value: Any,
    routing_value: Any = None,
) -> tuple[str, str, Any] | _Problem:
    """Resolve a match term's selector kind, key, and comparison value against
    the execution input. Returns ``(kind, key, value)`` — kind is ``"label"``
    or ``"meta"`` — or a :class:`_Problem`. Meta values stay raw (numeric
    comparisons need real numbers); label values stringify."""
    selector = term.get("selector")
    if isinstance(selector, str) and selector.startswith("meta:"):
        kind, key = "meta", selector[len("meta:") :]
    else:
        kind, key, problem = _resolve_selector_key(selector, input_value, routing_value)
        if problem is not None:
            if problem.category == "malformed":
                return problem
            return _Problem(problem.category, f"affinity {problem.message}")

    allowed_ops = _OPS if kind == "meta" else _REQUIRE_OPS
    if term.get("op") not in allowed_ops:
        return _Problem("malformed", f"malformed affinity term op: {term.get('op')!r}")

    value = term.get("value")
    if isinstance(value, dict):
        path, from_routing = _ref_path(value, _WRAPPED_REF_KEYS)
        if not path:
            return _Problem("malformed", f"malformed affinity value: {value!r}")
        value = _resolve_require_input(
            _ref_source(from_routing, input_value, routing_value),
            path,
        )
        if value is _UNRESOLVED:
            return _routing_blind(
                from_routing,
                "unresolved",
                f"affinity term on {kind} '{key}' requires input '{path}', which is not present",
            )
    # Meta keeps its raw value so numeric comparisons coerce correctly; labels
    # stringify (label semantics).
    if kind == "meta":
        return kind, key, value
    return kind, key, _label_value_str(value)


def _when_condition_active(
    term: dict,
    input_value: Any,
    routing_value: Any = None,
    *,
    worker_labels: dict[str, Any] | None = None,
    worker_metadata: dict[str, Any] | None = None,
    worker_metrics: dict[str, Any] | None = None,
    loads: dict[str, int] | None = None,
    worker: WorkerInfo | None = None,
    has_worker: bool = False,
) -> bool | str | None:
    """Whether a when-term's condition holds. Returns True/False, a string for a
    malformed condition, or None for a per-worker (meta/metric) condition asked
    without a worker (diagnostic context — the caller skips the term).

    ``input`` conditions resolve once per execution (unresolved → inactive/False,
    a widening construct). ``meta``/``metric`` conditions resolve per worker
    against the passed worker state.
    """
    cond = term.get("if")
    if not isinstance(cond, dict):
        return f"malformed affinity when-condition: {cond!r}"
    if any(k in cond for k in _BARE_REF_KEYS):
        path, from_routing = _ref_path(cond, _BARE_REF_KEYS)
        op = cond.get("op")
        if not path or op not in _REQUIRE_OPS:
            return f"malformed affinity when-condition: {cond!r}"
        resolved = _resolve_require_input(
            _ref_source(from_routing, input_value, routing_value),
            path,
        )
        if resolved is _UNRESOLVED:
            return False
        expected = cond.get("value")
        return (resolved == expected) if op == "==" else (resolved != expected)
    if "selector" in cond:
        selector, op = cond.get("selector"), cond.get("op")
        if (
            not isinstance(selector, str)
            or not (
                selector.startswith("meta:")
                or selector.startswith("metric:")
                or selector in _SERVER_EVALUATED_SELECTORS
            )
            or op not in _OPS
        ):
            return f"malformed affinity when-condition: {cond!r}"
        if selector in _SERVER_EVALUATED_SELECTORS:
            # when() confines these to the score stage, which always passes a
            # worker. Reaching here without one means hand-written metadata put
            # the condition in a require() expression — diagnose it so the
            # execution fails loudly instead of matching nobody and parking.
            if worker is None:
                return f"{selector}() gates score terms only: {cond!r}"
            return _compare(_selector_value(worker, selector, loads or {}), op, cond.get("value"))
        if not has_worker:
            return None  # fleet-dependent; not evaluable in diagnostic context
        kind, _, key = selector.partition(":")
        actual = (
            (worker_metadata or {}).get(key) if kind == "meta" else (worker_metrics or {}).get(key)
        )
        return _compare(actual, op, cond.get("value"))
    return f"malformed affinity when-condition: {cond!r}"


def require_diagnostic(terms: Any, input_value: Any, routing_value: Any = None) -> str | None:
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
            active = _when_condition_active(term, input_value, routing_value)
            if isinstance(active, str):
                return active
            if active is None or not active:
                continue
            term = term.get("then")
            if not isinstance(term, dict) or term.get("kind") != "match":
                return f"malformed affinity when-term body: {term!r}"
        elif term.get("kind") != "match":
            return f"malformed affinity term: {term!r}"
        resolved = _resolve_require_term(term, input_value, routing_value)
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
    *,
    routing_value: Any = None,
    worker_metrics: dict[str, Any] | None = None,
    loads: dict[str, int] | None = None,
) -> bool:
    """Whether a worker's labels and server-held metadata satisfy a
    require(...) affinity expression.

    Fail-closed: any problem (unresolved input on a non-optional term, invalid
    key, malformed spec) fails the match — mirroring ``require_diagnostic``,
    which turns those same problems into a terminal dispatch error so
    fail-closed never means queue-forever. ``worker_metrics``/``loads`` back
    ``when()`` conditions gating on worker state.
    """
    if not isinstance(terms, (list, tuple)) or not terms:
        return False
    labels = worker_labels or {}
    metadata = worker_metadata or {}
    for term in terms:
        if not isinstance(term, dict):
            return False
        if term.get("kind") == "when":
            active = _when_condition_active(
                term,
                input_value,
                routing_value,
                worker_labels=labels,
                worker_metadata=metadata,
                worker_metrics=worker_metrics or {},
                loads=loads or {},
                has_worker=True,
            )
            if isinstance(active, str) or active is None:
                return False
            if not active:
                continue
            term = term.get("then")
            if not isinstance(term, dict) or term.get("kind") != "match":
                return False
        elif term.get("kind") != "match":
            return False
        resolved = _resolve_require_term(term, input_value, routing_value)
        if isinstance(resolved, _Problem):
            if resolved.category == "unresolved" and term.get("optional"):
                continue
            return False
        kind, key, value = resolved
        op = term.get("op")
        if op not in _OPS:  # already validated by _resolve_require_term; narrows for mypy
            return False
        if kind == "meta":
            raw = metadata.get(key)
            if op == "!=":
                # Absent ≠ value holds by design (mirrors the label inversion).
                if raw is not None and _compare(raw, "==", value):
                    return False
            elif raw is None or not _compare(raw, op, value):
                return False
        else:  # label — string ==/!= with the absent-!= inversion (unchanged)
            raw = labels.get(key)
            actual = None if raw is None else _label_value_str(raw)
            if op == "==":
                if actual != value:
                    return False
            elif actual == value:
                return False
    return True
