"""System install (Tier 2): the daemon as its own user, out of the agent's reach.

``sudo sponsio install --system`` sets up a machine so the agent's OS
user cannot change what Sponsio enforces:

* a system user and group ``_sponsio`` the daemon runs as;
* the engine in a root-owned virtualenv (``/opt/sponsio``) built on a
  root-owned interpreter, and the hook shims in ``/usr/local/libexec/
  sponsio``, root-owned;
* the daemon's state (rules, history, audit log, config) in a directory
  only ``_sponsio`` can read or write, and its sockets beside it: the
  agent socket open to every user, the admin socket to ``_sponsio``
  only, so only root reaches it;
* a launchd daemon or systemd unit that keeps it running;
* the host's hooks in its managed (system) settings, which the user's
  own settings cannot remove or override (Claude Code), or in its
  system hooks file (Cursor, which stays Tier 1; see ``sponsio doctor``).

The existing user-level contract libraries are imported into the
daemon's rule store, and after that the daemon reads only its own copy.
``sponsio uninstall --system`` removes all of it.

Every path hangs off a :class:`Layout` with a ``root`` (``/`` on a real
machine, a temporary directory in tests), and every command goes through
:class:`Executor`, which can print instead of run (``--dry-run``).
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

SERVICE_USER = "_sponsio"
LAUNCHD_LABEL = "dev.sponsio.daemon"
SYSTEMD_UNIT = "sponsio.service"
MIN_CLAUDE_CODE = "2.1.295"
"""``onFailure: "block"`` on hooks needs this Claude Code version."""
MANIFEST = "system-install.json"
HOSTS = ("claude-code", "cursor")
HOST_BUCKETS = {
    "claude-code": ("_host_claude_code", "_host_claude_code_subagent"),
    "cursor": ("_host_cursor", "_host_cursor_subagent"),
}


class InstallError(RuntimeError):
    """The install cannot go ahead; the message says why and what to do."""


# ---------------------------------------------------------------------------
# Where everything goes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Layout:
    """Every path of a system install, as it is on the target machine.

    :meth:`fs` maps one of them under ``root`` for file operations, so a
    test can install into a temporary directory. Paths written into
    files (hook commands, the service definition) are the real ones.
    """

    platform: str
    root: Path = Path("/")

    def fs(self, path: Path) -> Path:
        return self.root / Path(path).relative_to("/")

    @property
    def darwin(self) -> bool:
        return self.platform == "darwin"

    @property
    def prefix(self) -> Path:
        return Path("/opt/sponsio")

    @property
    def python(self) -> Path:
        return self.prefix / "bin" / "python"

    @property
    def libexec(self) -> Path:
        return Path("/usr/local/libexec/sponsio")

    @property
    def shim(self) -> Path:
        return self.libexec / "sponsio_shim.py"

    @property
    def mcp_shim(self) -> Path:
        return self.libexec / "sponsio_mcp.py"

    @property
    def base(self) -> Path | None:
        """macOS: the root-owned directory that holds ``state`` and
        ``run``. ``/var/run`` is emptied at boot, and launchd does not
        recreate a directory there for a daemon that is not root."""
        return Path("/Library/Application Support/Sponsio") if self.darwin else None

    @property
    def state(self) -> Path:
        return self.base / "state" if self.base else Path("/var/lib/sponsio")

    @property
    def logs(self) -> Path:
        return (
            Path("/Library/Logs/Sponsio") if self.darwin else Path("/var/log/sponsio")
        )

    @property
    def run(self) -> Path:
        return self.base / "run" if self.base else Path("/run/sponsio")

    @property
    def socket(self) -> Path:
        return self.run / "agent.sock"

    @property
    def admin_socket(self) -> Path:
        from sponsio.daemon.server import admin_socket_for

        return admin_socket_for(self.socket)

    @property
    def plugins(self) -> Path:
        return self.state / "plugins"

    @property
    def service_file(self) -> Path:
        if self.darwin:
            return Path(f"/Library/LaunchDaemons/{LAUNCHD_LABEL}.plist")
        return Path(f"/etc/systemd/system/{SYSTEMD_UNIT}")

    @property
    def claude_managed_dir(self) -> Path:
        if self.darwin:
            return Path("/Library/Application Support/ClaudeCode")
        return Path("/etc/claude-code")

    @property
    def claude_settings(self) -> Path:
        """Our own drop-in file. ``managed-settings.json`` itself is never
        touched; files in ``managed-settings.d`` merge after it."""
        return self.claude_managed_dir / "managed-settings.d" / "50-sponsio.json"

    @property
    def cursor_hooks(self) -> Path:
        if self.darwin:
            return Path("/Library/Application Support/Cursor/hooks.json")
        return Path("/etc/cursor/hooks.json")

    @property
    def manifest(self) -> Path:
        return self.state / MANIFEST

    def protected_paths(self) -> list[Path]:
        """What the agent must not write: Sponsio's own files and the
        settings that wire it in."""
        paths = [
            self.prefix,
            self.libexec,
            self.state,
            self.logs,
            self.service_file,
            self.claude_managed_dir,
            self.cursor_hooks.parent,
        ]
        if self.base:
            paths.insert(2, self.base)
        return paths


def detect_layout(root: Path = Path("/")) -> Layout:
    if sys.platform not in ("darwin", "linux"):
        raise InstallError(
            f"system install supports macOS and Linux, not {sys.platform}"
        )
    return Layout(platform=sys.platform, root=root)


def installed_layout() -> Layout | None:
    """The layout of this machine's system install, if it has one."""
    if sys.platform not in ("darwin", "linux"):
        return None
    layout = Layout(platform=sys.platform)
    return layout if layout.socket.exists() or layout.service_file.exists() else None


