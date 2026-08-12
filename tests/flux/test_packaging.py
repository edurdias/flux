"""The PEP 561 marker is packaging, so nothing else fails when it goes missing.

Without ``flux/py.typed`` a type checker ignores every annotation in the
package, and a downstream ``--strict`` build keeps passing while the values
crossing the boundary silently become ``Any`` (issue #182). There is no import
error and no test failure to notice — hence this one.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import flux


def test_py_typed_marker_is_present():
    marker = Path(flux.__file__).parent / "py.typed"

    assert marker.is_file(), (
        "flux/py.typed is missing: PEP 561 makes type checkers ignore the "
        "package's annotations entirely, so consumers see Any"
    )


def test_marker_resolves_as_package_data():
    """Checked through importlib.resources rather than the filesystem: that is
    how an installed wheel exposes the file, so this fails if the marker stops
    being packaged even while it still sits in the source tree."""
    marker = resources.files("flux") / "py.typed"

    assert marker.is_file()
