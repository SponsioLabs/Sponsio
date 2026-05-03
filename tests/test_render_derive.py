"""Tests for view-layer field derivation.

Domain model stays minimal; the renderer infers `service` / `latency` /
`args_summary` etc. from existing fields. These helpers must be
deterministic so snapshot tests over rendered output are stable.
"""

from __future__ import annotations

from sponsio.render.derive import (
    args_summary,
    format_latency_ms,
    format_latency_us,
    format_relative_time,
    has_known_service,
    relative_time,
    service_for_tool,
    short_contract_alias,
    short_session_id,
)


# ---------------------------------------------------------------------------
# service_for_tool — transport-only inference (func / mcp / shell / http).
# Resource and business-domain axes were removed; postgres / fs / etc.
# now collapse into the default ``func`` transport label.
# ---------------------------------------------------------------------------


def test_service_for_tool_shell_prefixes():
    assert service_for_tool("bash") == "shell"
    assert service_for_tool("run_tests") == "shell"
    assert service_for_tool("execute_command") == "shell"


def test_service_for_tool_mcp_double_underscore():
    """MCP host plugins use the ``mcp__server__tool`` flat layout."""
    assert service_for_tool("mcp__github__create_issue") == "mcp"
    assert service_for_tool("user_instruction") == "mcp"


def test_service_for_tool_http_prefixes():
    assert service_for_tool("fetch") == "http"
    assert service_for_tool("web_fetch") == "http"
    assert service_for_tool("web_search") == "http"


def test_service_for_tool_defaults_to_func():
    """Anything not matching shell/mcp/http is in-process function call —
    the modal SDK behaviour."""
    assert service_for_tool("execute_sql") == "func"
    assert service_for_tool("read_file") == "func"
    assert service_for_tool("update_vendor_bank_account") == "func"
    assert service_for_tool("totally_made_up") == "func"


def test_service_for_tool_handles_none():
    assert service_for_tool(None) == "unknown"
    assert service_for_tool("") == "unknown"


def test_has_known_service_returns_true_for_transport_labels():
    """All four transports (func/shell/mcp/http) are colored brands."""
    assert has_known_service("bash") is True
    assert has_known_service("execute_sql") is True  # → func
    assert has_known_service("mcp__github__list") is True
    assert has_known_service(None) is False  # unknown


# ---------------------------------------------------------------------------
# args_summary — flatten args dict into a single line.
# ---------------------------------------------------------------------------


def test_args_summary_dict_renders_key_value_pairs():
    out = args_summary({"db": "prod", "limit": 10})
    assert "db=prod" in out
    assert "limit=10" in out


def test_args_summary_truncates_long_values():
    out = args_summary({"q": "x" * 200}, max_len=20)
    assert len(out) <= len("q=") + 20  # value capped to max_len
    assert out.endswith("…")


def test_args_summary_strips_newlines_in_values():
    out = args_summary({"prompt": "line1\nline2\nline3"})
    assert "\n" not in out
    assert "line1 line2 line3" in out


def test_args_summary_handles_str_arg():
    out = args_summary("a quick string")
    assert out == '"a quick string"'


def test_args_summary_handles_none():
    assert args_summary(None) == ""


def test_args_summary_handles_list():
    out = args_summary(["foo", "bar"])
    assert out == "foo, bar"


# ---------------------------------------------------------------------------
# Relative time formatting.
# ---------------------------------------------------------------------------


def test_relative_time_within_first_second():
    seconds, millis = relative_time(1000.0, 1000.380)
    assert seconds == 0
    assert millis == 380


def test_relative_time_clamps_negative_skew():
    """A clock that ticks backward should not produce negative output."""
    seconds, millis = relative_time(1000.0, 999.0)
    assert seconds == 0
    assert millis == 0


def test_format_relative_time_under_100_seconds():
    assert format_relative_time(1000.0, 1000.000) == "00.000"
    assert format_relative_time(1000.0, 1000.380) == "00.380"
    assert format_relative_time(1000.0, 1099.999) == "99.999"


def test_format_relative_time_over_100_seconds_drops_to_minute_seconds():
    assert format_relative_time(1000.0, 1120.5) == "002:00"


def test_format_relative_time_always_six_chars():
    """Column alignment depends on width consistency."""
    for delta in (0, 0.012, 0.380, 5.123, 99.999, 120.5):
        assert len(format_relative_time(1000.0, 1000 + delta)) == 6


# ---------------------------------------------------------------------------
# Latency formatting.
# ---------------------------------------------------------------------------


def test_format_latency_ms_basic():
    assert format_latency_ms(70) == "+70ms"
    assert format_latency_ms(0) == "+0ms"


def test_format_latency_ms_sub_millisecond_renders_as_us():
    """Sub-ms turns shouldn't all read as +0ms in the trace tree."""
    assert format_latency_ms(0.5) == "+500µs"
    assert format_latency_ms(0.155) == "+155µs"


def test_format_latency_ms_none():
    assert format_latency_ms(None) == ""


def test_format_latency_us_under_millisecond():
    assert format_latency_us(14) == "14µs"


def test_format_latency_us_promotes_to_ms():
    """Sub-ms checks read as µs; >1ms upgrade to ms with a decimal."""
    assert format_latency_us(2500) == "2.5ms"


# ---------------------------------------------------------------------------
# Short identifiers.
# ---------------------------------------------------------------------------


def test_short_session_id_is_stable():
    """Same input → same hash; needed for replay/explain cross-references."""
    a = short_session_id("20260501_120000_999")
    b = short_session_id("20260501_120000_999")
    assert a == b
    assert a.startswith("sess_")
    assert len(a) == len("sess_") + 8


def test_short_session_id_differs_per_input():
    a = short_session_id("20260501_120000_999")
    b = short_session_id("20260501_130000_888")
    assert a != b


def test_short_contract_alias_is_one_indexed():
    assert short_contract_alias("any_name", 0) == "C1"
    assert short_contract_alias("any_name", 9) == "C10"


def test_short_contract_alias_accepts_custom_prefix():
    assert short_contract_alias("any_name", 0, prefix="#") == "#1"
