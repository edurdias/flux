#!/usr/bin/env python3
"""Assert a published image variant carries exactly the extras it claims.

Run *inside* the built image, before it is pushed:

    docker run --rm -v "$PWD/docker/scripts:/scripts:ro" <image> \\
        python /scripts/verify_image_variant.py slim

Packaging intent that is only documented drifts: promote one dependency from
an extra to a base requirement and the slim image silently refills the
sandbox a runner child runs in. This turns that into a failed publish.

Importability is checked with ``find_spec`` rather than a real import, so the
check is about what is installed, not about import side effects.
"""

from __future__ import annotations

import importlib.util
import sys

# Import names, not distribution names: `google-genai` imports as
# `google.genai`, `psycopg-binary` as `psycopg`.
AI_CLIENTS = ("openai", "anthropic", "ollama", "google.genai")
POSTGRESQL = ("psycopg",)
# `opentelemetry.sdk`, not the `opentelemetry` namespace: a no-extras install
# still lands `opentelemetry-api` (verified against a built image, and
# consistent with the package delta measured in #247), while the SDK and the
# exporters — the weight the observability extra actually adds — stay out.
OBSERVABILITY = ("opentelemetry.sdk", "opentelemetry.exporter")

VARIANTS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # variant: (must be absent, must be present)
    "slim": (AI_CLIENTS + POSTGRESQL + OBSERVABILITY, ()),
    "server": (AI_CLIENTS, POSTGRESQL + OBSERVABILITY),
    "full": ((), AI_CLIENTS + POSTGRESQL + OBSERVABILITY),
}


def installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        # A namespace parent that is absent raises rather than returning None.
        return False


def verify(variant: str) -> list[str]:
    forbidden, required = VARIANTS[variant]
    problems = [f"{module}: present, must not be" for module in forbidden if installed(module)]
    problems += [
        f"{module}: missing, must be installed" for module in required if not installed(module)
    ]
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in VARIANTS:
        print(f"usage: {argv[0]} {{{'|'.join(VARIANTS)}}}", file=sys.stderr)
        return 2

    variant = argv[1]
    problems = verify(variant)
    if problems:
        print(f"image does not match the '{variant}' variant:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f"image matches the '{variant}' variant")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv))
