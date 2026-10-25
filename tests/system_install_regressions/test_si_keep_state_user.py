"""uninstall --keep-state keeps the 0600 rules / audit log / keys but
deletes the _sponsio account that owns them, leaving them owned by a
dangling uid the next created account can be given."""

from __future__ import annotations

import subprocess

import pytest

from sponsio.system_install import (
    SERVICE_USER,
    Executor,
    InstallOptions,
    Layout,
    install,
    uninstall,
)


class Recorder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_keep_state_keeps_the_owner_of_the_state(platform, tmp_path):
    lay = Layout(platform=platform, root=tmp_path / "root")
    rec = Recorder()
    ex = Executor(lay, chown=False, runner=rec, echo=lambda _l: None)
    install(ex, InstallOptions())
    rec.calls.clear()

    uninstall(ex, keep_state=True)

    assert lay.fs(lay.state).is_dir()  # state kept, as asked
    deleted_owner = [
        c
        for c in rec.calls
        if c[:3] == ["dscl", ".", "-delete"]
        and SERVICE_USER in c[-1]
        or c[:1] == ["userdel"]
    ]
    assert deleted_owner == [], f"kept state but deleted its owner: {deleted_owner}"
