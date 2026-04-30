"""Cross-namespace trace merge — :func:`sponsio.guard_stdin._load_prior_events`.

Trace files are written per-namespace (``derive_plugin_id`` decides
which dir), but lethal-trifecta-class flows (untrusted host read →
exfil via MCP plugin) need cross-namespace temporal evaluation:
a contract in ``gh_gist/sponsio.yaml`` whose assumption ``A`` is
``F(arg_field_has(read, path, "/work/notes/"))`` must observe the
``read`` event that landed in ``_host_openclaw``'s trace.

These tests assert the read-side merge without breaking namespace
isolation on the write-side or conversation-bucketing of cumulative
contracts.
"""

from __future__ import annotations

import json

import pytest

from sponsio.guard_stdin import (
    _append_event,
    _load_prior_events,
    _trace_file_for,
)


@pytest.fixture
def sponsio_root(tmp_path, monkeypatch):
    monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))
    monkeypatch.delenv("SPONSIO_SHIELD_TRACE_ROOT", raising=False)
    return tmp_path


def _ev(ts: int, agent: str, tool: str, **args):
    return {
        "ts": ts,
        "agent": agent,
        "type": "tool_call",
        "tool": tool,
        "args": args or None,
    }


def test_cross_namespace_merge_host_read_visible_to_plugin(sponsio_root):
    """The lethal-trifecta scenario: host ``read`` in ``_host_openclaw``
    must be visible when evaluating contracts in ``gh_gist``.
    """
    _append_event(
        "_host_openclaw",
        _ev(1, "_host_openclaw", "read", path="/work/notes/SALARY.md"),
    )
    _append_event(
        "gh_gist",
        _ev(2, "gh_gist", "mcp__gh_gist__create_gist", public=True),
    )

    events = _load_prior_events("gh_gist")
    tools = [e.tool for e in events]
    assert tools == ["read", "mcp__gh_gist__create_gist"]

    host_reads = [e for e in events if e.tool == "read"]
    assert len(host_reads) == 1
    assert host_reads[0].args["path"] == "/work/notes/SALARY.md"


def test_single_namespace_behavior_unchanged(sponsio_root):
    """When only the calling namespace has events, the result is the
    same as before the cross-namespace change.
    """
    _append_event(
        "gh_gist",
        _ev(1, "gh_gist", "mcp__gh_gist__create_gist"),
    )
    events = _load_prior_events("gh_gist")
    assert len(events) == 1
    assert events[0].tool == "mcp__gh_gist__create_gist"


def test_bak_namespace_skipped(sponsio_root):
    """A retired namespace (``.bak`` suffix — Sponsio's convention for
    disabling a contract bucket without losing the files) must not
    contribute trace events.
    """
    bak_dir = sponsio_root / "_gh_gist.curated.bak"
    bak_dir.mkdir()
    (bak_dir / ".shield-trace.jsonl").write_text(
        json.dumps(_ev(0, "gh_gist", "stale_event")) + "\n"
    )
    _append_event(
        "gh_gist",
        _ev(1, "gh_gist", "live_event"),
    )

    events = _load_prior_events("gh_gist")
    tools = [e.tool for e in events]
    assert "stale_event" not in tools
    assert "live_event" in tools


def test_disabled_namespace_skipped(sponsio_root):
    """``.disabled`` suffix — same convention as ``.bak``."""
    disabled = sponsio_root / "telegram.disabled"
    disabled.mkdir()
    (disabled / ".shield-trace.jsonl").write_text(
        json.dumps(_ev(0, "telegram", "send_message")) + "\n"
    )
    _append_event("gh_gist", _ev(1, "gh_gist", "create_gist"))

    events = _load_prior_events("gh_gist")
    tools = [e.tool for e in events]
    assert "send_message" not in tools
    assert "create_gist" in tools


