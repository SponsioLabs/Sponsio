"""macOS pieces of the system install: the ``_sponsio`` user (dscl) and
the launchd daemon."""

from __future__ import annotations

import plistlib
from pathlib import Path

from sponsio.system_install import (
    LAUNCHD_LABEL,
    SERVICE_USER,
    Executor,
    InstallError,
    daemon_argv,
)

_ID_RANGE = range(200, 500)
"""Below 500: macOS hides these users from the login window."""


def _taken(ex: Executor, kind: str, attr: str) -> set[int]:
    out = ex.run(["dscl", ".", "-list", f"/{kind}", attr], check=False)
    taken: set[int] = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("-").isdigit():
            taken.add(int(parts[-1]))
    return taken


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
    """Create ``_sponsio``, or finish it. Every setting that keeps the
    account from being used to log in is applied on every run, so an
    install that stopped partway is completed by running it again."""
    group = f"/Groups/{SERVICE_USER}"
    user = f"/Users/{SERVICE_USER}"
    if _exists(ex):
        import grp
        import pwd

        ex.echo(f"user {SERVICE_USER} exists; applying its settings again")
        gid = pwd.getpwnam(SERVICE_USER).pw_gid
        try:
            grp.getgrnam(SERVICE_USER)
        except KeyError:
            ex.run(["dscl", ".", "-create", group])
            ex.run(["dscl", ".", "-create", group, "PrimaryGroupID", str(gid)])
    else:
        taken = _taken(ex, "Users", "UniqueID") | _taken(ex, "Groups", "PrimaryGroupID")
        free = next((i for i in _ID_RANGE if i not in taken), None)
        if free is None:
            raise InstallError("no free id below 500 for the _sponsio user")
        for argv in (
            ["dscl", ".", "-create", group],
            ["dscl", ".", "-create", group, "PrimaryGroupID", str(free)],
            ["dscl", ".", "-create", user],
            ["dscl", ".", "-create", user, "UniqueID", str(free)],
            ["dscl", ".", "-create", user, "PrimaryGroupID", str(free)],
        ):
            ex.run(argv)
    for argv in (
        ["dscl", ".", "-create", group, "RealName", "Sponsio daemon"],
        ["dscl", ".", "-create", group, "Password", "*"],
        ["dscl", ".", "-create", user, "UserShell", "/usr/bin/false"],
        ["dscl", ".", "-create", user, "NFSHomeDirectory", "/var/empty"],
        ["dscl", ".", "-create", user, "RealName", "Sponsio daemon"],
        ["dscl", ".", "-create", user, "Password", "*"],
        ["dscl", ".", "-create", user, "IsHidden", "1"],
    ):
        ex.run(argv)


def remove_user(ex: Executor) -> None:
    ex.run(["dscl", ".", "-delete", f"/Users/{SERVICE_USER}"], check=False)
    ex.run(["dscl", ".", "-delete", f"/Groups/{SERVICE_USER}"], check=False)


def plist(ex: Executor) -> bytes:
    lay = ex.layout
    return plistlib.dumps(
        {
            "Label": LAUNCHD_LABEL,
            "ProgramArguments": daemon_argv(lay),
            "UserName": SERVICE_USER,
            "GroupName": SERVICE_USER,
            "RunAtLoad": True,
            "KeepAlive": True,
            "ProcessType": "Interactive",
            "Umask": 0o077,
            "EnvironmentVariables": {
                "HOME": str(lay.state),
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            },
            "StandardOutPath": str(lay.logs / "daemon.log"),
            "StandardErrorPath": str(lay.logs / "daemon.log"),
        }
    )


def install_service(ex: Executor) -> None:
    lay = ex.layout
    ex.run(["launchctl", "bootout", f"system/{LAUNCHD_LABEL}"], check=False)
    ex.write(lay.service_file, plist(ex).decode("utf-8"), mode=0o644)
    ex.run(["launchctl", "bootstrap", "system", str(lay.service_file)])


def remove_service(ex: Executor) -> None:
    ex.run(["launchctl", "bootout", f"system/{LAUNCHD_LABEL}"], check=False)
    ex.remove(ex.layout.service_file)
