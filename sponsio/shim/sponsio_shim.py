"""Sponsio hook shim: forward one hook event to the daemon, fail closed.

Standard library only. This file must not import ``sponsio``: the host
runs it with ``python3 -I -S`` for every tool call, and interpreter
start-up is the whole budget.

Usage::

    sponsio_shim.py <host> [--event NAME] --socket PATH [--deadline SECONDS]
                    [--plugin-root DIR] [--state-dir DIR] [--sponsio CMD]
                    [--no-start]

``host`` is ``claude-code``, ``cursor`` or ``openclaw``. The installer
writes every option as an absolute value, so the shim reads nothing from
the environment: not the socket, not the roots. The mode is not the
shim's at all: it is in the daemon's config, so a ``--mode`` that an
older installer wrote on the command is accepted and not sent. The
daemon decides; the shim writes the daemon's stdout and exits with the
daemon's exit code.

If no daemon answers, the shim starts one (``sponsio daemon run
--detach``, with a scrubbed environment), waits for the socket, and
retries once. With ``--no-start`` (a system install, where the service
manager runs the daemon as its own user) it does not try. If that fails too, or the deadline passes, it writes the
host's deny shape and exits 2, so a host that reads either signal
blocks the call. The deny reason says how to recover; the agent cannot,
since its own tools are denied too.
"""

from __future__ import annotations

import json
import os
import shlex
import socket
import struct
import subprocess
import sys
import time

DEFAULT_DEADLINE_S = 2.0
"""Wall-clock budget for the whole hook, start to exit."""
START_WAIT_S = 1.5
"""How long to wait for a daemon the shim started."""
_LEN = struct.Struct(">I")
_MAX_FRAME = 16 * 1024 * 1024

_CURSOR_PRE_EVENTS = (
    "preToolUse",
    "beforeShellExecution",
    "beforeMCPExecution",
    "beforeReadFile",
    "beforeTabFileRead",
    "beforeSubmitPrompt",
)

KNOWN_HOSTS = ("claude-code", "cursor", "openclaw")
"""Hosts whose deny shape the shim knows. Kept equal to
``sponsio.daemon.guard_service.KNOWN_HOSTS`` (a test checks)."""

_ENV_PASSTHROUGH = (
    "HOME",
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "USER",
    "LOGNAME",
    "SHELL",
    "TERM",
    "TZ",
    "OPENCLAW_HOME",
)


class Options:
    def __init__(self) -> None:
        self.host = ""
        self.event: str | None = None
        self.socket = ""
        self.mode: str | None = None
        self.deadline = DEFAULT_DEADLINE_S
        self.plugin_root: str | None = None
        self.state_dir: str | None = None
        self.sponsio: list[str] | None = None
        self.no_start = False


def parse_args(argv: list[str]) -> Options:
    opts = Options()
    it = iter(argv)
    for arg in it:
        if arg == "--event":
            opts.event = next(it, None)
        elif arg == "--socket":
            opts.socket = next(it, "") or ""
        elif arg == "--mode":
            opts.mode = next(it, None)
        elif arg == "--deadline":
            try:
                opts.deadline = float(next(it, "") or DEFAULT_DEADLINE_S)
            except ValueError:
                opts.deadline = DEFAULT_DEADLINE_S
        elif arg == "--plugin-root":
            opts.plugin_root = next(it, None)
        elif arg == "--state-dir":
            opts.state_dir = next(it, None)
        elif arg == "--sponsio":
            raw = next(it, None)
            opts.sponsio = shlex.split(raw) if raw else None
        elif arg == "--no-start":
            opts.no_start = True
        elif arg.startswith("--"):
            next(it, None)
        elif not opts.host:
            opts.host = arg
    if not opts.socket:
        home = os.environ.get("HOME") or os.path.expanduser("~")
        opts.socket = os.path.join(home, ".sponsio", "sponsio.sock")
    return opts


# ---------------------------------------------------------------------------
# Which RPC, and what a deny looks like, per host
# ---------------------------------------------------------------------------


def classify(host: str, event: str | None, raw: str) -> tuple[str, str]:
    """``(method, event_name)`` for this hook invocation.

    Cursor names the event on the command line. Claude Code and OpenClaw
    carry it in the JSON body; anything that is not ``PreToolUse`` has
    already run and is observed, not gated.
    """
    if host == "cursor":
        name = event or "preToolUse"
        return ("guard.check" if name in _CURSOR_PRE_EVENTS else "guard.observe"), name
    name = "PreToolUse"
    try:
        body = json.loads(raw) if raw.strip() else None
    except ValueError:
        body = None
    if isinstance(body, dict):
        found = body.get("hook_event_name")
        if isinstance(found, str) and found:
            name = found
    return ("guard.check" if name == "PreToolUse" else "guard.observe"), name