# ---------------------------------------------------------------------------
# Doing it, or saying what would be done
# ---------------------------------------------------------------------------

Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, check=False)


@dataclass
class Executor:
    """Performs the install's actions under ``layout.root``.

    ``dry_run`` prints each action and performs none. ``chown`` is off in
    tests, which do not run as root. ``runner`` runs commands
    (``subprocess.run`` by default); tests pass one that records them.
    """

    layout: Layout
    dry_run: bool = False
    chown: bool = True
    runner: Runner = _run
    echo: Callable[[str], None] = print
    log: list[str] = field(default_factory=list)

    def _say(self, line: str) -> None:
        self.log.append(line)
        self.echo(line)

    def run(self, argv: list[str], *, check: bool = True) -> str:
        self._say("$ " + shlex.join(argv))
        if self.dry_run:
            return ""
        done = self.runner(argv)
        if check and done.returncode != 0:
            raise InstallError(
                f"`{shlex.join(argv)}` failed ({done.returncode}): "
                f"{(done.stderr or done.stdout or '').strip()[:500]}"
            )
        return done.stdout or ""

    def _own(self, path: Path, owner: str, mode: int) -> None:
        os.chmod(path, mode)
        if self.chown:
            import grp
            import pwd

            uid = pwd.getpwnam(owner).pw_uid
            gid = grp.getgrnam(owner).gr_gid if owner != "root" else 0
            os.chown(path, uid, gid)

    def mkdir(
        self,
        path: Path,
        *,
        owner: str = "root",
        mode: int = 0o755,
        shared: bool = False,
    ) -> None:
        """Create ``path``. One of Sponsio's own directories gets ``owner``
        and ``mode`` even if it exists; a ``shared`` one (``/opt``, a
        host's settings directory) only when this creates it."""
        target = self.layout.fs(path)
        if shared and target.is_dir():
            return
        self._say(f"mkdir {path} ({owner}, {mode:o})")
        if self.dry_run:
            return
        target.mkdir(parents=True, exist_ok=True)
        self._own(target, owner, mode)

    def write(
        self, path: Path, text: str, *, owner: str = "root", mode: int = 0o644
    ) -> None:
        self._say(f"write {path} ({owner}, {mode:o})")
        if self.dry_run:
            return
        target = self.layout.fs(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.sponsio-tmp")
        tmp.write_text(text, encoding="utf-8")
        self._own(tmp, owner, mode)
        os.replace(tmp, target)

    def copy(
        self, source: Path, path: Path, *, owner: str = "root", mode: int = 0o644
    ) -> None:
        """``source`` is a local file; ``path`` a target path."""
        self.write(
            path, Path(source).read_text(encoding="utf-8"), owner=owner, mode=mode
        )

    def copytree(self, source: Path, path: Path, *, owner: str) -> list[str]:
        """Copy the directories and regular files under ``source``. This
        runs as root over a directory the user owns, so a link is never
        followed and anything but a regular file is left out; returns
        what was left out."""
        self._say(f"copy {source} -> {path} ({owner})")
        skipped: list[str] = []
        if source.is_symlink() or not source.is_dir():
            return [str(source)]
        if self.dry_run:
            return skipped
        target = self.layout.fs(path)
        for here, dirs, files in os.walk(source, followlinks=False):
            rel = Path(here).relative_to(source)
            dest = target / rel
            dest.mkdir(parents=True, exist_ok=True)
            self._own(dest, owner, 0o700)
            for name in [*dirs]:
                if (Path(here) / name).is_symlink():
                    dirs.remove(name)
                    skipped.append(str(Path(here) / name))
            for name in files:
                src = Path(here) / name
                st = os.lstat(src)
                if not stat.S_ISREG(st.st_mode):
                    skipped.append(str(src))
                    continue
                fd = os.open(src, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd, "rb") as fh:
                    data = fh.read()
                (dest / name).write_bytes(data)
                self._own(dest / name, owner, 0o600)
        return skipped

    def secure_tree(self, path: Path) -> None:
        """``path`` and everything under it (links not followed): root's,
        with group and other write removed."""
        self._say(f"secure {path} (root, no group or other write)")
        if self.dry_run:
            return
        top = self.layout.fs(path)
        for here, dirs, files in os.walk(top, followlinks=False):
            for p in [Path(here), *(Path(here) / n for n in [*dirs, *files])]:
                st = os.lstat(p)
                if stat.S_ISLNK(st.st_mode):
                    if self.chown and st.st_uid != 0:
                        os.lchown(p, 0, 0)
                    continue
                mode = stat.S_IMODE(st.st_mode)
                if mode & 0o022 or (self.chown and st.st_uid != 0):
                    self._own(p, "root", mode & ~0o022)

    def tighten(self, path: Path) -> None:
        """A shared directory that already exists (a host's settings
        directory): make it root's and not writable by other users,
        leaving the rest of its mode as it was."""
        target = self.layout.fs(path)
        if not target.is_dir():
            return
        st = target.stat()
        mode = stat.S_IMODE(st.st_mode)
        if mode & 0o022 == 0 and (st.st_uid == 0 or not self.chown):
            return
        self._say(f"tighten {path} (root, {mode & ~0o022:o})")
        if self.dry_run:
            return
        self._own(target, "root", mode & ~0o022)

    def remove(self, path: Path) -> None:
        target = self.layout.fs(path)
        if not (target.exists() or target.is_symlink()):
            return
        self._say(f"remove {path}")
        if self.dry_run:
            return
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()

    def exists(self, path: Path) -> bool:
        return self.layout.fs(path).exists()

    def read(self, path: Path) -> str | None:
        try:
            return self.layout.fs(path).read_text(encoding="utf-8")
        except OSError:
            return None


# ---------------------------------------------------------------------------
# The interpreter the engine runs on
# ---------------------------------------------------------------------------

_CANDIDATES_DARWIN = (
    "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3",
    "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3",
    "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3",
    "/Library/Frameworks/Python.framework/Versions/3.10/bin/python3",
    "/usr/bin/python3",
)
_CANDIDATES_LINUX = ("/usr/bin/python3", "/usr/local/bin/python3")


def _root_owned_chain(path: Path) -> str | None:
    """Why ``path`` or a directory above it could be changed by a user
    other than root, or ``None``."""
    for p in [path, *path.parents]:
        try:
            st = p.stat()
        except OSError as e:
            return f"{p}: {e}"
        if st.st_uid != 0:
            return f"{p} is owned by uid {st.st_uid}, not root"
        if st.st_mode & 0o002 and not (st.st_mode & 0o1000):
            return f"{p} is writable by every user"
        if st.st_mode & 0o020 and st.st_gid != 0:
            return f"{p} is writable by group {st.st_gid}"
    return None


def shared_parent_problems(layout: Layout) -> list[str]:
    """Why a directory above one of Sponsio's own could be changed by a
    user other than root, one line per directory, or ``[]``. Sponsio's
    own directories are made root's by the install; the shared ones above
    them (``/opt``, ``/usr/local``, ...) are only checked."""
    own = [
        layout.prefix,
        layout.libexec,
        layout.base or layout.state,
        layout.logs,
        layout.run,
        layout.service_file,
    ]
    problems: dict[str, None] = {}
    for path in own:
        parent = layout.fs(path).parent
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        problem = _root_owned_chain(parent)
        if problem:
            problems[problem] = None
    return list(problems)


def check_interpreter(python: Path, runner: Runner = _run) -> str | None:
    """Why ``python`` cannot carry a Tier 2 engine, or ``None``.

    The virtualenv loads the standard library from the interpreter's own
    installation, so that installation must be root-owned too: a Python
    under a user-owned prefix (Homebrew on macOS, pyenv) would let the
    agent's user change the engine anyway.
    """
    real = Path(os.path.realpath(python))
    problem = _root_owned_chain(real)
    if problem:
        return problem
    done = runner(
        [
            str(real),
            "-c",
            "import sys, sysconfig; print(sys.version_info >= (3, 10)); "
            "print(sysconfig.get_paths()['stdlib'])",
        ]
    )
    if done.returncode != 0:
        return f"{real} does not run: {(done.stderr or '').strip()[:200]}"
    lines = (done.stdout or "").split("\n")
    if not lines or lines[0].strip() != "True":
        return f"{real} is older than Python 3.10"
    stdlib = Path(lines[1].strip()) if len(lines) > 1 else None
    if stdlib is not None:
        problem = _root_owned_chain(stdlib)
        if problem:
            return f"its standard library: {problem}"
    return None


def choose_interpreter(
    platform: str, given: str | None, runner: Runner = _run
) -> tuple[Path | None, list[str]]:
    """The interpreter to build the engine on, and why the others were
    passed over."""
    tried: list[str] = []
    candidates = (
        [given]
        if given
        else list(_CANDIDATES_DARWIN if platform == "darwin" else _CANDIDATES_LINUX)
    )
    for c in candidates:
        path = Path(c)
        if not path.exists():
            tried.append(f"{c}: not found")
            continue
        problem = check_interpreter(path, runner)
        if problem is None:
            return path, tried
        tried.append(f"{c}: {problem}")
    return None, tried


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------


def daemon_argv(layout: Layout) -> list[str]:
    return [
        str(layout.python),
        "-m",
        "sponsio.cli",
        "daemon",
        "run",
        "--socket",
        str(layout.socket),
        "--mode",
        "0666",
        "--state-dir",
        str(layout.state),
        "--plugin-root",
        str(layout.plugins),
    ]


def hook_command(layout: Layout, host: str, event: str | None = None) -> str:
    from sponsio.integrations.hosts import shim_command

    return shim_command(
        host,
        event=event,
        socket_path=layout.socket,
        plugin_root=layout.plugins,
        state_dir=layout.state,
        python=str(layout.python),
        shim=layout.shim,
        no_start=True,
    )


def claude_managed_settings(layout: Layout, *, allow_user_hooks: bool) -> dict:
    """Our ``managed-settings.d`` file for Claude Code.

    Hooks on PreToolUse (``onFailure: block``) and PostToolUse; only
    managed hooks load unless ``allow_user_hooks``; deny rules for
    edits to Sponsio's paths (``Edit(//abs/**)``: ``Write(...)`` rules
    never match) and reads of its state; the sandbox on, failing when it
    cannot start and never running a command outside it; and the
    minimum Claude Code that honours ``onFailure``.
    """
    pre = {
        "type": "command",
        "command": hook_command(layout, "claude-code"),
        "timeout": 10,
        "onFailure": "block",
    }
    post = {
        "type": "command",
        "command": hook_command(layout, "claude-code"),
        "timeout": 10,
    }
    # A directory is denied with everything under it; a file (the service
    # definition) by its own path, which `<file>/**` would not match.
    paths = [str(p) for p in layout.protected_paths()]
    files = {layout.service_file}
    deny = [
        f"Edit(/{p})" if p in files else f"Edit(/{p}/**)"
        for p in layout.protected_paths()
    ]
    deny.append(f"Read(/{layout.state}/**)")
    return {
        "allowManagedHooksOnly": not allow_user_hooks,
        "requiredMinimumVersion": MIN_CLAUDE_CODE,
        "hooks": {
            "PreToolUse": [{"matcher": "*", "hooks": [pre]}],
            "PostToolUse": [{"matcher": "*", "hooks": [post]}],
        },
        "permissions": {"deny": deny},
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,
            "allowUnsandboxedCommands": False,
            "filesystem": {"denyWrite": paths},
        },
    }


