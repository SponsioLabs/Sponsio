"""``sponsio run --sandbox`` (P3.3, P3.5): inside the sandbox the only way
out is the egress proxy, Sponsio's state cannot be read, nothing outside
the workspace can be written, and credentials are gone from the
environment, while the daemon's agent socket still answers.

Runs for real on macOS (sandbox-exec) and on Linux with bubblewrap and
unprivileged user namespaces; skipped elsewhere. Every connection here
is to loopback.
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from sponsio.daemon import config
from sponsio.daemon.guard_service import GuardService
from sponsio.daemon.handlers import register_default_handlers
from sponsio.daemon.server import DaemonServer
from sponsio.guard_stdin import GuardPolicy
from sponsio.sandbox import SandboxError, plan, run, secret_env


def _can_sandbox() -> bool:
    if sys.platform == "darwin":
        return Path("/usr/bin/sandbox-exec").exists()
    if sys.platform.startswith("linux") and shutil.which("bwrap"):
        probe = subprocess.run(
            ["bwrap", "--ro-bind", "/", "/", "--unshare-net", "true"],
            capture_output=True,
        )
        return probe.returncode == 0
    return False


needs_sandbox = pytest.mark.skipif(
    not _can_sandbox(), reason="no usable OS sandbox here"
)


def test_credentials_are_stripped_but_the_model_key_is_kept():
    env = {
        "GITHUB_TOKEN": "x",
        "AWS_SECRET_ACCESS_KEY": "x",
        "STRIPE_API_KEY": "x",
        "DB_PASSWORD": "x",
        "ANTHROPIC_API_KEY": "x",
        "PATH": "/bin",
        "MY_TOKEN": "x",
    }
    assert secret_env(env) == [
        "AWS_SECRET_ACCESS_KEY",
        "DB_PASSWORD",
        "GITHUB_TOKEN",
        "MY_TOKEN",
        "STRIPE_API_KEY",
    ]
    assert "MY_TOKEN" not in secret_env(env, frozenset({"MY_TOKEN"}))


def test_no_command_and_no_platform_are_refused(tmp_path):
    with pytest.raises(SandboxError):
        plan([], workspace=tmp_path, socket_path=tmp_path / "s", egress={})
    with pytest.raises(SandboxError, match="macOS and Linux only"):
        plan(
            ["true"],
            workspace=tmp_path,
            socket_path=tmp_path / "s",
            egress={"port": 1, "unix": "x"},
            platform="win32",
        )


def test_no_daemon_means_no_agent(tmp_path):
    marker = tmp_path / "ran"
    with pytest.raises(SandboxError, match="not started"):
        run(
            ["touch", str(marker)],
            workspace=tmp_path,
            socket_path=tmp_path / "absent.sock",
            echo=lambda _l: None,
        )
    assert not marker.exists()


class _Hello(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b"hello through the proxy"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a):
        pass


PROBE = r"""
import json, os, socket, sys, urllib.request
out = {}
def attempt(name, fn):
    try:
        out[name] = ["ok", repr(fn())[:80]]
    except Exception as e:
        out[name] = ["fail", type(e).__name__]
web, closed, agent_sock, home = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], sys.argv[4]
attempt("direct", lambda: socket.create_connection(("127.0.0.1", closed), timeout=3).close())
attempt("proxied", lambda: urllib.request.urlopen(f"http://127.0.0.1:{web}/x", timeout=10).read())
attempt("read_sponsio", lambda: open(os.path.join(home, ".sponsio", "plugins", "rules.yaml")).read())
attempt("write_home", lambda: open(os.path.join(home, "planted"), "w").write("x"))
attempt("write_workspace", lambda: open("inside.txt", "w").write("x"))
def ping():
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(5); s.connect(agent_sock)
    body = json.dumps({"method": "ping", "params": {}}).encode()
    s.sendall(len(body).to_bytes(4, "big") + body)
    n = int.from_bytes(s.recv(4), "big"); data = b""
    while len(data) < n: data += s.recv(n - len(data))
    return json.loads(data)["ok"]
