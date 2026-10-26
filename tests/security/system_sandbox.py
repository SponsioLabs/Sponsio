"""The boundary corpus against a real system install (Tier 2).

The Tier 1 :class:`~tests.security.corpus.Sandbox` builds a throwaway
install inside a temporary HOME. At Tier 2 the install is the machine's
own, made by ``sudo sponsio install --system`` (only in CI or a VM), and
the agent is a separate, unprivileged OS user. :class:`SystemSandbox`
finds that install through the installer's own
:class:`~sponsio.system_install.Layout` and gives a vector three things:

* the hook path as the agent: :meth:`SystemSandbox.check` runs the hook
  command from Claude Code's managed settings as the agent's user, with
  the payload on stdin, and parses the reply into a :class:`Verdict`;
* permission assertions that name the vector when they fail;
* rule preparation as root through the admin socket, undone in
  :meth:`SystemSandbox.close`.

Who runs what. The test process runs either as the agent itself (then
agent-side calls run directly and admin preparation is unavailable), or
as a user with passwordless sudo and ``SPONSIO_TEST_AGENT_USER`` naming
the agent: agent-side calls then run through ``sudo -n -u <agent>``, as
the CI job does, and admin calls through ``sudo -n``.

Nothing here runs unless ``SPONSIO_TEST_TIER=2`` and a system install is
present; otherwise the case is skipped with the reason. Under
``SPONSIO_TEST_REQUIRE_SYSTEM=1`` (the CI job) a missing install or a
missing privilege fails the case instead, so a broken job cannot pass by
skipping everything.

Safety: agent-side commands run as the agent's user, with a minimal
environment and a fresh temporary HOME; :meth:`SystemSandbox.execute`
refuses network-capable commands and paths outside that HOME and the
install.
"""

from __future__ import annotations

import json
import os
import pwd
import re
import shlex
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, NoReturn, Sequence

import pytest

from sponsio.system_install import HOST_BUCKETS, SERVICE_USER, Layout
from tests.security.corpus import _NETWORK_TOKENS, HOST

TIER_ENV = "SPONSIO_TEST_TIER"
AGENT_USER_ENV = "SPONSIO_TEST_AGENT_USER"
REQUIRE_ENV = "SPONSIO_TEST_REQUIRE_SYSTEM"

DENIED_ERRNOS = frozenset({"EACCES", "EPERM", "EROFS"})
"""What a refused open, create or connect fails with."""

_TIMEOUT_S = 60
_HOME_PREFIX = "spt2-"
_AGENT_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


class Unavailable(Exception):
    """The system sandbox cannot run here; the message says why."""


class AdminError(RuntimeError):
    """An admin call made as root was refused or failed."""

    def __init__(self, code: str | None, message: str) -> None:
        super().__init__(message)
        self.code = code


def unavailable(reason: str) -> NoReturn:
    """Skip with ``reason``, or fail under ``SPONSIO_TEST_REQUIRE_SYSTEM=1``."""
    if os.environ.get(REQUIRE_ENV, "").strip() == "1":
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason)


def _me() -> str:
    return pwd.getpwuid(os.geteuid()).pw_name


