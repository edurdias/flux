"""Workflows sealed in the docker-airgapped runner.

``docker-airgapped`` is the runner for code you do not trust — model-authored
(dynamic) workflows above all. Each execution runs in a container whose only
capability channel is the stdio protocol to the parent worker: no network
(``--network=none``), read-only rootfs with a size-capped tmpfs ``/tmp``,
no capabilities, no privilege escalation, pids/memory/cpu limits, and a
wall-clock ceiling. Checkpoints, secrets, configs, and approvals all flow
through the worker, where every request is permission-checked.

Workers opt in explicitly:

    [flux.workers]
    runners = ["docker-airgapped"]
    airgapped_image = "<registry>/flux:<version-matching-the-worker>"

Three capabilities can be granted — each only through its named config key,
so ``flux.toml`` is the audit trail of opened surfaces:

    airgapped_gpus = "all"                      # compute, no data path out
    airgapped_mounts = ["/srv/assets:/assets"]  # read-only, forced
    airgapped_shm_size = "8g"                   # /dev/shm for large buffers

Pinning ``runner="docker-airgapped"`` also constrains dispatch: the workflow
only goes to workers advertising the sealed runner. (Dynamically registered
workflows get this pin stamped server-side via
``[flux.dynamic_workflows] require_runner`` — authors can't opt out.)

Runner selection is a dispatch concern; running these inline
(``workflow.run()``, as in the tests) executes in the current process, so
each example degrades gracefully outside the container.
"""

from __future__ import annotations

import re
from pathlib import Path

from flux import ExecutionContext
from flux.task import task
from flux.workflow import workflow

# Inside the sealed container this path exists only if the operator granted
# it via airgapped_mounts; read-only is forced by the runner.
ASSETS_DIR = Path("/assets")

_BUILTIN_STOPWORDS = frozenset({"a", "an", "and", "in", "is", "of", "or", "the", "to"})

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")


@task
async def tokenize(text: str) -> list[str]:
    return [word.strip(".,;:!?\"'()").lower() for word in text.split()]


@task
async def load_stopwords() -> list[str]:
    """Read reference data from a mounted read-only asset, if granted.

    The mount is an *input* channel: data can enter the sandbox, results
    still leave only through the worker-mediated stdio protocol. Without the
    grant (or when running inline) the built-in fallback keeps the workflow
    functional.
    """
    stopwords_file = ASSETS_DIR / "stopwords.txt"
    if stopwords_file.is_file():
        return [line.strip() for line in stopwords_file.read_text().splitlines() if line.strip()]
    return sorted(_BUILTIN_STOPWORDS)


@task
async def count_keywords(words: list[str], stopwords: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    excluded = set(stopwords)
    for word in words:
        if word and word not in excluded:
            counts[word] = counts.get(word, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


@workflow.with_options(runner="docker-airgapped")
async def sealed_keyword_count(ctx: ExecutionContext[str]):
    """The untrusted-code shape: arbitrary text processing, fully sealed.

    Even if this code were adversarial it could not phone home (no network),
    tamper with the host (read-only rootfs, no mounts unless granted
    read-only), or exhaust the worker (memory/cpu/pids/wall-clock limits).
    """
    if not ctx.input:
        raise TypeError("Input not provided")
    words = await tokenize(ctx.input)
    stopwords = await load_stopwords()
    return await count_keywords(words, stopwords)


@task
async def redact(text: str) -> dict[str, str | int]:
    """Mask emails and phone numbers before anything leaves the sandbox."""
    redactions = 0

    def _mask(match: re.Match[str]) -> str:
        nonlocal redactions
        redactions += 1
        return "[REDACTED]"

    masked = _EMAIL.sub(_mask, text)
    masked = _PHONE.sub(_mask, masked)
    return {"text": masked, "redactions": redactions}


@workflow.with_options(runner="docker-airgapped")
async def sealed_redact(ctx: ExecutionContext[str]):
    """Privacy-preserving processing of sensitive text.

    The raw input reaches the container through the worker's stdio channel
    and is processed with no network to leak it through; the only thing
    that leaves is this return value, checkpointed through the parent
    worker — already masked.
    """
    if not ctx.input:
        raise TypeError("Input not provided")
    return await redact(ctx.input)


if __name__ == "__main__":  # pragma: no cover
    ctx = sealed_keyword_count.run("the quick brown fox jumps over the lazy dog")
    print(ctx.to_json())
    ctx = sealed_redact.run("Contact Ada at ada@example.com or +1 (555) 010-9999 for access.")
    print(ctx.to_json())
