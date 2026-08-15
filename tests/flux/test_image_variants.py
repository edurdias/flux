"""The published image variants and the guard that keeps them honest (#247).

The variant matrix lives in `.github/workflows/docker-publish.yml`, which no
CI job exercises before it publishes. These tests pin the two things that
would otherwise only fail in production: the guard's own logic, and the
workflow declaring the variants the guard knows about.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "docker-publish.yml"

_spec = importlib.util.spec_from_file_location(
    "verify_image_variant",
    ROOT / "docker" / "scripts" / "verify_image_variant.py",
)
assert _spec and _spec.loader
verifier = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verifier)


@pytest.fixture(scope="module")
def matrix() -> list[dict]:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    [job] = [job for name, job in workflow["jobs"].items() if "strategy" in job]
    return job["strategy"]["matrix"]["variant"]


class TestGuard:
    def test_slim_rejects_an_image_carrying_ai_clients(self, monkeypatch):
        monkeypatch.setattr(verifier, "installed", lambda module: module == "openai")

        problems = verifier.verify("slim")

        assert problems == ["openai: present, must not be"]

    def test_server_rejects_ai_clients_and_requires_its_own_extras(self, monkeypatch):
        monkeypatch.setattr(
            verifier,
            "installed",
            lambda module: module in ("anthropic", "psycopg"),
        )

        problems = verifier.verify("server")

        assert "anthropic: present, must not be" in problems
        assert "opentelemetry.sdk: missing, must be installed" in problems
        assert not any("psycopg" in problem for problem in problems)

    def test_full_requires_every_extra(self, monkeypatch):
        monkeypatch.setattr(verifier, "installed", lambda module: module != "ollama")

        assert verifier.verify("full") == ["ollama: missing, must be installed"]

    def test_a_matching_image_reports_no_problems(self, monkeypatch):
        monkeypatch.setattr(verifier, "installed", lambda module: False)

        assert verifier.verify("slim") == []

    def test_unknown_variant_is_a_usage_error(self):
        assert verifier.main(["verify", "worker"]) == 2

    def test_slim_tolerates_the_opentelemetry_api_a_base_install_carries(self, monkeypatch):
        """A no-extras install still lands `opentelemetry-api`; the extra's
        real weight is the SDK and exporters, so those are what slim forbids."""
        monkeypatch.setattr(
            verifier,
            "installed",
            lambda module: module == "opentelemetry",
        )

        assert verifier.verify("slim") == []

    def test_namespace_parent_absence_is_not_an_error(self, monkeypatch):
        """`google.genai` raises rather than returning None when `google` is
        absent; the guard must read that as "not installed"."""

        def _raise(module, *_args, **_kwargs):
            raise ValueError(module)

        monkeypatch.setattr(importlib.util, "find_spec", _raise)

        assert verifier.installed("google.genai") is False


class TestPublishMatrix:
    def test_every_declared_variant_is_one_the_guard_knows(self, matrix):
        assert {entry["name"] for entry in matrix} == set(verifier.VARIANTS)

    def test_only_the_default_variant_is_unsuffixed(self, matrix):
        unsuffixed = [entry["name"] for entry in matrix if not entry.get("suffix")]

        assert unsuffixed == ["full"], "exactly one variant may own the bare tags"

    def test_extras_match_each_variant_s_promise(self, matrix):
        extras = {entry["name"]: entry["extras"] for entry in matrix}

        assert extras["slim"] == ""
        assert "ai" not in extras["server"]
        assert "postgresql" in extras["server"] and "observability" in extras["server"]
        assert "ai" in extras["full"]

    def test_each_variant_caches_under_its_own_scope(self, matrix):
        """A shared gha cache scope makes three variants evict each other, so
        every build would run cold."""
        scopes = [entry["name"] for entry in matrix]

        assert len(set(scopes)) == len(scopes)