attempt("agent_socket", ping)
out["env_token"] = os.environ.get("GITHUB_TOKEN")
out["proxy"] = os.environ.get("HTTP_PROXY")
print(json.dumps(out))
"""


@needs_sandbox
def test_inside_the_sandbox(tmp_path_factory):
    # Not under /tmp: the sandbox lets the agent write its temp dirs.
    base = Path(tempfile.mkdtemp(prefix="spx-", dir="/var/tmp")).resolve()
    try:
        home = base / "home"
        sponsio_dir = home / ".sponsio"
        (sponsio_dir / "plugins").mkdir(parents=True)
        (sponsio_dir / "plugins" / "rules.yaml").write_text("secret: rules\n")
        workspace = base / "ws"
        workspace.mkdir()
        sock = sponsio_dir / "s.sock"

        web = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Hello)
        threading.Thread(target=web.serve_forever, daemon=True).start()
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        closed.listen(1)

        state = base / "state"
        config.save(state, config.DaemonConfig(egress_allow=("127.0.0.1",)))
        svc = GuardService(
            root=sponsio_dir / "plugins",
            state_dir=state,
            run_dir=base,
            policy=GuardPolicy(mode="enforce", on_error="deny"),
        )
        server = DaemonServer(sock, accept_timeout_s=0.05, socket_mode=0o666)
        register_default_handlers(server, guard_service=svc)
        server.start()
        probe = base / "probe.py"
        probe.write_text(PROBE)
        out_file = workspace / "out.json"
        env = {
            "HOME": str(home),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "GITHUB_TOKEN": "must-not-arrive",
        }
        try:
            from sponsio.daemon.client import DaemonClient

            client = DaemonClient(socket_path=sock, timeout_s=10)
            egress = client.call("egress.open", {"session": "probe"})
            p = plan(
                [
                    sys.executable,
                    "-I",
                    str(probe),
                    str(web.server_address[1]),
                    str(closed.getsockname()[1]),
                    str(sock),
                    str(home),
                ],
                workspace=workspace,
                socket_path=sock,
                egress=egress,
                env=env,
            )
            with open(out_file, "w") as fh:
                done = subprocess.run(
                    p.argv,
                    env=p.env,
                    cwd=workspace,
                    stdout=fh,
                    stderr=subprocess.PIPE,
                    timeout=120,
                )
            for f in p.cleanup:
                f.unlink(missing_ok=True)
        finally:
            server.stop(join_timeout_s=0.5)
            svc.close_gateway()
            web.shutdown()
            closed.close()

        assert done.returncode == 0, done.stderr.decode()[-2000:]
        out = json.loads(out_file.read_text())
        assert out["direct"][0] == "fail", out
        assert out["proxied"] == ["ok", "b'hello through the proxy'"], out
        assert out["read_sponsio"][0] == "fail", out
        assert out["write_home"][0] == "fail", out
        assert out["write_workspace"][0] == "ok", out
        assert out["agent_socket"] == ["ok", "True"], out
        assert out["env_token"] is None and out["proxy"].startswith("http://127.0.0.1:")
        assert not (home / "planted").exists()
        assert "GITHUB_TOKEN" in p.stripped
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_container_mode_only_routes_the_network(tmp_path):
    p = plan(
        ["my-agent", "--flag"],
        workspace=tmp_path,
        socket_path=tmp_path / "agent.sock",
        egress={"unix": "/run/sponsio/egress-x.sock", "port": 1, "session": "s"},
        env={"PATH": "/bin", "GITHUB_TOKEN": "x"},
        platform="container",
    )
    assert p.argv[-3:] == ["--", "my-agent", "--flag"]
    assert "/run/sponsio/egress-x.sock" in p.argv and "bwrap" not in p.argv[0]
    assert p.env["HTTPS_PROXY"] == "http://127.0.0.1:3128"
    assert "GITHUB_TOKEN" not in p.env
