"""The managed-settings deny list writes every protected path as
Edit(//p/**). For the service file (a file, not a directory) `p/**`
matches only paths *under* p, so no Edit rule covers the plist / unit."""

from __future__ import annotations

import pytest

from sponsio.system_install import Layout, claude_managed_settings


def _covered(rules: list[str], path: str) -> bool:
    for r in rules:
        if not (r.startswith("Edit(//") and r.endswith(")")):
            continue
        pat = r[len("Edit(/") : -1]  # "/abs/..." form
        if pat == path:
            return True
        if pat.endswith("/**") and path.startswith(pat[:-3] + "/"):
            return True
    return False


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_service_file_edit_is_denied(platform):
    lay = Layout(platform=platform)
    deny = claude_managed_settings(lay, allow_user_hooks=False)["permissions"]["deny"]
    assert _covered(deny, str(lay.service_file)), deny
