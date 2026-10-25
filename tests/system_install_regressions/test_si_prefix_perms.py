"""install() never sets owner/mode on the engine prefix /opt/sponsio.
`python -m venv --clear` empties an existing directory but keeps it (and
its owner/mode), so a pre-existing writable /opt/sponsio stays writable
and the agent's user can swap bin/ or site-packages under the engine.
(Contrast: libexec is re-permissioned by a non-shared mkdir.)"""

from __future__ import annotations

import stat
import subprocess

import pytest

from sponsio.system_install import Executor, InstallOptions, Layout, install


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_existing_prefix_is_reowned(platform, tmp_path):
    lay = Layout(platform=platform, root=tmp_path / "root")
    pre = lay.fs(lay.prefix)
    pre.mkdir(parents=True)
    pre.chmod(0o777)
    # A real `venv --clear` keeps the directory itself; the recorder
    # stands in for it here.
    ex = Executor(
        lay,
        chown=False,
        runner=lambda a: subprocess.CompletedProcess(a, 0, "", ""),
        echo=lambda _l: None,
    )
    install(ex, InstallOptions())
    assert stat.S_IMODE(pre.stat().st_mode) == 0o755