def cursor_hooks(layout: Layout, existing: str | None) -> str:
    """Cursor's system ``hooks.json``, keeping hooks that are not ours."""
    from sponsio.integrations.cursor_install import (
        _INSTALLED_EVENTS_FAILCLOSED,
        _INSTALLED_EVENTS_SOFT,
    )
    from sponsio.integrations.hosts import is_sponsio_hook_command

    try:
        data = json.loads(existing) if existing else {}
    except ValueError as e:
        raise InstallError(
            f"{layout.cursor_hooks} is not valid JSON ({e}); fix or move it first"
        ) from e
    if not isinstance(data, dict):
        data = {}
    data.setdefault("version", 1)
    hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    for event, entries in list(hooks.items()):
        if isinstance(entries, list):
            hooks[event] = [
                e
                for e in entries
                if not (
                    isinstance(e, dict)
                    and isinstance(e.get("command"), str)
                    and is_sponsio_hook_command(e["command"], "cursor")
                )
            ]
    for event in _INSTALLED_EVENTS_FAILCLOSED:
        hooks.setdefault(event, []).append(
            {"command": hook_command(layout, "cursor", event), "failClosed": True}
        )
    for event in _INSTALLED_EVENTS_SOFT:
        hooks.setdefault(event, []).append(
            {"command": hook_command(layout, "cursor", event)}
        )
    data["hooks"] = {k: v for k, v in hooks.items() if v}
    return json.dumps(data, indent=2) + "\n"


