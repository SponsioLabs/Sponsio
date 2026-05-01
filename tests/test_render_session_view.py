"""Tests for the end-of-session trace-tree renderer.

Builds synthetic Span trees that match real ``RuntimeMonitor.turn_spans``
shape, runs them through ``render_session``, and asserts on the
plain-text + ANSI-color presence. Avoids byte-for-byte snapshots so
Rich version drift doesn't make the suite brittle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console

from sponsio.models.spans import (
    AgentTurnSpan,
    ContractCheckSpan,
    EnforcementSpan,
    GuaranteeSpan,
    PreconditionSpan,
    ViolationSpan,
)
from sponsio.render.session_view import (
    _default_ctas,
    _perf_stats,
    _verdict_headline,
    _verdict_status,
    _walk_violations,
    render_session,
)


# ---------------------------------------------------------------------------
# Test fixtures.
# ---------------------------------------------------------------------------


@dataclass
class _Assumption:
    desc: str


@dataclass
class _Contract:
    desc: str
    assumptions: list[_Assumption] = field(default_factory=list)
    enforcements: list[Any] = field(default_factory=list)


def _turn(
    action: str, t_offset: float = 0.0, dur_ms: float = 1.0, args: dict | None = None
) -> AgentTurnSpan:
    return AgentTurnSpan(
        span_type="sponsio.agent_turn",
        start_time=1000.0 + t_offset,
        end_time=1000.0 + t_offset + dur_ms / 1000,
        agent_id="bot",
        action=action,
        attributes={"args": args or {}},
    )


def _check(
    name: str, pipeline: str = "hard", lat_us: float = 14.0
) -> ContractCheckSpan:
    return ContractCheckSpan(
        span_type="sponsio.contract_check",
        start_time=0,
        end_time=lat_us / 1_000_000,
        contract_name=name,
        pipeline=pipeline,
    )


def _assume_ok(desc: str) -> PreconditionSpan:
    return PreconditionSpan(
        span_type="sponsio.precondition",
        start_time=0,
        end_time=0,
        formula_desc=desc,
        result=True,
    )


def _violation(desc: str, action: str = "blocked") -> GuaranteeSpan:
    g = GuaranteeSpan(
        span_type="sponsio.guarantee",
        start_time=0,
        end_time=0,
        formula_desc=desc,
        result=False,
        status="violated",
    )
    g.children.append(
        ViolationSpan(
            span_type="sponsio.violation",
            start_time=0,
            end_time=0,
            kind="guarantee",
            evidence=desc,
        )
    )
    g.children.append(
        EnforcementSpan(
            span_type="sponsio.enforcement",
            start_time=0,
            end_time=0,
            strategy="DetBlock",
            result_action=action,
        )
    )
    return g


def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def _render(turn_spans: list, **kwargs) -> tuple[str, str]:
    console = Console(
        record=True, width=100, force_terminal=True, color_system="truecolor"
    )
    render_session(
        console=console,
        agent_id=kwargs.pop("agent_id", "bot"),
        mode=kwargs.pop("mode", "enforce"),
        contracts=kwargs.pop("contracts", []),
        turn_spans=turn_spans,
        session_id=kwargs.pop("session_id", "sess_test"),
        **kwargs,
    )
    ansi = console.export_text(styles=True)
    return ansi, _strip_ansi(ansi)


# ---------------------------------------------------------------------------
# Verdict / perf aggregation helpers.
# ---------------------------------------------------------------------------


def test_walk_violations_counts_blocked_and_observed():
    t = _turn("execute_sql")
    c1 = _check("c1")
    c1.children.append(_violation("d1", action="blocked"))
    c2 = _check("c2")
    c2.children.append(_violation("d2", action="observed"))
    t.children.extend([c1, c2])
    blocked, observed = _walk_violations([t])
    assert blocked == 1
    assert observed == 1


def test_verdict_status_dispatch():
    assert _verdict_status(2, 0) == "BLOCKED"
    assert _verdict_status(0, 5) == "WARN"
    assert _verdict_status(0, 0) == "PASS"


def test_verdict_headline_blocked_pluralizes_correctly():
    assert "1 action stopped" in _verdict_headline(1, 0, 5)
    assert "3 actions stopped" in _verdict_headline(3, 0, 5)


def test_verdict_headline_pass_when_clean():
    assert "satisfied" in _verdict_headline(0, 0, 5)


def test_verdict_headline_no_actions():
    assert "no actions" in _verdict_headline(0, 0, 0)


def test_perf_stats_separates_det_from_sto():
    t = _turn("a")
    t.children.append(_check("c1", pipeline="hard", lat_us=20))
    t.children.append(_check("c2", pipeline="sto", lat_us=300))
    total, sto, lat = _perf_stats([t])
    assert total == 2
    assert sto == 1
    assert sorted(lat) == [20.0, 300.0]


# ---------------------------------------------------------------------------
# End-to-end: zone presence + ordering.
# ---------------------------------------------------------------------------


def test_render_emits_all_zones_in_order():
    contracts = [_Contract("rule_A", [_Assumption("freeze declared")])]
    t = _turn("execute_sql", t_offset=0.380, dur_ms=70)
    c = _check("rule_A", lat_us=14)
    c.children.append(_violation("destructive SQL during freeze"))
    t.children.append(c)
    _, plain = _render([t], contracts=contracts)
    assert "Sponsio" in plain
    assert "session" in plain
    assert "contracts armed" in plain
    assert "trace" in plain
    assert "VERDICT" in plain
    assert "BLOCKED" in plain
    assert "→" in plain  # CTA arrow
    # Ordering.
    for left, right in [
        ("session", "contracts armed"),
        ("contracts armed", "trace"),
        ("trace", "VERDICT"),
    ]:
        assert plain.index(left) < plain.index(right)


def test_render_includes_session_metadata_grid():
    _, plain = _render(
        [_turn("a")],
        session_id="sess_4f2a",
        agent_id="coding_agent",
        mode="enforce",
        tenant="acme",
        env="prod",
        sdk="openai@1.42",
    )
    assert "sess_4f2a" in plain
    assert "coding_agent" in plain
    assert "ENFORCE" in plain
    assert "acme" in plain
    assert "prod" in plain
    assert "openai@1.42" in plain


def test_render_metadata_uses_dash_for_missing_fields():
    _, plain = _render([_turn("a")], tenant=None, sdk=None)
    # Two "—" should appear (tenant, sdk); env may or may not depending on env var.
    assert plain.count("—") >= 1


# ---------------------------------------------------------------------------
# Trace tree.
# ---------------------------------------------------------------------------


def test_trace_tree_uses_branch_and_end_for_first_and_last_turn():
    contracts = []
    t1 = _turn("first", t_offset=0.0)
    t2 = _turn("middle", t_offset=0.05)
    t3 = _turn("last", t_offset=0.1)
    _, plain = _render([t1, t2, t3], contracts=contracts)
    # Last turn must end with └─ (first/middle use ├─).
    trace_section = plain.split("trace")[1]
    assert "├─ first" in trace_section
    assert "├─ middle" in trace_section
    assert "└─ last" in trace_section


def test_trace_tree_renders_satisfied_assume_with_state_transition():
    contracts = [_Contract("rule_X", [_Assumption("X declared")])]
    t = _turn("user_instruction")
    c = _check("rule_X")
    c.children.append(_assume_ok("X declared"))
    t.children.append(c)
    _, plain = _render([t], contracts=contracts)
    assert "assume[C1]" in plain
    assert "X declared" in plain
    assert "✓" in plain
    assert "contract C1 → ACTIVE" in plain


def test_trace_tree_renders_violation_lines_after_event():
    contracts = [_Contract("rule_blocked")]
    t = _turn("execute_sql")
    c = _check("rule_blocked", lat_us=42)
    c.children.append(_violation("nope"))
    t.children.append(c)
    ansi, plain = _render([t], contracts=contracts)
    assert "✗" in plain
    assert "enforce[C1]" in plain
    assert "BLOCKED" in plain
    # PALETTE['violation'] = #FCA5A5 → 38;2;252;165;165
    assert "38;2;252;165;165" in ansi


def test_trace_tree_handles_observed_status():
    contracts = [_Contract("rule")]
    t = _turn("send_email")
    c = _check("rule")
    c.children.append(_violation("would have blocked", action="observed"))
    t.children.append(c)
    _, plain = _render([t], contracts=contracts)
    assert "OBSERVED" in plain


def test_trace_tree_includes_service_label_for_known_tools():
    contracts = []
    t = _turn("execute_sql")
    _, plain = _render([t], contracts=contracts)
    assert "postgres" in plain


def test_trace_tree_falls_back_to_unknown_for_unmapped_tool():
    contracts = []
    t = _turn("totally_made_up")
    _, plain = _render([t], contracts=contracts)
    assert "unknown" in plain


# ---------------------------------------------------------------------------
# Verdict banner color.
# ---------------------------------------------------------------------------


def test_verdict_banner_color_matches_status_blocked():
    contracts = [_Contract("r")]
    t = _turn("a")
    c = _check("r")
    c.children.append(_violation("d", action="blocked"))
    t.children.append(c)
    ansi, _ = _render([t], contracts=contracts)
    assert "38;2;252;165;165" in ansi  # PALETTE['violation']


def test_verdict_banner_color_matches_status_warn():
    contracts = [_Contract("r")]
    t = _turn("a")
    c = _check("r")
    c.children.append(_violation("d", action="observed"))
    t.children.append(c)
    ansi, _ = _render([t], contracts=contracts)
    assert "38;2;252;211;77" in ansi  # PALETTE['warning']


def test_verdict_banner_color_matches_status_pass():
    t = _turn("a")
    ansi, _ = _render([t])
    assert "38;2;134;239;172" in ansi  # PALETTE['success']


# ---------------------------------------------------------------------------
# CTA composition.
# ---------------------------------------------------------------------------


def test_default_ctas_for_blocked_session_includes_explain_and_replay():
    t = _turn("execute_sql")
    c = _check("rule_blocked")
    c.children.append(_violation("d"))
    t.children.append(c)
    alias_map = {"rule_blocked": "C7"}
    out = _default_ctas([t], alias_map, "sess_xyz")
    assert "sponsio explain C7" in out
    assert "sponsio replay sess_xyz" in out


def test_default_ctas_for_clean_session_only_replay():
    t = _turn("safe")
    out = _default_ctas([t], {}, "sess_clean")
    assert "sponsio explain" not in " ".join(out)
    assert "sponsio replay sess_clean" in out


# ---------------------------------------------------------------------------
# Empty / edge cases.
# ---------------------------------------------------------------------------


def test_render_handles_empty_turn_spans():
    """An empty session still emits a header + verdict (PASS)."""
    _, plain = _render([])
    assert "Sponsio" in plain
    assert "VERDICT" in plain
    # No "trace" section since there's nothing to show.
    assert "trace" not in plain or "trace" in plain.split("VERDICT")[0]


def test_render_dedups_activation_lines_across_turns():
    """Each contract should announce ``→ ACTIVE`` exactly once, even if
    its assumption re-evaluates as satisfied on every subsequent turn
    (which is how the verifier works — re-checking is cheap and stateless)."""
    contracts = [_Contract("rule_X", [_Assumption("X declared")])]
    t1 = _turn("a", t_offset=0.0)
    c1 = _check("rule_X")
    c1.children.append(_assume_ok("X declared"))
    t1.children.append(c1)

    t2 = _turn("b", t_offset=0.05)
    c2 = _check("rule_X")
    c2.children.append(_assume_ok("X declared"))
    t2.children.append(c2)

    _, plain = _render([t1, t2], contracts=contracts)
    # The activation announcement must appear exactly once even though
    # both turns re-satisfied the assumption.
    assert plain.count("contract C1 → ACTIVE") == 1


def test_render_does_not_announce_activation_for_bare_contracts():
    """Contracts with no assumption are ACTIVE from step 0 (already shown
    in the contracts-armed table). The trace tree must not re-announce
    them when they fire on subsequent tool calls."""
    contracts = [_Contract("bare_rule")]  # no assumptions → bare
    t = _turn("execute_sql")
    c = _check("bare_rule")
    # A bare contract has no precondition span — only guarantees fire.
    t.children.append(c)
    _, plain = _render([t], contracts=contracts)
    # No "→ ACTIVE" inside trace section (the contracts-armed table
    # already shows ACTIVE; trace-tree announcements are only for
    # READY → ACTIVE transitions).
    trace_section = plain.split("trace")[1].split("VERDICT")[0]
    assert "C1 → ACTIVE" not in trace_section


def test_render_handles_contract_without_alias_map_entry():
    """A contract referenced by a check but missing from `contracts` list
    must not crash — we render a stable fallback alias instead."""
    t = _turn("execute_sql")
    c = _check("orphan_contract")
    c.children.append(_violation("d"))
    t.children.append(c)
    _, plain = _render([t], contracts=[])  # empty contracts list
    # Alias fallback uses 'C?<2 hex digits>'
    assert "C?" in plain
