"""The P1.2 import runs as root and copies the invoking user's
~/.sponsio/plugins/<bucket> with shutil.copytree's default
symlinks=False, so a symlink planted there is followed and the target's
contents (any root-readable file or tree) land in the rule store."""

from __future__ import annotations

import subprocess

from sponsio.system_install import Executor, Layout, import_user_libraries


def test_import_does_not_follow_symlinks_out_of_the_user_dir(tmp_path):
    home = tmp_path / "home"
    bucket = home / ".sponsio" / "plugins" / "_host_claude_code"
    bucket.mkdir(parents=True)
    (bucket / "sponsio.yaml").write_text("agents: {}\n")
    secret = tmp_path / "root-only-secret"
    secret.write_text("TOP SECRET\n")
    (bucket / "notes").symlink_to(secret)

    lay = Layout(platform="linux", root=tmp_path / "root")
    ex = Executor(
        lay,
        chown=False,
        runner=lambda a: subprocess.CompletedProcess(a, 0, "", ""),
        echo=lambda _l: None,
    )
    import_user_libraries(ex, home, ())

    copied = lay.fs(lay.plugins / "_host_claude_code" / "notes")
    leaked = (
        copied.exists()
        and not copied.is_symlink()
        and "TOP SECRET" in copied.read_text()
    )
    assert not leaked, (
        "root copied a file outside the user's plugin dir into the rule store"
    )