def daemon_config(layout: Layout) -> str:
    from sponsio.daemon.config import DaemonConfig

    import yaml

    cfg = DaemonConfig(plugin_root=layout.plugins)
    return (
        "# Sponsio daemon config (system install). Change it through the admin\n"
        "# socket (`sudo sponsio host install --mode ...`).\n"
        + yaml.safe_dump(cfg.to_dict(), sort_keys=False)
    )


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


@dataclass
class InstallOptions:
    hosts: tuple[str, ...] = ("claude-code",)
    python: str | None = None
    package: str | None = None
    """What pip installs into the engine: a version spec, a wheel, or a
    source directory. Default: this version of sponsio with signing."""
    allow_user_hooks: bool = False
    migrate_from: Path | None = None
    """A user's home whose ``~/.sponsio/plugins`` is imported."""


def _package_spec(given: str | None) -> str:
    if given:
        return given
    import sponsio

    return f"sponsio[signing]=={sponsio.__version__}"


def _engine_file(ex: Executor, name: str) -> Path:
    """A shim file from the engine just installed (or, in a dry run or a
    test, from the running package)."""
    from sponsio.shim import mcp_shim_path, shim_path

    local = {"sponsio_shim.py": shim_path(), "sponsio_mcp.py": mcp_shim_path()}[name]
    if ex.dry_run or ex.layout.root != Path("/"):
        return local
    out = ex.run(
        [
            str(ex.layout.python),
            "-c",
            "import sponsio.shim, pathlib; "
            f"print(pathlib.Path(sponsio.shim.__file__).parent / {name!r})",
        ]
    )
    return Path(out.strip()) if out.strip() else local