def test_dot_prefixed_dir_skipped(sponsio_root):
    """Dot-prefixed dirs (``.cache``, ``.internal``, etc.) are skipped.
    They aren't contract namespaces and shouldn't pollute the merge.
    """
    cache = sponsio_root / ".cache"
    cache.mkdir()
    (cache / ".shield-trace.jsonl").write_text(
        json.dumps(_ev(99, "_internal", "internal_event")) + "\n"
    )
    _append_event("gh_gist", _ev(1, "gh_gist", "live_event"))

    events = _load_prior_events("gh_gist")
    tools = [e.tool for e in events]
    assert "internal_event" not in tools
    assert "live_event" in tools


def test_conversation_id_buckets_remain_isolated(sponsio_root):
    """Cross-namespace merge MUST NOT leak events across conversations.
    Each conv has its own ``conv-<id>.shield-trace.jsonl`` per namespace;
    the merge unions namespaces *within* a conv, never across.
    """
    _append_event(
        "_host_openclaw",
        _ev(1, "_host_openclaw", "read", path="/x"),
        conversation_id="conv-A",
    )
    _append_event(
        "_host_openclaw",
        _ev(2, "_host_openclaw", "read", path="/y"),
        conversation_id="conv-B",
    )
    _append_event(
        "gh_gist",
        _ev(3, "gh_gist", "create_gist"),
        conversation_id="conv-A",
    )

    events_a = _load_prior_events("gh_gist", conversation_id="conv-A")
    paths_a = [e.args.get("path") for e in events_a if e.args]
    assert "/x" in paths_a
    assert "/y" not in paths_a
    assert any(e.tool == "create_gist" for e in events_a)

    events_b = _load_prior_events("gh_gist", conversation_id="conv-B")
    paths_b = [e.args.get("path") for e in events_b if e.args]
    assert "/y" in paths_b
    assert "/x" not in paths_b
    # No gh_gist events belong to conv-B.
    assert not any(e.tool == "create_gist" for e in events_b)


def test_chronological_merge_across_namespaces(sponsio_root):
    """Events from multiple namespaces are returned sorted by ``ts``."""
    _append_event("_host_openclaw", _ev(5, "_host_openclaw", "later_host"))
    _append_event("gh_gist", _ev(1, "gh_gist", "earlier_gist"))
    _append_event("_host_openclaw", _ev(3, "_host_openclaw", "middle_host"))

    events = _load_prior_events("gh_gist")
    tools = [e.tool for e in events]
    assert tools == ["earlier_gist", "middle_host", "later_host"]


def test_empty_root_returns_empty(sponsio_root):
    """Fresh install — no events anywhere — must return ``[]``."""
    assert _load_prior_events("gh_gist") == []


def test_calling_namespace_first_event(sponsio_root):
    """The very first call into a namespace (no dir yet) still sees
    other namespaces' events.  Regression guard for the ``seen_calling``
    branch in :func:`_load_prior_events`.
    """
    _append_event(
        "_host_openclaw",
        _ev(1, "_host_openclaw", "read", path="/work/notes/x.md"),
    )
    # gh_gist namespace dir hasn't been created yet.
    assert not (sponsio_root / "gh_gist").exists()

    events = _load_prior_events("gh_gist")
    assert len(events) == 1
    assert events[0].tool == "read"


def test_trace_file_path_isolation_by_namespace(sponsio_root):
    """Sanity check: writes still go to per-namespace files (the merge
    happens on read, not write).  Regression guard against accidentally
    flattening the write side.
    """
    _append_event("_host_openclaw", _ev(1, "_host_openclaw", "read"))
    _append_event("gh_gist", _ev(2, "gh_gist", "create_gist"))

    host_path = _trace_file_for("_host_openclaw")
    plugin_path = _trace_file_for("gh_gist")
    assert host_path != plugin_path

    host_lines = host_path.read_text().strip().splitlines()
    plugin_lines = plugin_path.read_text().strip().splitlines()
    assert len(host_lines) == 1
    assert len(plugin_lines) == 1
    assert "read" in host_lines[0]
    assert "create_gist" in plugin_lines[0]
