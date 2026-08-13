"""The runner child's import graph stays lean (issue #233).

The child is the per-execution entrypoint: for container runners its
module-level imports are paid on every request, and profiling showed ~600ms
before any user code — 185ms of it SQLAlchemy, imported into a network=none
sandbox that cannot open a database connection. The persistence graph is
server/inline-path only; a sandboxed child should not even be able to load
it. These run a fresh interpreter per check so this file's own imports
cannot mask a regression.
"""

from __future__ import annotations

import subprocess
import sys


def _imported_after(statement: str, module: str) -> bool:
    code = f"import sys; {statement}; print({module!r} in sys.modules)"
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip() == "True"


class TestChildStaysLean:
    def test_child_does_not_import_sqlalchemy(self):
        assert not _imported_after("import flux.runners.child", "sqlalchemy")

    def test_child_does_not_import_flux_models(self):
        assert not _imported_after("import flux.runners.child", "flux.models")

    def test_runners_package_does_not_import_the_loader_graph(self):
        """python -m flux.runners.child imports the package init first, so
        an eager registry there would defeat every deferral below it."""
        assert not _imported_after("import flux.runners", "flux.runners.loader")

    def test_manager_abcs_do_not_pull_the_persistence_graph(self):
        assert not _imported_after("import flux.config_manager", "sqlalchemy")
        assert not _imported_after("import flux.secret_managers", "sqlalchemy")


class TestLazySurfacesStillResolve:
    def test_runner_registry_attributes(self):
        from flux.runners import (  # noqa: F401
            InProcessRunner,
            Runner,
            RunnerHooks,
            SubprocessRunner,
            WorkflowModuleLoader,
        )

    def test_database_managers_still_work(self, tmp_path):
        from flux.config import Configuration

        Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'lean.db'}")
        from flux.models import DatabaseRepository

        DatabaseRepository._engines.clear()
        try:
            from flux.config_manager import DatabaseConfigManager

            manager = DatabaseConfigManager()
            manager.save("k", {"v": 1})
            assert manager.all() == ["k"]

            from flux.secret_managers import DatabaseSecretManager

            secrets = DatabaseSecretManager()
            secrets.save("s", "value")
            assert secrets.all() == ["s"]
        finally:
            DatabaseRepository._engines.clear()
