"""The corpus refuses to run on a sponsio that is not this checkout's."""

from __future__ import annotations

from pathlib import Path

from tests.security.conftest import isolated_import_problem


def _checkout(path: Path) -> Path:
    (path / "sponsio").mkdir(parents=True)
    (path / "pyproject.toml").write_text("[project]\nname = 'sponsio'\n")
    return path / "sponsio" / "__init__.py"


def test_this_checkout_is_fine(tmp_path):
    module = _checkout(tmp_path / "repo")
    assert isolated_import_problem(str(module), repo=tmp_path / "repo") is None


def test_an_installed_build_is_fine(tmp_path):
    module = tmp_path / "site-packages" / "sponsio" / "__init__.py"
    module.parent.mkdir(parents=True)
    assert isolated_import_problem(str(module), repo=tmp_path / "repo") is None


def test_not_installed_says_how_to_install(tmp_path):
    problem = isolated_import_problem(None, python="py", repo=tmp_path / "repo")
    assert "not installed into py" in problem
    assert f"py -m pip install -e {tmp_path / 'repo'}" in problem


def test_another_checkout_is_named(tmp_path):
    other = _checkout(tmp_path / "other")
    problem = isolated_import_problem(str(other), repo=tmp_path / "repo")
    assert f"another checkout ({tmp_path / 'other'})" in problem