_HOST_CONFIG = {
    "claude-code": "~/.claude/settings.json",
    "cursor": "~/.cursor/hooks.json",
}


def system_recovery_reason(cause: str) -> str:
    """The deny reason when the system daemon (Tier 2) does not answer."""
    if sys.platform == "darwin":
        restart = "sudo launchctl kickstart -k system/dev.sponsio.daemon"
    else:
        restart = "sudo systemctl restart sponsio"
    return (
        f"Sponsio: the system enforcement daemon is not reachable ({cause}). "
        f"This tool call is denied, and every tool call will be denied until "
        f"it is back. From your own terminal: `{restart}`, then "
        f"`sponsio doctor`."
    )


def recovery_reason(
    cause: str, socket_path: str, state_dir: str | None, host: str = ""
) -> str:
    """The deny reason when no daemon answers.

    Written for the person, not the agent: while the daemon is down every
    tool call is denied, including the ones the agent would use to fix
    it. Two ways out are given, because starting a daemon does not help
    when the hook itself points somewhere that cannot work (a socket in a
    deleted directory, an interpreter that is gone).
    """
    log = os.path.join(state_dir or "~/.sponsio/state", "daemon.log")
    config = _HOST_CONFIG.get(host, "the host's hook config")
    uninstall = f"sponsio host uninstall {host}" if host else "sponsio host uninstall"
    return (
        f"Sponsio: the enforcement daemon is not reachable ({cause}). "
        f"This tool call is denied, and every tool call will be denied until "
        f"this is fixed; the agent cannot repair it itself. "
        f"From your own terminal: (1) run `sponsio daemon run --detach "
        f"--socket {socket_path}` and then `sponsio doctor`; "
        f"(2) if that does not help, remove the hook with `{uninstall}`, or "
        f"delete the entries naming sponsio_shim.py from {config}. "
        f"Log: {log}"
    )


def _recovery(opts: "Options", cause: str) -> str:
    """The deny reason for a call no daemon decided: the system install's
    recovery (``--no-start``), or the per-user one."""
    if opts.no_start:
        return system_recovery_reason(cause)
    return recovery_reason(cause, opts.socket, opts.state_dir, opts.host)


def deny_reply(host: str, event_name: str, reason: str) -> tuple[str, int]:
    """The host's deny shape for ``event_name``, always with exit 2."""
    if host == "cursor":
        if event_name == "beforeSubmitPrompt":
            payload: dict = {"continue": False, "user_message": reason}
        elif event_name in _CURSOR_PRE_EVENTS:
            payload = {
                "permission": "deny",
                "user_message": reason,
                "agent_message": reason,
            }
        else:
            payload = {"additional_context": reason}
        return json.dumps(payload), 2
    if event_name == "PreToolUse":
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    else:
        payload = {"decision": "block", "reason": reason}
    return json.dumps(payload), 2


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


class Unreachable(Exception):
    """No daemon answered (missing socket, refused, or timed out)."""


class ReplyLost(Unreachable):
    """The request was sent and no reply came back: the daemon may have
    acted on it. Retrying is safe only for a request that has no effect
    of its own."""


