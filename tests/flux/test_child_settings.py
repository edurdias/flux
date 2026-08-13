"""The parent-provided settings snapshot for runner children (issue #241).

The snapshot is how the sandbox reads configuration without importing
pydantic; the tests pin its three contracts: parity (the shim answers
exactly what the real settings would), secrecy (credentials the child env
sanitization strips must not ride back in), and fallback (no snapshot means
the real config, unchanged).
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from flux.config import Configuration
from flux.runners.child_settings import (
    CHILD_SETTINGS_ENV,
    _Section,
    _Shim,
    snapshot,
)


class TestSnapshotParity:
    def test_allowlisted_values_match_the_live_settings(self):
        data = json.loads(snapshot())
        settings = Configuration.get().settings

        assert data["log_level"] == settings.log_level
        assert data["serializer"] == settings.serializer
        assert data["home"] == settings.home
        assert data["workers"]["server_url"] == settings.workers.server_url
        assert data["workers"]["transient_fast_path"] == settings.workers.transient_fast_path
        assert (
            data["scheduling"]["schedule_check_tolerance"]
            == settings.scheduling.schedule_check_tolerance
        )
        assert data["security"]["auth"]["enabled"] == settings.security.auth.enabled

    def test_shim_resolves_the_same_paths_the_child_reads(self):
        """Every Configuration read on the child's execution path must
        resolve through the shim: logging setup, the task auth gate, cache
        and storage paths, schedule tolerances, the transient fast path."""
        shim = _Shim(json.loads(snapshot()))

        s = shim.settings
        assert isinstance(s.log_level, str)
        assert isinstance(s.log_format, str)
        assert isinstance(s.log_date_format, str)
        assert isinstance(s.serializer, str)
        assert isinstance(s.home, str)
        assert isinstance(s.cache_path, str)
        assert isinstance(s.local_storage_path, str)
        assert isinstance(s.security.auth.enabled, bool)
        # The cache/storage path calls integrity.sign() in the child; the
        # read must resolve — to None, so signing no-ops keylessly.
        assert s.security.encryption.encryption_key is None
        assert isinstance(s.workers.server_url, str)
        assert isinstance(s.workers.transient_fast_path, bool)
        assert isinstance(s.scheduling.schedule_check_tolerance, float)
        assert isinstance(s.scheduling.once_schedule_tolerance, float)
        assert s.ai is not None

    def test_a_missing_setting_names_itself_and_the_fix(self):
        section = _Section({"present": 1}, "settings.workers")
        with pytest.raises(AttributeError) as excinfo:
            _ = section.absent
        assert "settings.workers.absent" in str(excinfo.value)
        assert "allowlist" in str(excinfo.value)


class TestSnapshotSecrecy:
    def test_credentials_never_travel(self):
        """The child env sanitization strips the bootstrap token, the
        encryption key, and every FLUX_SECURITY__* value; the snapshot must
        not smuggle them back in."""
        Configuration.get().override(
            workers={"bootstrap_token": "SUPER-SECRET-BOOTSTRAP"},
            security={"encryption": {"encryption_key": "SUPER-SECRET-KEY"}},
        )
        try:
            raw = snapshot()
        finally:
            Configuration.get().reset()

        assert "SUPER-SECRET-BOOTSTRAP" not in raw
        assert "SUPER-SECRET-KEY" not in raw
        data = json.loads(raw)
        assert set(data["security"]) == {"auth", "encryption"}
        assert set(data["security"]["auth"]) == {"enabled"}
        # The encryption entry exists so the child's integrity read resolves,
        # but its value is unconditionally None — even when the parent holds
        # a real key, as this override ensures it does.
        assert data["security"]["encryption"] == {"encryption_key": None}
        assert "bootstrap_token" not in data.get("workers", {})

    def test_shim_settings_are_read_only(self):
        shim = _Shim(json.loads(snapshot()))
        with pytest.raises(RuntimeError, match="read-only snapshot"):
            shim.override(workers={})
        with pytest.raises(RuntimeError, match="read-only snapshot"):
            shim.reset()


class TestInstall:
    def test_no_snapshot_means_the_real_config(self):
        """An older parent (or a bare `python -m flux.runners.child`) sets no
        snapshot; the child must fall back to importing flux.config."""
        code = (
            "import os, sys; os.environ.pop('FLUX_CHILD_SETTINGS', None); "
            "from flux.runners.child_settings import install_from_env; "
            "print(install_from_env()); "
            "from flux.config import Configuration; "
            "print(type(Configuration).__name__)"
        )
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
        )
        lines = out.stdout.strip().splitlines()
        assert lines[-2] == "False"
        assert lines[-1] != "_Shim"

    def test_installed_shim_serves_config_reads_without_pydantic(self):
        """The end-to-end property, in miniature: with the snapshot in the
        environment, the child-side install makes configure_logging and the
        task-auth read work with pydantic never imported."""
        snap = snapshot()
        code = (
            "import sys; "
            "from flux.runners.child_settings import install_from_env; "
            "assert install_from_env(); "
            "from flux.config import Configuration; "
            "s = Configuration.get().settings; "
            "assert isinstance(s.security.auth.enabled, bool); "
            "from flux.utils import configure_logging; configure_logging(); "
            "print('pydantic loaded:', 'pydantic' in sys.modules)"
        )
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            env={**__import__("os").environ, CHILD_SETTINGS_ENV: snap},
        )
        assert out.stdout.strip().splitlines()[-1] == "pydantic loaded: False"
