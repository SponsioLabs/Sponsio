"""default_socket_path() falls back to the per-user socket on a machine
with a system install whenever the system socket is momentarily absent
(daemon restarting; on Linux systemd removes RuntimeDirectory on stop)."""

from __future__ import annotations

from sponsio.system_install import Layout


def test_installed_machine_never_resolves_to_user_socket(monkeypatch):
    import sponsio.system_install as si
    from sponsio.daemon.client import default_socket_path

    monkeypatch.delenv("SPONSIO_DAEMON_SOCKET", raising=False)
    # A system install exists (service file present) but its socket is
    # not there right now. Linux paths do not exist on the test host.
    lay = Layout(platform="linux")
    assert not lay.socket.exists()
    monkeypatch.setattr(si, "installed_layout", lambda: lay)

    assert default_socket_path() == lay.socket
