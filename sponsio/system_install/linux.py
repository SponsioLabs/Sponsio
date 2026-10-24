"""Linux pieces of the system install: the ``_sponsio`` user (useradd)
and the systemd unit."""

from __future__ import annotations

import shlex
from pathlib import Path

from sponsio.system_install import (
    SERVICE_USER,
    SYSTEMD_UNIT,
    Executor,
    daemon_argv,
)


def _exists(ex: Executor) -> bool:
    if ex.dry_run or ex.layout.root != Path("/"):
        return False
    import pwd

    try:
        pwd.getpwnam(SERVICE_USER)
    except KeyError:
        return False
    return True


def ensure_user(ex: Executor) -> None:
    if _exists(ex):
        ex.echo(f"user {SERVICE_USER} exists")
        return
    ex.run(
        [
            "useradd",
            "--system",
            "--user-group",
            "--no-create-home",
            "--home-dir",
            "/nonexistent",
            "--shell",
            "/usr/sbin/nologin",
            SERVICE_USER,
        ]
    )


def remove_user(ex: Executor) -> None:
    ex.run(["userdel", SERVICE_USER], check=False)
    ex.run(["groupdel", SERVICE_USER], check=False)


def unit(ex: Executor) -> str:
    """The unit. systemd creates the state, log and runtime directories
    with the right owner on every start; the filesystem is read-only to
    the daemon except those."""
    lay = ex.layout
    return f"""[Unit]
Description=Sponsio enforcement daemon
After=network.target

[Service]
User={SERVICE_USER}
Group={SERVICE_USER}
ExecStart={shlex.join(daemon_argv(lay))}
Restart=always
RestartSec=1
Environment=HOME={lay.state}
UMask=0077
StateDirectory={lay.state.name}
StateDirectoryMode=0700
LogsDirectory={lay.logs.name}
LogsDirectoryMode=0700
RuntimeDirectory={lay.run.name}
RuntimeDirectoryMode=0755
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=yes
ReadWritePaths={lay.state} {lay.logs} {lay.run}
StandardOutput=append:{lay.logs / "daemon.log"}
StandardError=append:{lay.logs / "daemon.log"}

[Install]
WantedBy=multi-user.target
"""


def install_service(ex: Executor) -> None:
    ex.write(ex.layout.service_file, unit(ex), mode=0o644)
    ex.run(["systemctl", "daemon-reload"])
    ex.run(["systemctl", "enable", SYSTEMD_UNIT])
    ex.run(["systemctl", "restart", SYSTEMD_UNIT])


def remove_service(ex: Executor) -> None:
    ex.run(["systemctl", "disable", "--now", SYSTEMD_UNIT], check=False)
    ex.remove(ex.layout.service_file)
    ex.run(["systemctl", "daemon-reload"], check=False)
