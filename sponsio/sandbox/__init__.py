"""``sponsio run --sandbox``: start an agent where Sponsio is its only way out.

The agent runs inside an OS sandbox:

* **Network.** No route out except the daemon's egress proxy (the run
  gets its own, ``egress.open``), which decides every connection. On
  Linux the sandbox has its own empty network namespace and a forwarder
  inside it passes ``127.0.0.1:3128`` to the proxy's Unix socket; on
  macOS a Seatbelt profile allows only the proxy's loopback port.
* **Files.** Everything is read-only except the workspace, temporary
  directories and paths named with ``--writable``. Sponsio's own state
  (``~/.sponsio``, the system install's state) cannot be read at all;
  only the daemon's agent socket stays reachable, so hooks inside still
  reach the daemon.
* **Environment.** Credentials are stripped (P3.5): names ending in
  ``_TOKEN``, ``_SECRET``, ``_PASSWORD``, ``_API_KEY`` or ``_KEY``, and
  known cloud credentials, except the agent's own model key and names
  kept with ``--keep-env``. Tools reach their services through the MCP
  gateway, which holds those credentials.

With no daemon to open a proxy the agent is not started.

Linux needs ``bwrap`` (bubblewrap) and unprivileged user namespaces;
macOS uses ``/usr/bin/sandbox-exec``. Landlock is not applied yet: the
filesystem rules come from bubblewrap's mounts.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

PROXY_PORT_LINUX = 3128
SECRET_SUFFIXES = ("_TOKEN", "_SECRET", "_PASSWORD", "_API_KEY", "_KEY", "_CREDENTIALS")
SECRET_NAMES = frozenset(
    {
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "AZURE_CLIENT_SECRET",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "SPONSIO_API_KEY",
        "NPM_TOKEN",
        "PYPI_TOKEN",
        "DOCKER_PASSWORD",
        "KUBECONFIG",
        "SSH_AUTH_SOCK",
    }
)
DEFAULT_KEEP = frozenset(
    {"ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_KEY"}
)
"""The agent's own model key: the agent cannot run without it."""


class SandboxError(RuntimeError):
    """The agent cannot be started in a sandbox; the message says why."""


def secret_env(env: dict[str, str], keep: frozenset[str] = frozenset()) -> list[str]:
    """The names in ``env`` to remove before the agent starts."""
    keep = DEFAULT_KEEP | keep
    out = []
    for name in env:
        if name in keep:
            continue
        upper = name.upper()
        if upper in SECRET_NAMES or upper.endswith(SECRET_SUFFIXES):
            out.append(name)
    return sorted(out)


@dataclass
class Plan:
    """What :func:`run` will start."""

    argv: list[str]
    env: dict[str, str]
    stripped: list[str]
    masked: list[Path]
    session: str
    cleanup: list[Path] = field(default_factory=list)


def sponsio_paths(home: Path) -> list[Path]:
    """Sponsio's state, which the agent must not even read."""
    paths = [home / ".sponsio"]
    try:
        from sponsio.system_install import installed_layout

        lay = installed_layout()
    except Exception:  # noqa: BLE001 - not every platform has one
        lay = None
    if lay is not None:
        paths.append(lay.state)
    return paths


def _proxy_env(env: dict[str, str], url: str) -> None:
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        env[name] = url
    for name in ("NO_PROXY", "no_proxy"):
        env.pop(name, None)


def linux_argv(
    command: list[str],
    *,
    workspace: Path,
    writable: list[Path],
    masked: list[Path],
    agent_socket: Path,
    egress_unix: str,
    hide_home: Path | None,
) -> list[str]:
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        raise SandboxError("bubblewrap is not installed (`apt install bubblewrap`)")
    from sponsio.sandbox import forward

    argv = [
        bwrap,
        "--die-with-parent",
        "--new-session",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-net",
        "--unshare-cgroup-try",
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
        "--tmpfs",
        "/run",
    ]
    if hide_home is not None:
        argv += ["--tmpfs", str(hide_home)]
    for p in masked:
        if p.is_dir():
            argv += ["--tmpfs", str(p)]
        elif p.exists():
            argv += ["--ro-bind", "/dev/null", str(p)]
    for p in [workspace, *writable]:
        argv += ["--bind", str(p), str(p)]
    if agent_socket.exists():
        argv += ["--ro-bind", str(agent_socket), str(agent_socket)]
    inner = "/run/sponsio-egress.sock"
    argv += ["--bind", egress_unix, inner, "--chdir", str(workspace)]
    argv += [
        "--",
        sys.executable,
        "-I",
        "-S",
        str(Path(forward.__file__).resolve()),
        "--unix",
        inner,
        "--port",
        str(PROXY_PORT_LINUX),
        "--",
        *command,
    ]
    return argv


