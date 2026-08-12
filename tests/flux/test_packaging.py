"""The PEP 561 marker is packaging, so nothing else fails when it goes missing.

Without ``flux/py.typed`` a type checker ignores every annotation in the
package, and a downstream ``--strict`` build keeps passing while the values
crossing the boundary silently become ``Any`` (issue #182). There is no import
error and no test failure to notice — hence this one.
"""

from __future__ import annotations

from pathlib import Path

import flux


def test_py_typed_marker_is_present():
    marker = Path(flux.__file__).parent / "py.typed"

    assert marker.is_file(), (
        "flux/py.typed is missing: PEP 561 makes type checkers ignore the "
        "package's annotations entirely, so consumers see Any"
    )


def test_marker_sits_beside_the_package_init():
    """It has to live inside the package directory, not the repo root — a
    marker anywhere else is invisible to PEP 561 resolution."""
    package_dir = Path(flux.__file__).parent

    assert (package_dir / "__init__.py").is_file()
    assert (package_dir / "py.typed").parent == package_dir
