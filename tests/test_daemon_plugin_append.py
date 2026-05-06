"""End-to-end tests for ``plugin.append`` over the daemon RPC.

These pair with :mod:`tests.test_plugin_append` (which exercises the
direct-mode CLI path).  Same input scenarios, executed through a real
running daemon — proves the handler + client + protocol all line up
and that the structural-additive guarantees are preserved across the
IPC boundary.

Layered with :mod:`tests.test_daemon_ipc` which proves the protocol
itself; this file proves the *plugin.append handler specifically* is
wired correctly into that protocol.
"""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

import pytest

from sponsio.daemon.client import DaemonClient, DaemonError
from sponsio.daemon.handlers import register_default_handlers
from sponsio.daemon.server import DaemonServer


@pytest.fixture
def short_tmp() -> Path:
    """Short-path tempdir for AF_UNIX binding (~104 char cap on macOS)."""
    d = Path(tempfile.mkdtemp(prefix="sp-", dir="/tmp"))
    yield d
    try:
        for sub in d.rglob("*"):
            if sub.is_file() or sub.is_symlink() or sub.is_socket():
                try:
                    sub.unlink()
                except OSError:
                    pass
        for sub in sorted(d.rglob("*"), reverse=True):
            if sub.is_dir():
                try:
                    sub.rmdir()
                except OSError:
                    pass
        d.rmdir()
    except OSError:
        pass


@pytest.fixture
def server(short_tmp: Path) -> DaemonServer:
    s = DaemonServer(short_tmp / "sock", accept_timeout_s=0.05)
    register_default_handlers(s)
    s.start()
    yield s
    s.stop(join_timeout_s=0.5)


