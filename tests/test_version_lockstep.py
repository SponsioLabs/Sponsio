"""``sponsio.__version__`` matches the version the wheel is built with.

0.2.0a12 through a16 all introduced themselves as 0.2.0a11 (``sponsio
--version``, ``doctor``, the daemon) because only ``pyproject.toml`` was
bumped, the same slip the comment in ``sponsio/__init__.py`` records for
a4. A lockstep comment did not hold; a test does.
"""

from __future__ import annotations

import re
from pathlib import Path

import sponsio


def test_package_version_matches_pyproject():
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    declared = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE).group(1)
    assert sponsio.__version__ == declared