def import_user_libraries(
    ex: Executor, home: Path | None, hosts: tuple[str, ...]
) -> list[str]:
    """P1.2: copy the user's host buckets into the daemon's rule store,
    and add the bundled library for a wired host that has none. Returns
    one line per bucket."""
    lines: list[str] = []
    source = home / ".sponsio" / "plugins" if home else None
    if source is not None and source.is_dir():
        for lib in sorted(source.glob("*/sponsio.yaml")):
            bucket = lib.parent.name
            target = ex.layout.plugins / bucket
            if ex.exists(target / "sponsio.yaml"):
                lines.append(f"kept {bucket}: the rule store already has it")
                continue
            skipped = ex.copytree(lib.parent, target, owner=SERVICE_USER)
            lines.append(f"imported {bucket} ({_count(lib)} contracts) from {lib}")
            for item in skipped:
                lines.append(f"left out {item}: not a regular file in {bucket}")
    wanted = [b for host in hosts for b in HOST_BUCKETS.get(host, ())]
    for bucket in wanted:
        if ex.exists(ex.layout.plugins / bucket / "sponsio.yaml"):
            continue
        if any(line.startswith(f"imported {bucket} ") for line in lines):
            continue
        from sponsio.cli.groups.plugin import _stamp_bundled_source
        from sponsio.plugin.registry import read_bundled

        try:
            text = read_bundled(bucket)
        except (FileNotFoundError, ModuleNotFoundError):
            continue
        ex.mkdir(ex.layout.plugins / bucket, owner=SERVICE_USER, mode=0o700)
        ex.write(
            ex.layout.plugins / bucket / "sponsio.yaml",
            _stamp_bundled_source(text, bucket),
            owner=SERVICE_USER,
            mode=0o600,
        )
        lines.append(f"added the bundled library for {bucket}")
    return lines


