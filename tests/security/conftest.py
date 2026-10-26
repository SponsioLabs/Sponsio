"""Shared fixtures for the boundary corpus.

The corpus sandbox runs sponsio in subprocesses with ``-I``, which leaves
this checkout off ``sys.path``. Those subprocesses then import whatever
sponsio the interpreter has installed: none (the cases fail on import),
or another checkout's editable install (the cases pass or fail on code
that is not this tree's). :func:`sponsio_from_this_checkout` says which,
before any case runs.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def isolated_import_problem(
    module_file: str | None, *, python: str = sys.executable, repo: Path = REPO
) -> str | None:
    """Why ``python -I`` importing sponsio from ``module_file`` would not
    test this checkout, or ``None`` when it does.

    ``module_file`` is ``sponsio.__file__`` as ``python -I`` sees it, or
    ``None`` when it cannot import sponsio. An installed build (not a
    source checkout) is accepted: CI installs the wheel built from this
    tree.
    """
    fix = f"run `{python} -m pip install -e {repo}`"
    if module_file is None:
        return (
            f"sponsio is not installed into {python}, and the corpus runs it "
            f"with -I, which leaves this checkout off sys.path; {fix}"
        )
    root = Path(module_file).resolve().parent.parent
    if root == repo.resolve() or not (root / "pyproject.toml").exists():
        return None
    return (
        f"{python} -I imports sponsio from another checkout ({root}), so the "
        f"corpus would test that code, not {repo}; {fix}"
    )


@pytest.fixture(scope="session")
def sponsio_from_this_checkout() -> None:
    proc = subprocess.run(
        [sys.executable, "-I", "-c", "import sponsio; print(sponsio.__file__)"],
        capture_output=True,
        text=True,
        cwd="/",
        timeout=60,
    )
    found = proc.stdout.strip() if proc.returncode == 0 else None
    problem = isolated_import_problem(found or None)
    if problem:
        pytest.fail(problem, pytrace=False)