def _user_name(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    """The host's view of one hook run.

    ``decision`` is ``"allow"``, ``"deny"``, ``"ask"``, or ``"error"``.
    ``"error"`` means the hook exited without a decision (an exit code
    other than 0 or 2); Claude Code then runs the tool, so a vector that
    expects a block asserts :attr:`denied`, not ``not allowed``.
    :attr:`allowed` and :attr:`reason` match the Tier 1 ``GuardOutcome``.
    """

    decision: str
    reason: str
    exit_code: int
    stdout: str
    stderr: str

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"

    @property
    def denied(self) -> bool:
        return self.decision == "deny"

    @property
    def fail_closed(self) -> bool:
        """Denied because the hook could not get a decision (exit 2), as
        opposed to a rule's deny (exit 0 with the deny JSON)."""
        return self.denied and self.exit_code == 2

    @classmethod
    def parse(cls, proc: subprocess.CompletedProcess[str]) -> "Verdict":
        out = proc.stdout.strip()
        try:
            data = json.loads(out) if out else None
        except ValueError:
            data = None
        spec = data.get("hookSpecificOutput") if isinstance(data, dict) else None
        if isinstance(spec, dict) and spec.get("permissionDecision"):
            decision = str(spec["permissionDecision"])
            reason = str(spec.get("permissionDecisionReason") or "")
        elif isinstance(data, dict) and data.get("decision") == "block":
            decision, reason = "deny", str(data.get("reason") or "")
        elif proc.returncode == 2:
            decision, reason = "deny", proc.stderr.strip()
        elif proc.returncode == 0 and (
            (data is None and not out) or isinstance(data, dict)
        ):
            decision, reason = "allow", ""
        else:
            decision, reason = "error", proc.stderr.strip() or out
        return cls(decision, reason, proc.returncode, proc.stdout, proc.stderr)


# ---------------------------------------------------------------------------
# Running as the agent and as root
# ---------------------------------------------------------------------------


class Privileges:
    """How commands run as the agent and as root.

    This base runs both as the current user, which is the agent, with
    "root" meaning this same user. That is enough for a temporary layout
    this user owns, which is what the unit tests use.
    """

    def __init__(self, agent_user: str | None = None) -> None:
        self.agent_user = agent_user or _me()

    def agent(
        self, argv: Sequence[str], env: dict[str, str], cwd: str
    ) -> tuple[list[str], dict[str, str], str]:
        """``(command, environment, working directory)`` for
        :func:`subprocess.run` to run ``argv`` as the agent."""
        return list(argv), dict(env), cwd

    def root(self, argv: Sequence[str]) -> list[str]:
        return list(argv)

    def root_problem(self) -> str | None:
        """Why root is not available, or ``None``."""
        return None


class SudoPrivileges(Privileges):
    """The agent through ``sudo -n -u <agent>`` (or directly, when the
    agent is this user) and root through ``sudo -n``."""

    def __init__(self, agent_user: str | None = None) -> None:
        super().__init__(agent_user)
        self._root_problem: str | None | bool = False

    def agent(
        self, argv: Sequence[str], env: dict[str, str], cwd: str
    ) -> tuple[list[str], dict[str, str], str]:
        if self.agent_user == _me():
            return list(argv), dict(env), cwd
        # The agent's HOME is closed to this user, so the agent changes
        # into it itself.
        pinned = [f"{k}={v}" for k, v in env.items()]
        chdir = ["/bin/sh", "-c", 'cd "$1" || exit 125; shift; exec "$@"', "sh", cwd]
        return (
            ["sudo", "-n", "-u", self.agent_user, "/usr/bin/env", "-i", *pinned]
            + chdir
            + list(argv),
            {"PATH": _AGENT_PATH},
            "/",
        )

    def root(self, argv: Sequence[str]) -> list[str]:
        return list(argv) if os.geteuid() == 0 else ["sudo", "-n", *argv]

    def root_problem(self) -> str | None:
        if self._root_problem is False:
            if os.geteuid() == 0:
                self._root_problem = None
            else:
                probe = subprocess.run(
                    ["sudo", "-n", "true"],
                    capture_output=True,
                    text=True,
                    timeout=_TIMEOUT_S,
                )
                self._root_problem = (
                    None
                    if probe.returncode == 0
                    else f"preparing rules needs root, and `sudo -n` is not "
                    f"available to {_me()}: {probe.stderr.strip()}"
                )
        return self._root_problem  # type: ignore[return-value]


# Run by the engine's interpreter as the agent: one probe, one JSON line.
_AGENT_PROBE = r"""
import errno, json, os, socket, stat, sys

op, path = sys.argv[1], sys.argv[2]


def probe():
    if op == "admin":
        from sponsio.daemon.protocol import Request, Response, read_frame, write_frame

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(10)
            s.connect(path)
            write_frame(s, Request(method="ping", params={}).to_json())
            r = Response.from_json(read_frame(s))
        return {"ok": True, "response": {"ok": r.ok, "code": r.code, "error": r.error}}
    try:
        isdir = stat.S_ISDIR(os.stat(path).st_mode)
    except FileNotFoundError:
        isdir = None
    if op == "read":
        if isdir is None:
            raise FileNotFoundError(errno.ENOENT, "no such file", path)
        if isdir:
            os.listdir(path)
        else:
            fd = os.open(path, os.O_RDONLY)
            os.read(fd, 1)
            os.close(fd)
    elif isdir is False:
        os.close(os.open(path, os.O_WRONLY | os.O_APPEND))
    else:
        target = os.path.join(path, ".sponsio-probe-%d" % os.getpid()) if isdir else path
        os.close(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        os.unlink(target)
    return {"ok": True}


try:
    out = probe()
except OSError as e:
    out = {"ok": False, "errno": errno.errorcode.get(e.errno, str(e.errno)), "error": str(e)}
print(json.dumps(out))
"""

# Run by the engine's interpreter as root: one request on stdin, one reply.
_ROOT_HELPER = r"""
import json, os, sys, tempfile

req = json.loads(sys.stdin.read())
op = req["op"]


def main():
    if op == "admin":
        from pathlib import Path
        from sponsio.daemon.client import DaemonClient, DaemonError

        client = DaemonClient(socket_path=Path(req["socket"]), timeout_s=30)
        try:
            return {"ok": True, "result": client.call(req["method"], req["params"])}
        except DaemonError as e:
            return {"ok": False, "code": e.code, "error": str(e)}
    path = req["path"]
    if op == "read":
        try:
            with open(path, "rb") as fh:
                fh.seek(req.get("offset") or 0)
                return {"ok": True, "text": fh.read().decode("utf-8")}
        except FileNotFoundError:
            return {"ok": True, "text": None}
    if op == "size":
        try:
            return {"ok": True, "size": os.stat(path).st_size}
        except FileNotFoundError:
            return {"ok": True, "size": None}
    if op == "stat":
        st = os.lstat(path)
        return {"ok": True, "uid": st.st_uid, "gid": st.st_gid, "mode": st.st_mode}
    if op == "remove":
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        return {"ok": True}
    if op == "rmdir":
        try:
            os.rmdir(path)
        except FileNotFoundError:
            pass
        return {"ok": True}
    if op == "write":
        # Same owner and mode as the file it replaces; a new file gets its
        # directory's owner, mode 0600 (a new directory 0700).
        parent = os.path.dirname(path)
        created = not os.path.isdir(parent)
        if created:
            above = os.lstat(os.path.dirname(parent))
            os.mkdir(parent, 0o700)
            if (above.st_uid, above.st_gid) != (os.geteuid(), os.getegid()):
                os.chown(parent, above.st_uid, above.st_gid)
        try:
            like = os.lstat(path)
            mode = like.st_mode & 0o7777
        except FileNotFoundError:
            like, mode = os.lstat(parent), 0o600
        fd, tmp = tempfile.mkstemp(dir=parent, prefix=".sponsio-test-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(req["text"].encode("utf-8"))
                os.fchmod(fh.fileno(), mode)
                if (like.st_uid, like.st_gid) != (os.geteuid(), os.getegid()):
                    os.fchown(fh.fileno(), like.st_uid, like.st_gid)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        return {"ok": True, "created_dir": created}
    raise SystemExit("unknown op %r" % op)


print(json.dumps(main()))
"""


# ---------------------------------------------------------------------------
# Finding the install
# ---------------------------------------------------------------------------


def find_install(root: Path = Path("/")) -> tuple[Layout | None, str]:
    """The system install under ``root`` (``/`` on a real machine), or
    ``None`` and why not. Every path comes from the installer's
    :class:`Layout`."""
    if sys.platform not in ("darwin", "linux"):
        return None, f"a system install needs macOS or Linux, not {sys.platform}"
    layout = Layout(platform=sys.platform, root=root)
    if layout.fs(layout.socket).exists():
        return layout, ""
    if layout.fs(layout.service_file).exists():
        return None, (
            f"a system install is here ({layout.service_file}) but its daemon "
            f"is not listening on {layout.socket}"
        )
    return None, (
        f"no system install on this machine ({layout.socket} does not exist); "
        "Tier 2 runs after `sudo sponsio install --system`, in CI or a VM only"
    )


def privileges_from_env() -> Privileges:
    """The agent named by ``SPONSIO_TEST_AGENT_USER``, else this user."""
    agent = os.environ.get(AGENT_USER_ENV, "").strip() or None
    if agent is None or agent == _me():
        if os.geteuid() == 0:
            raise Unavailable(
                f"running as root: run as the agent's user, or set {AGENT_USER_ENV}"
            )
        return SudoPrivileges()
    try:
        pwd.getpwnam(agent)
    except KeyError:
        raise Unavailable(f"{AGENT_USER_ENV}={agent} is not a user here") from None
    probe = subprocess.run(
        ["sudo", "-n", "-u", agent, "true"],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_S,
    )
    if probe.returncode != 0:
        raise Unavailable(
            f"cannot run as {agent} through `sudo -n -u`: {probe.stderr.strip()}"
        )
    return SudoPrivileges(agent)


def open_system_sandbox(
    vector_id: str,
    *,
    root: Path = Path("/"),
    privileges: Privileges | None = None,
) -> "SystemSandbox":
    """A :class:`SystemSandbox` for one test case, or a skip that says why
    not (a failure under ``SPONSIO_TEST_REQUIRE_SYSTEM=1``)."""
    if os.environ.get(TIER_ENV, "").strip() != "2":
        pytest.skip(f"Tier 2 expectations run only with {TIER_ENV}=2")
    layout, reason = find_install(root)
    if layout is None:
        unavailable(f"[{vector_id}] {reason}")
    if privileges is None:
        try:
            privileges = privileges_from_env()
        except Unavailable as e:
            unavailable(f"[{vector_id}] {e}")
    return SystemSandbox(layout, vector_id=vector_id, privileges=privileges)


# ---------------------------------------------------------------------------
# The sandbox
# ---------------------------------------------------------------------------


class SystemSandbox:
    """One test case against the system install.

    Paths: :attr:`layout` holds every path as it is on the machine;
    the attributes below are those paths as this process reaches them
    (the same unless the layout has a test ``root``). Methods that take a
    path accept either form, or a path relative to the agent's HOME.

    Each sandbox has its own agent HOME (:attr:`home`, also the hook's
    working directory) and its own session id (:attr:`session_id`).
    """

    def __init__(
        self,
        layout: Layout,
        *,
        vector_id: str,
        privileges: Privileges | None = None,
    ) -> None:
        from sponsio.guard_stdin import library_path_for

        self.layout = layout
        self.vector_id = vector_id
        self.privileges = privileges or Privileges()
        self.agent_user = self.privileges.agent_user
        self.bucket = HOST_BUCKETS[HOST][0]
        self.state_dir = layout.fs(layout.state)
        self.plugin_root = layout.fs(layout.plugins)
        self.library = library_path_for(self.bucket, root=self.plugin_root)
        self.socket_path = layout.fs(layout.socket)
        self.admin_socket_path = layout.fs(layout.admin_socket)
        self.settings = layout.fs(layout.claude_settings)
        self.python = layout.fs(layout.python)
        self.shim = layout.fs(layout.shim)
        self.session_id = self._fresh_session_id()
        self._undo: list[tuple[str, Callable[[], None]]] = []
        self._remembered: set[Path] = set()
        self._created_dirs: set[Path] = set()
        self._closed = False
        self.home = self._make_home()
        self._audit_offset = self._audit_size()

    # ---- identity ----

    def _msg(self, text: str) -> str:
        return f"[{self.vector_id}] {text}"

    def _fresh_session_id(self) -> str:
        slug = re.sub(r"[^A-Za-z0-9_-]+", "-", self.vector_id).strip("-") or "case"
        return f"t2-{slug}-{uuid.uuid4().hex[:12]}"

    def new_session(self) -> str:
        """Switch to a fresh session id and return it."""
        self.session_id = self._fresh_session_id()
        return self.session_id

    def path(self, rel: str) -> str:
        """Absolute path of a file under the agent's HOME, as the agent
        would spell it in a tool argument."""
        return str(self.home / rel)

    def fs(self, path: str | Path) -> Path:
        """``path`` as this process reaches it: relative paths are under
        the agent's HOME, layout paths are mapped under a test root."""
        p = Path(path)
        if not p.is_absolute():
            return self.home / p
        root = self.layout.root
        if root == Path("/") or p.is_relative_to(root):
            return p
        return self.layout.fs(p)

    # ---- running as the agent ----

    def _agent_env(self, home: Path) -> dict[str, str]:
        return {
            "HOME": str(home),
            "PATH": _AGENT_PATH,
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "CLAUDE_PROJECT_DIR": str(home),
        }

    def _as_agent(
        self,
        argv: Sequence[str],
        *,
        input: str | None = None,
        home: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        home = home if home is not None else self.home
        cmd, env, cwd = self.privileges.agent(argv, self._agent_env(home), str(home))
        return subprocess.run(
            cmd,
            input=input,
            capture_output=True,
            text=True,
            env=env,
            cwd=cwd,
            timeout=_TIMEOUT_S,
        )

    def _agent_python(self, code: str, *args: str, home: Path | None = None):
        return self._as_agent([str(self.python), "-I", "-c", code, *args], home=home)

    def _make_home(self) -> Path:
        proc = self._agent_python(
            "import tempfile; "
            f"print(tempfile.mkdtemp(prefix={_HOME_PREFIX!r}, dir='/tmp'))",
            home=Path("/tmp"),
        )
        out = proc.stdout.strip()
        if proc.returncode != 0 or not out:
            raise RuntimeError(
                self._msg(f"could not make a HOME as {self.agent_user}: {proc.stderr}")
            )
        return Path(out)

    def _remove_home(self) -> None:
        home = os.path.realpath(self.home)
        assert Path(home).name.startswith(_HOME_PREFIX) and Path(home).parent in (
            Path("/tmp"),
            Path("/private/tmp"),
        ), self._msg(f"refusing to remove {home}: not a sandbox HOME")
        self._agent_python(
            "import shutil, sys; shutil.rmtree(sys.argv[1], ignore_errors=True)",
            home,
            home=Path("/tmp"),
        )

    # ---- the hook path ----

    def hook_argv(self, event_name: str = "PreToolUse") -> list[str]:
        """The hook command Claude Code runs for ``event_name``, from the
        managed settings the installer wrote."""
        from sponsio.integrations.hosts import is_sponsio_hook_command

        try:
            data = json.loads(self.settings.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            unavailable(self._msg(f"cannot read {self.layout.claude_settings}: {e}"))
        commands = [
            h["command"]
            for entry in (data.get("hooks") or {}).get(event_name) or []
            for h in entry.get("hooks") or []
            if isinstance(h.get("command"), str)
            and is_sponsio_hook_command(h["command"], HOST)
        ]
        if not commands:
            unavailable(
                self._msg(
                    f"no Sponsio {event_name} hook in {self.layout.claude_settings}"
                )
            )
        argv = shlex.split(commands[0])
        assert str(self.layout.shim) in argv, self._msg(
            f"the managed {event_name} hook does not run {self.layout.shim}: "
            f"{commands[0]}"
        )
        return [str(self.fs(a)) if a.startswith("/") else a for a in argv]

    def check(
        self, payload: dict[str, Any], *, session_id: str | None = None
    ) -> Verdict:
        """Run the managed hook as the agent with ``payload`` on stdin.

        The payload gets this sandbox's session id and the agent's HOME
        as ``cwd`` unless it carries its own; ``session_id`` overrides.
        """
        event = dict(payload)
        event.setdefault("hook_event_name", "PreToolUse")
        if session_id is not None:
            event["session_id"] = session_id
        event.setdefault("session_id", self.session_id)
        event.setdefault("cwd", str(self.home))
        argv = self.hook_argv(event["hook_event_name"])
        return Verdict.parse(self._as_agent(argv, input=json.dumps(event)))

    def execute(
        self, command: str, *, touches: Sequence[str | Path] = ()
    ) -> subprocess.CompletedProcess[str]:
        """Run ``command`` with ``/bin/sh`` as the agent, in its HOME.

        Refuses anything that names a network-capable program, and any
        path in ``touches`` outside the agent's HOME and the install's
        protected paths.
        """
        assert not _NETWORK_TOKENS.search(command), self._msg(
            "refusing to execute a command that could reach the network"
        )
        allowed = [Path(os.path.realpath(self.home))] + [
            Path(os.path.realpath(self.fs(p))) for p in self.layout.protected_paths()
        ]
        for t in touches:
            target = Path(os.path.realpath(self.fs(t)))
            assert any(target.is_relative_to(a) for a in allowed), self._msg(
                f"{t} is outside the agent's HOME and the install"
            )
        return self._as_agent(["/bin/sh", "-c", command])

    # ---- permission assertions (as the agent) ----

    def _probe(self, op: str, path: str | Path) -> dict[str, Any]:
        proc = self._agent_python(_AGENT_PROBE, op, str(path))
        try:
            return json.loads(proc.stdout.strip().splitlines()[-1])
        except (IndexError, ValueError):
            raise RuntimeError(
                self._msg(
                    f"probe {op} {path} as {self.agent_user} failed: {proc.stderr}"
                )
            ) from None

    def _assert_refused(self, op: str, verb: str, path: str | Path) -> None:
        target = self.fs(path)
        res = self._probe(op, target)
        assert not res["ok"], self._msg(f"{target} is {verb} by {self.agent_user}")
        assert res["errno"] in DENIED_ERRNOS, self._msg(
            f"{op} {target} as {self.agent_user} failed with {res['errno']} "
            f"({res['error']}), not a permission error"
        )

    def assert_not_writable(self, path: str | Path) -> None:
        """The agent can neither open ``path`` for writing nor, for a
        directory (or a path that does not exist), create a file in it."""
        self._assert_refused("write", "writable", path)

    def assert_not_readable(self, path: str | Path) -> None:
        """The agent can neither read ``path`` nor, for a directory, list it."""
        self._assert_refused("read", "readable", path)

    def assert_admin_refused(self) -> None:
        """The admin socket refuses the agent: at connect, or with
        ``permission`` on the first call."""
        res = self._probe("admin", self.admin_socket_path)
        if not res["ok"]:
            assert res["errno"] in DENIED_ERRNOS, self._msg(
                f"connecting to {self.admin_socket_path} as {self.agent_user} "
                f"failed with {res['errno']} ({res['error']}), not a permission error"
            )
            return
        response = res["response"]
        assert not response["ok"] and response["code"] == "permission", self._msg(
            f"admin socket {self.admin_socket_path} accepted {self.agent_user}: "
            f"{response}"
        )

    def assert_owned_by(
        self, path: str | Path, owners: Sequence[str] = ("root", SERVICE_USER)
    ) -> None:
        """``path`` itself (not a link's target) belongs to one of ``owners``."""
        target = self.fs(path)
        try:
            uid = os.lstat(target).st_uid
        except PermissionError:
            uid = self._root({"op": "stat", "path": str(target)})["uid"]
        owner = _user_name(uid)
        assert owner in owners, self._msg(
            f"{target} is owned by {owner}, expected one of {', '.join(owners)}"
        )

    # ---- preparation as root ----

    def _root(self, request: dict[str, Any]) -> dict[str, Any]:
        problem = self.privileges.root_problem()
        if problem:
            unavailable(self._msg(problem))
        proc = subprocess.run(
            self.privileges.root([str(self.python), "-I", "-c", _ROOT_HELPER]),
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
        )
        try:
            return json.loads(proc.stdout.strip().splitlines()[-1])
        except (IndexError, ValueError):
            raise RuntimeError(
                self._msg(f"root helper {request['op']} failed: {proc.stderr}")
            ) from None

    def admin(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Call ``method`` on the admin socket as root; :class:`AdminError`
        when the daemon refuses it."""
        res = self._root(
            {
                "op": "admin",
                "socket": str(self.admin_socket_path),
                "method": method,
                "params": params or {},
            }
        )
        if not res["ok"]:
            raise AdminError(res.get("code"), self._msg(f"{method}: {res['error']}"))
        return res["result"]

    def read_library(self, bucket: str | None = None) -> str | None:
        """The bucket's library as the daemon's rule store has it."""
        return self._root({"op": "read", "path": str(self._library_for(bucket))})[
            "text"
        ]

    def _library_for(self, bucket: str | None) -> Path:
        from sponsio.guard_stdin import library_path_for

        return library_path_for(bucket or self.bucket, root=self.plugin_root)

    def install_library(self, text: str, *, bucket: str | None = None) -> None:
        """Replace the bucket's library in the rule store and apply it
        (``rules.reload``). :meth:`close` puts the original back."""
        bucket = bucket or self.bucket
        path = self._library_for(bucket)
        if path not in self._remembered:
            self._remembered.add(path)
            original = self._root({"op": "read", "path": str(path)})["text"]
            self._undo.append(
                (f"restore {path}", lambda: self._restore(path, bucket, original))
            )
        res = self._root({"op": "write", "path": str(path), "text": text})
        if res.get("created_dir"):
            self._created_dirs.add(path.parent)
        self._apply(bucket)

    def _restore(self, path: Path, bucket: str, original: str | None) -> None:
        if original is None:
            self._root({"op": "remove", "path": str(path)})
            if path.parent in self._created_dirs:
                self._root({"op": "rmdir", "path": str(path.parent)})
        else:
            self._root({"op": "write", "path": str(path), "text": original})
        self._apply(bucket)

    def _apply(self, bucket: str) -> None:
        """Make the daemon enforce the bucket's file as it is now.

        ``rules.reload`` applies only a change the daemon has already
        seen, and it sees one when it next loads the bucket; a weaker
        file it has not seen would deny the next call instead. So
        ``rules.status`` first, which loads every bucket from disk.
        """
        self.admin("rules.status")
        self.admin("rules.reload", {"bucket": bucket})
        held = self.admin("rules.status").get("held") or {}
        assert bucket not in held, self._msg(
            f"{bucket} is still held after rules.reload: {held[bucket]}"
        )

    def install_default_library(self, *, bucket: str | None = None) -> None:
        """The bundled library, as the installer writes it."""
        from sponsio.cli.groups.plugin import _stamp_bundled_source
        from sponsio.plugin.registry import read_bundled

        bucket = bucket or self.bucket
        self.install_library(
            _stamp_bundled_source(read_bundled(bucket), bucket), bucket=bucket
        )

    def add_contracts(
        self, contracts: list[dict[str, Any]], *, bucket: str | None = None
    ) -> None:
        """Append ``contracts`` to the bucket's library (see
        :meth:`install_library`)."""
        import yaml

        bucket = bucket or self.bucket
        data = yaml.safe_load(self.read_library(bucket) or "") or {}
        data.setdefault("version", "1")
        agents = data.setdefault("agents", {})
        name = bucket if bucket in agents or not agents else next(iter(agents))
        agent = agents.setdefault(name, {}) or {}
        agents[name] = agent
        agent.setdefault("contracts", []).extend(contracts)
        self.install_library(yaml.safe_dump(data, sort_keys=False), bucket=bucket)

    def set_mode(self, mode: str, *, host: str = HOST) -> None:
        """``mode.set`` as root; :meth:`close` sets the previous mode back."""
        import yaml

        from sponsio.daemon.config import CONFIG_FILE, DaemonConfig

        raw = self._root({"op": "read", "path": str(self.state_dir / CONFIG_FILE)})
        before = DaemonConfig.from_dict(
            yaml.safe_load(raw["text"] or "") or {}
        ).mode_for(host)
        self.admin("mode.set", {"host": host, "mode": mode})
        self._undo.append(
            (
                f"mode {host}={before}",
                lambda: self.admin("mode.set", {"host": host, "mode": before}),
            )
        )

    # ---- audit ----

    def _audit_path(self) -> Path:
        from sponsio.daemon.audit import AUDIT_FILE

        return self.state_dir / AUDIT_FILE

    def _audit_size(self) -> int | None:
        if self.privileges.root_problem():
            return None
        return self._root({"op": "size", "path": str(self._audit_path())})["size"]

    def audit_events(self, kind: str | None = None) -> list[dict[str, Any]]:
        """Audit records the daemon wrote since this sandbox opened
        (read as root), optionally of one ``event`` kind."""
        text = self._root(
            {
                "op": "read",
                "path": str(self._audit_path()),
                "offset": self._audit_offset or 0,
            }
        )["text"]
        out = []
        for line in (text or "").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and (kind is None or rec.get("event") == kind):
                out.append(rec)
        return out

    # ---- lifecycle ----

    def close(self) -> None:
        """Undo every preparation (last first) and remove the agent's
        HOME. A step that fails is reported after the others ran."""
        if self._closed:
            return
        self._closed = True
        failed = []
        for what, undo in reversed(self._undo):
            try:
                undo()
            except Exception as e:  # noqa: BLE001 - report every failed step
                failed.append(f"{what}: {e}")
        self._undo.clear()
        self._remove_home()
        assert not failed, self._msg(
            "could not put the install back: " + "; ".join(failed)
        )