def _count(lib: Path) -> int | str:
    try:
        from sponsio.config import load_config

        cfg = load_config(lib)
    except Exception:  # noqa: BLE001 - a count for a message
        return "?"
    return sum(len(a.contracts) for a in cfg.agents.values())


def install(ex: Executor, opts: InstallOptions) -> dict:
    """Set up the system install. Returns the manifest it wrote."""
    from sponsio.system_install import linux, macos

    lay = ex.layout
    platform_mod = macos if lay.darwin else linux
    for host in opts.hosts:
        if host not in HOSTS:
            raise InstallError(f"unknown host {host!r}; one of {', '.join(HOSTS)}")

    # 1. The interpreter the engine runs on, checked before anything changes.
    if lay.root == Path("/"):
        python, tried = choose_interpreter(lay.platform, opts.python, _run)
        if python is None:
            message = (
                "no root-owned Python 3.10+ to build the engine on:\n  "
                + "\n  ".join(tried)
                + "\nInstall one (macOS: python.org; Linux: the distribution's "
                "python3), or pass --python."
            )
            if not ex.dry_run:
                raise InstallError(message)
            ex.echo("warning: " + message)
            python = Path(opts.python or "<python3.10+>")
    else:
        python = Path(opts.python or sys.executable)

    # 2. Directories above Sponsio's own that someone other than root can
    # change: renaming one swaps out what is below it. They are shared, so
    # they are not changed here; the operator fixes them.
    if lay.root == Path("/"):
        problems = shared_parent_problems(lay)
        if problems:
            message = (
                "a directory above Sponsio's own can be changed by a user other "
                "than root, so the agent's user could replace Sponsio's files:\n  "
                + "\n  ".join(problems)
                + "\nMake each root-owned and not group- or world-writable (for "
                "example `sudo chown root <dir> && sudo chmod go-w <dir>`), then "
                "run the install again."
            )
            if not ex.dry_run:
                raise InstallError(message)
            ex.echo("warning: " + message)

    # 3. Who the daemon runs as.
    platform_mod.ensure_user(ex)

    # 4. The engine.
    ex.mkdir(lay.prefix.parent, shared=True)
    ex.run([str(python), "-m", "venv", "--clear", str(lay.prefix)])
    # `venv --clear` empties a directory that exists but keeps its owner
    # and mode: make it root's whatever it was before.
    ex.mkdir(lay.prefix)
    ex.run(
        [
            str(lay.python),
            "-m",
            "pip",
            "install",
            "--quiet",
            "--disable-pip-version-check",
            _package_spec(opts.package),
        ]
    )

    # Everything Sponsio put under its prefix: root's, writable by no one
    # else.
    ex.secure_tree(lay.prefix)

    # 5. The shims, root-owned.
    ex.mkdir(lay.libexec)
    ex.copy(_engine_file(ex, "sponsio_shim.py"), lay.shim)
    ex.copy(_engine_file(ex, "sponsio_mcp.py"), lay.mcp_shim)

    # 6. State, logs and sockets.
    if lay.base:
        ex.mkdir(lay.base)
    ex.mkdir(lay.state, owner=SERVICE_USER, mode=0o700)
    ex.mkdir(lay.plugins, owner=SERVICE_USER, mode=0o700)
    ex.mkdir(lay.logs, owner=SERVICE_USER, mode=0o700)
    ex.mkdir(lay.run, owner=SERVICE_USER, mode=0o755)
    if not ex.exists(lay.state / "daemon.yaml"):
        ex.write(
            lay.state / "daemon.yaml",
            daemon_config(lay),
            owner=SERVICE_USER,
            mode=0o600,
        )

    # 7. The rules (P1.2).
    imported = import_user_libraries(ex, opts.migrate_from, opts.hosts)
    for line in imported:
        ex.echo("  " + line)

    # 8. The service.
    platform_mod.install_service(ex)

    # 9. The hosts.
    if "claude-code" in opts.hosts:
        ex.mkdir(lay.claude_settings.parent, shared=True)
        ex.tighten(lay.claude_settings.parent)
        ex.tighten(lay.claude_managed_dir)
        ex.write(
            lay.claude_settings,
            json.dumps(
                claude_managed_settings(lay, allow_user_hooks=opts.allow_user_hooks),
                indent=2,
            )
            + "\n",
        )
    if "cursor" in opts.hosts:
        ex.mkdir(lay.cursor_hooks.parent, shared=True)
        ex.tighten(lay.cursor_hooks.parent)
        ex.write(lay.cursor_hooks, cursor_hooks(lay, ex.read(lay.cursor_hooks)))

    import sponsio

    manifest = {
        "version": getattr(sponsio, "__version__", None),
        "platform": lay.platform,
        "python": str(python),
        "hosts": list(opts.hosts),
        "allow_user_hooks": opts.allow_user_hooks,
        "imported": imported,
        "socket": str(lay.socket),
        "state": str(lay.state),
    }
    ex.write(
        lay.manifest,
        json.dumps(manifest, indent=2) + "\n",
        owner=SERVICE_USER,
        mode=0o600,
    )
    return manifest