def _sbpl_quote(path: Path) -> str:
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def macos_profile(
    *,
    workspace: Path,
    writable: list[Path],
    masked: list[Path],
    agent_socket: Path,
    proxy_port: int,
    hide_home: Path | None,
) -> str:
    """A Seatbelt profile. Later rules win over earlier ones."""
    temp = [
        Path("/private/tmp"),
        Path("/private/var/folders"),
        Path(tempfile.gettempdir()).resolve(),
    ]
    write_ok = [workspace, *writable, *temp]
    lines = [
        "(version 1)",
        "(allow default)",
        "(deny network*)",
        f'(allow network-outbound (remote ip "localhost:{proxy_port}"))',
        f"(allow network-outbound (remote unix-socket (path-literal {_sbpl_quote(agent_socket)})))",
        '(allow network-bind (local ip "localhost:*"))',
        '(allow network-inbound (local ip "localhost:*"))',
        '(deny file-write* (subpath "/"))',
        "(allow file-write* "
        + " ".join(f"(subpath {_sbpl_quote(p.resolve())})" for p in write_ok)
        + ' (literal "/dev/null") (literal "/dev/tty") (regex #"^/dev/ttys[0-9]+$"))',
    ]
    if hide_home is not None:
        lines.append(f"(deny file-read* (subpath {_sbpl_quote(hide_home.resolve())}))")
        lines.append(
            "(allow file-read* "
            + " ".join(
                f"(subpath {_sbpl_quote(p.resolve())})" for p in [workspace, *writable]
            )
            + ")"
        )
    for p in masked:
        lines.append(
            f"(deny file-read* file-write* (subpath {_sbpl_quote(p.resolve())}))"
        )
    return "\n".join(line for line in lines if line) + "\n"


def plan(
    command: list[str],
    *,
    workspace: Path,
    socket_path: Path,
    egress: dict,
    writable: list[Path] | None = None,
    keep_env: frozenset[str] = frozenset(),
    hide_home: bool = False,
    env: dict[str, str] | None = None,
    platform: str = sys.platform,
) -> Plan:
    """The sandboxed command for ``command``, given an open egress proxy."""
    if not command:
        raise SandboxError("no command to run")
    env = dict(os.environ if env is None else env)
    stripped = secret_env(env, keep_env)
    for name in stripped:
        env.pop(name)
    home = Path(env.get("HOME") or Path.home())
    masked = [p for p in sponsio_paths(home) if p.exists()]
    writable = [Path(p).resolve() for p in (writable or [])]
    workspace = workspace.resolve()
    cleanup: list[Path] = []
    if platform == "container":
        # The container is the sandbox (deploy/tier3): no OS layer, only
        # the forwarder to the proxy's socket in the shared volume.
        from sponsio.sandbox import forward

        _proxy_env(env, f"http://127.0.0.1:{PROXY_PORT_LINUX}")
        argv = [
            sys.executable,
            "-I",
            "-S",
            str(Path(forward.__file__).resolve()),
            "--unix",
            egress["unix"],
            "--port",
            str(PROXY_PORT_LINUX),
            "--",
            *command,
        ]
    elif platform.startswith("linux"):
        _proxy_env(env, f"http://127.0.0.1:{PROXY_PORT_LINUX}")
        argv = linux_argv(
            command,
            workspace=workspace,
            writable=writable,
            masked=masked,
            agent_socket=socket_path,
            egress_unix=egress["unix"],
            hide_home=home if hide_home else None,
        )
    elif platform == "darwin":
        _proxy_env(env, f"http://127.0.0.1:{egress['port']}")
        profile = macos_profile(
            workspace=workspace,
            writable=writable,
            masked=masked,
            agent_socket=socket_path,
            proxy_port=int(egress["port"]),
            hide_home=home if hide_home else None,
        )
        fd, path = tempfile.mkstemp(prefix="sponsio-sandbox-", suffix=".sb")
        with os.fdopen(fd, "w") as fh:
            fh.write(profile)
        cleanup.append(Path(path))
        argv = ["/usr/bin/sandbox-exec", "-f", path, *command]
    else:
        raise SandboxError(f"no sandbox for {platform}; macOS and Linux only")
    env["SPONSIO_SANDBOX"] = "1"
    return Plan(argv, env, stripped, masked, egress.get("session", ""), cleanup)


def run(
    command: list[str],
    *,
    workspace: Path,
    socket_path: Path | None = None,
    writable: list[Path] | None = None,
    keep_env: frozenset[str] = frozenset(),
    hide_home: bool = False,
    container: bool = False,
    echo=print,
) -> int:
    """Open a proxy for this run, start ``command`` in the sandbox, close
    the proxy. Returns the command's exit status."""
    from sponsio.daemon.client import DaemonClient, DaemonError, default_socket_path

    sock = (socket_path or default_socket_path()).expanduser()
    client = DaemonClient(socket_path=sock, timeout_s=10)
    session = f"run-{secrets.token_hex(6)}"
    try:
        egress = client.call("egress.open", {"session": session})
    except DaemonError as e:
        raise SandboxError(
            f"the daemon did not open an egress proxy ({e}); the agent is not "
            f"started without one"
        ) from e
    try:
        p = plan(
            command,
            workspace=workspace,
            socket_path=sock,
            egress=egress,
            writable=writable,
            keep_env=keep_env,
            hide_home=hide_home,
            platform="container" if container else sys.platform,
        )
        if p.stripped:
            echo(f"sponsio run: removed from the environment: {', '.join(p.stripped)}")
        echo(f"sponsio run: session {session}, network through the egress proxy only")
        try:
            return subprocess.call(p.argv, env=p.env, cwd=str(workspace))
        finally:
            for f in p.cleanup:
                f.unlink(missing_ok=True)
    finally:
        try:
            client.call("egress.close", {"session": session})
        except DaemonError:
            pass