@pytest.fixture
def plugin_root(short_tmp: Path) -> Path:
    """A throwaway plugin root with a bootstrapped ``_host_cursor`` bucket."""
    root = short_tmp / "plugins"
    bucket = root / "_host_cursor"
    bucket.mkdir(parents=True)
    (bucket / "sponsio.yaml").write_text(
        'version: "1"\n'
        "agents:\n"
        "  _host_cursor:\n"
        "    contracts:\n"
        '      - desc: "shipped"\n'
        "        G:\n"
        "          pattern: rate_limit\n"
        "          args: [Bash, 50]\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def client(server: DaemonServer) -> DaemonClient:
    return DaemonClient(socket_path=server.socket_path, timeout_s=2.0)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_append_via_daemon_writes_file(client: DaemonClient, plugin_root: Path):
    staging = (
        "agents:\n"
        "  _host_cursor:\n"
        "    contracts:\n"
        '      - desc: "R1: no DELETE on Railway"\n'
        "        G:\n"
        "          pattern: arg_blacklist\n"
        "          args:\n"
        "            - Bash\n"
        "            - command\n"
        '            - ["curl -X DELETE"]\n'
    )

    result = client.call(
        "plugin.append",
        {
            "target": "_host_cursor",
            "staging_yaml": staging,
            "dry_run": False,
            "root": str(plugin_root),
        },
    )

    assert result["appended_count"] == 1
    assert result["agent_id"] == "_host_cursor"
    assert result["descs"] == ["R1: no DELETE on Railway"]
    assert result["dry_run"] is False

    # The merge actually landed on disk.
    on_disk = (plugin_root / "_host_cursor" / "sponsio.yaml").read_text()
    assert "R1: no DELETE on Railway" in on_disk
    assert "shipped" in on_disk  # original kept


def test_append_via_daemon_dry_run(client: DaemonClient, plugin_root: Path):
    target = plugin_root / "_host_cursor" / "sponsio.yaml"
    before = target.read_text()

    result = client.call(
        "plugin.append",
        {
            "target": "_host_cursor",
            "staging_yaml": (
                "agents:\n"
                "  _host_cursor:\n"
                "    contracts:\n"
                '      - desc: "new rule"\n'
                "        G: { pattern: rate_limit, args: [Read, 100] }\n"
            ),
            "dry_run": True,
            "root": str(plugin_root),
        },
    )

    assert result["dry_run"] is True
    assert result["appended_count"] == 1
    # File untouched — dry-run is genuinely dry across the IPC boundary.
    assert target.read_text() == before


# ---------------------------------------------------------------------------
# Structural-additive guarantees survive the IPC boundary
# ---------------------------------------------------------------------------


def test_daemon_rejects_customized_block_with_validation_code(
    client: DaemonClient, plugin_root: Path
):
    """The same rejection the in-process CLI does, but the error code
    arrives back as ``"validation"`` so the client can surface a clean
    message instead of "internal error"."""
    with pytest.raises(DaemonError) as excinfo:
        client.call(
            "plugin.append",
            {
                "target": "_host_cursor",
                "staging_yaml": (
                    "agents:\n"
                    "  _host_cursor:\n"
                    "    customized:\n"
                    "      - match: { desc: shipped }\n"
                    "        disabled: true\n"
                ),
                "root": str(plugin_root),
            },
        )
    assert excinfo.value.code == "validation"
    assert "customized" in str(excinfo.value).lower()


def test_daemon_rejects_disabled_on_contract(client: DaemonClient, plugin_root: Path):
    with pytest.raises(DaemonError) as excinfo:
        client.call(
            "plugin.append",
            {
                "target": "_host_cursor",
                "staging_yaml": (
                    "agents:\n"
                    "  _host_cursor:\n"
                    "    contracts:\n"
                    '      - desc: "new"\n'
                    "        disabled: true\n"
                    "        G: { pattern: rate_limit, args: [Bash, 0] }\n"
                ),
                "root": str(plugin_root),
            },
        )
    assert excinfo.value.code == "validation"
    assert "disabled" in str(excinfo.value).lower()


def test_daemon_rejects_desc_collision(client: DaemonClient, plugin_root: Path):
    with pytest.raises(DaemonError) as excinfo:
        client.call(
            "plugin.append",
            {
                "target": "_host_cursor",
                "staging_yaml": (
                    "agents:\n"
                    "  _host_cursor:\n"
                    "    contracts:\n"
                    '      - desc: "shipped"\n'
                    "        G: { pattern: rate_limit, args: [Bash, 999] }\n"
                ),
                "root": str(plugin_root),
            },
        )
    assert excinfo.value.code == "validation"
    assert "already exist" in str(excinfo.value)


def test_daemon_rejects_legacy_overrides_key(client: DaemonClient, plugin_root: Path):
    """The dropped legacy alias ``overrides:`` produces the rename
    hint, also code=validation."""
    with pytest.raises(DaemonError) as excinfo:
        client.call(
            "plugin.append",
            {
                "target": "_host_cursor",
                "staging_yaml": (
                    "agents:\n"
                    "  _host_cursor:\n"
                    "    overrides:\n"
                    "      - match: { desc: shipped }\n"
                    "        disabled: true\n"
                ),
                "root": str(plugin_root),
            },
        )
    assert excinfo.value.code == "validation"
    assert "no longer accepted" in str(excinfo.value)


def test_daemon_rejects_missing_target(client: DaemonClient, plugin_root: Path):
    with pytest.raises(DaemonError) as excinfo:
        client.call(
            "plugin.append",
            {
                "target": "ghost-bucket",
                "staging_yaml": (
                    "agents:\n"
                    "  ghost-bucket:\n"
                    "    contracts:\n"
                    "      - desc: x\n"
                    "        G: { pattern: rate_limit, args: [Bash, 1] }\n"
                ),
                "root": str(plugin_root),
            },
        )
    assert excinfo.value.code == "not_found"


# ---------------------------------------------------------------------------
# Param validation at the handler boundary
# ---------------------------------------------------------------------------


def test_daemon_rejects_missing_target_param(client: DaemonClient):
    with pytest.raises(DaemonError) as excinfo:
        client.call("plugin.append", {"staging_yaml": "agents: {}"})
    assert excinfo.value.code == "validation"
    assert "target" in str(excinfo.value)


def test_daemon_rejects_missing_staging_yaml_param(client: DaemonClient):
    with pytest.raises(DaemonError) as excinfo:
        client.call("plugin.append", {"target": "_host_cursor"})
    assert excinfo.value.code == "validation"
    assert "staging_yaml" in str(excinfo.value)


# ---------------------------------------------------------------------------
# CLI integration — `sponsio plugin append` routes through daemon
# ---------------------------------------------------------------------------


def test_cli_routes_through_running_daemon(
    server: DaemonServer, plugin_root: Path, monkeypatch
):
    """When the daemon socket is reachable, the CLI's ``plugin append``
    must use it instead of writing the file directly.  We prove this
    by pointing the daemon's plugin root at a fixture, the CLI's
    ``$SPONSIO_DAEMON_SOCKET`` at the test daemon, and observing that
    the file shape on disk matches what the daemon produced."""
    from click.testing import CliRunner

    from sponsio.cli import cli

    monkeypatch.setenv("SPONSIO_DAEMON_SOCKET", str(server.socket_path))

    staging_path = plugin_root.parent / ".sponsio.staging.yaml"
    staging_path.write_text(
        "agents:\n"
        "  _host_cursor:\n"
        "    contracts:\n"
        '      - desc: "daemon-routed rule"\n'
        "        G: { pattern: rate_limit, args: [Read, 10] }\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "plugin",
            "append",
            "--from",
            str(staging_path),
            "--target",
            "_host_cursor",
            "--root",
            str(plugin_root),
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert "appended 1 contract" in result.output

    on_disk = (plugin_root / "_host_cursor" / "sponsio.yaml").read_text()
    assert "daemon-routed rule" in on_disk


def test_cli_no_daemon_flag_skips_daemon(
    server: DaemonServer, plugin_root: Path, monkeypatch
):
    """``--no-daemon`` forces the in-process direct-write path even
    when a daemon is reachable.  Used by tests / dev workflows that
    want to bypass IPC."""
    from click.testing import CliRunner

    from sponsio.cli import cli

    # Even with daemon socket env var set, --no-daemon should win.
    monkeypatch.setenv("SPONSIO_DAEMON_SOCKET", str(server.socket_path))

    staging_path = plugin_root.parent / "stage.yaml"
    staging_path.write_text(
        "agents:\n"
        "  _host_cursor:\n"
        "    contracts:\n"
        '      - desc: "in-process rule"\n'
        "        G: { pattern: rate_limit, args: [Read, 10] }\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "plugin",
            "append",
            "--from",
            str(staging_path),
            "--target",
            "_host_cursor",
            "--root",
            str(plugin_root),
            "--no-daemon",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    on_disk = (plugin_root / "_host_cursor" / "sponsio.yaml").read_text()
    assert "in-process rule" in on_disk


# ---------------------------------------------------------------------------
# Concurrency — daemon must serialise file writes per target
# ---------------------------------------------------------------------------


def test_concurrent_appends_are_serialised(
    client: DaemonClient, server: DaemonServer, plugin_root: Path
):
    """Two clients append simultaneously.  Both must succeed and the
    final file must contain both rules — neither write may clobber
    the other.  Our atomic ``os.replace`` semantics give last-writer-
    wins on the rename, but each writer reads-modifies-writes the same
    target file, so without daemon-side serialisation we'd risk
    losing one.  Either Python GIL + the small critical section keep
    us safe, or a future commit needs an explicit lock — this test
    pins the contract either way.
    """

    def _staging(desc: str) -> str:
        return (
            "agents:\n"
            "  _host_cursor:\n"
            "    contracts:\n"
            f'      - desc: "{desc}"\n'
            "        G: { pattern: rate_limit, args: [Read, 10] }\n"
        )

    def _go(desc: str) -> dict:
        c = DaemonClient(server.socket_path, timeout_s=5.0)
        return c.call(
            "plugin.append",
            {
                "target": "_host_cursor",
                "staging_yaml": _staging(desc),
                "root": str(plugin_root),
            },
        )

    results: list[dict] = []
    threads = [
        threading.Thread(target=lambda d=d: results.append(_go(d)))
        for d in ("rule-A", "rule-B", "rule-C", "rule-D")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    on_disk = (plugin_root / "_host_cursor" / "sponsio.yaml").read_text()
    for d in ("rule-A", "rule-B", "rule-C", "rule-D"):
        assert d in on_disk, f"lost rule {d!r}: {on_disk}"