def uninstall(ex: Executor, *, keep_state: bool = False) -> list[str]:
    """Remove the system install. Returns what to tell the operator."""
    from sponsio.integrations.hosts import is_sponsio_hook_command
    from sponsio.system_install import linux, macos

    lay = ex.layout
    platform_mod = macos if lay.darwin else linux
    notes: list[str] = []
    platform_mod.remove_service(ex)
    ex.remove(lay.claude_settings)
    raw = ex.read(lay.cursor_hooks)
    if raw is not None:
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("hooks"), dict):
            kept = {
                event: [
                    e
                    for e in entries
                    if not (
                        isinstance(e, dict)
                        and isinstance(e.get("command"), str)
                        and is_sponsio_hook_command(e["command"], "cursor")
                    )
                ]
                for event, entries in data["hooks"].items()
                if isinstance(entries, list)
            }
            kept = {k: v for k, v in kept.items() if v}
            if kept:
                data["hooks"] = kept
                ex.write(lay.cursor_hooks, json.dumps(data, indent=2) + "\n")
            else:
                ex.remove(lay.cursor_hooks)
    ex.remove(lay.libexec)
    ex.remove(lay.prefix)
    ex.remove(lay.run)
    if keep_state:
        # The kept files belong to the service user: removing it would
        # leave them to whichever account gets its id next.
        notes.append(
            f"kept {lay.state} and {lay.logs}, and the {SERVICE_USER} user that "
            f"owns them (--keep-state)"
        )
    else:
        ex.remove(lay.state)
        ex.remove(lay.logs)
        if lay.base:
            ex.remove(lay.base)
        platform_mod.remove_user(ex)
    notes.append(
        "Hooks now load from user settings again. To guard Claude Code at "
        "Tier 1, run `sponsio host install claude-code` as your own user."
    )
    return notes