def call(socket_path: str, method: str, params: dict, deadline: float) -> dict:
    """One RPC; raises :class:`Unreachable` when no daemon answers and
    ``RuntimeError`` when a daemon answers with an error."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise Unreachable("deadline passed")
    body = json.dumps({"method": method, "params": params}).encode("utf-8")
    sent = False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(remaining)
            s.connect(socket_path)
            s.sendall(_LEN.pack(len(body)) + body)
            sent = True
            header = _recv_exact(s, _LEN.size)
            (length,) = _LEN.unpack(header)
            if length > _MAX_FRAME:
                raise RuntimeError("reply too large")
            reply = json.loads(_recv_exact(s, length).decode("utf-8"))
    except socket.timeout as e:
        raise (ReplyLost if sent else Unreachable)("timed out") from e
    except (OSError, ConnectionError) as e:
        raise (ReplyLost if sent else Unreachable)(_describe(e, socket_path)) from e
    if not isinstance(reply, dict) or not reply.get("ok"):
        err = reply.get("error") if isinstance(reply, dict) else reply
        raise RuntimeError(f"daemon error: {err}")
    result = reply.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("malformed daemon reply")
    return result


def _recv_exact(s: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("daemon closed the connection")
        buf += chunk
    return bytes(buf)


def _describe(e: BaseException, socket_path: str) -> str:
    if isinstance(e, FileNotFoundError):
        return f"no socket at {socket_path}"
    if isinstance(e, ConnectionRefusedError):
        return "connection refused"
    return f"{e.__class__.__name__}: {e}"


def start_daemon(opts: Options) -> str | None:
    """Spawn ``sponsio daemon run --detach`` for our socket. Returns a
    failure description, or ``None`` when the spawn itself succeeded."""
    cmd = list(opts.sponsio) if opts.sponsio else [sys.executable, "-m", "sponsio.cli"]
    cmd += ["daemon", "run", "--detach", "--socket", opts.socket]
    if opts.plugin_root:
        cmd += ["--plugin-root", opts.plugin_root]
    if opts.state_dir:
        cmd += ["--state-dir", opts.state_dir]
    env = {k: v for k, v in os.environ.items() if k in _ENV_PASSTHROUGH}
    try:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
            close_fds=True,
        )
    except OSError as e:
        return f"could not start the daemon: {e}"
    return None


def wait_for_socket(socket_path: str, until: float) -> bool:
    while time.monotonic() < until:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(0.2)
                s.connect(socket_path)
            return True
        except OSError:
            time.sleep(0.05)
    return False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def unknown_host_reason(host: str) -> str:
    named = f"unknown host {host!r}" if host else "no host named"
    return (
        f"Sponsio: the hook command has {named}, so this tool call is denied. "
        f"Re-run `sponsio host install <host>` from your own terminal "
        f"(one of: {', '.join(KNOWN_HOSTS)})."
    )


def run(argv: list[str], stdin_text: str) -> tuple[str, int, str]:
    """``(stdout, exit_code, stderr)`` for one hook invocation."""
    opts = parse_args(argv)
    if opts.host not in KNOWN_HOSTS:
        # No daemon call: the daemon would deny it too, and without a
        # known host there is no protocol to ask it in.
        reason = unknown_host_reason(opts.host)
        _method, event_name = classify(opts.host, opts.event, stdin_text)
        out, code = deny_reply(opts.host, event_name, reason)
        return out, code, reason + "\n"
    started = time.monotonic()
    deadline = started + opts.deadline
    method, event_name = classify(opts.host, opts.event, stdin_text)
    params: dict = {"host": opts.host or None, "event": opts.event, "raw": stdin_text}

    cause = ""
    try:
        result = call(opts.socket, method, params, deadline)
    except Unreachable as first:
        if opts.no_start:
            reason = system_recovery_reason(str(first))
            out, code = deny_reply(opts.host, event_name, reason)
            return out, code, reason + "\n"
        cause = str(first)
        failure = start_daemon(opts)
        if failure is not None:
            cause = f"{cause}; {failure}"
            result = None
        else:
            until = min(deadline, time.monotonic() + START_WAIT_S)
            if wait_for_socket(opts.socket, until):
                try:
                    result = call(opts.socket, method, params, deadline)
                except Unreachable as second:
                    cause = f"{cause}; after restart: {second}"
                    result = None
                except RuntimeError as e:
                    cause = f"{cause}; after restart: {e}"
                    result = None
            else:
                cause = f"{cause}; the daemon did not come up"
                result = None
    except RuntimeError as e:
        cause = str(e)
        result = None

    if result is None:
        reason = _recovery(opts, cause)
        out, code = deny_reply(opts.host, event_name, reason)
        return out, code, reason + "\n"
    out = result.get("stdout")
    code = result.get("exit_code")
    warning = result.get("stderr")
    return (
        out if isinstance(out, str) else "",
        code if isinstance(code, int) else 2,
        warning + "\n" if isinstance(warning, str) and warning else "",
    )


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        stdin_text = sys.stdin.read()
    except OSError:
        stdin_text = ""
    try:
        out, code, err = run(argv, stdin_text)
    except Exception as e:  # noqa: BLE001 - any failure in the shim is a deny
        opts = parse_args(argv)
        _method, event_name = classify(opts.host, opts.event, stdin_text)
        reason = _recovery(opts, f"shim failure {e.__class__.__name__}: {e}")
        out, code = deny_reply(opts.host, event_name, reason)
        err = reason + "\n"
    try:
        if out:
            sys.stdout.write(out)
            if not out.endswith("\n"):
                sys.stdout.write("\n")
            sys.stdout.flush()
        if err:
            sys.stderr.write(err)
            sys.stderr.flush()
    except OSError:
        pass
    return code


if __name__ == "__main__":
    sys.exit(main())
