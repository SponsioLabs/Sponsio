"""Tests for the in-process monitor rendering layer.

Covers the dispatch in ``render_event`` (verbosity gating, dedup,
det / sto / observe / blocked branches) and the activation banner
shape. We assert on plain-text content + the presence of the right
PALETTE color in the styled output, not byte-for-byte ANSI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console

from sponsio.render.monitor import (
    build_label_map,
    render_assume_satisfied,
    render_assume_unsatisfied,
    render_banner,
    render_event,
    render_observed,
    render_pass,
    render_sto_retry,
    render_violation,
)


# ---------------------------------------------------------------------------
# Test fixtures — minimal stand-ins for MonitorEvent / Contract.
# ---------------------------------------------------------------------------


@dataclass
class _Result:
    action: str
    message: str = ""
    retry_prompt: str | None = None


@dataclass
class _Sto:
    score: float | None = None


@dataclass
class _Event:
    agent_id: str = "bot"
    action: str = "do_thing"
    pipeline: str = "det"
    constraint_name: str = "rule"
    result: _Result = field(default_factory=lambda: _Result(action="allow"))
    sto_result: Any = None


@dataclass
class _Assumption:
    desc: str


@dataclass
class _Enforcement:
    desc: str


@dataclass
class _Contract:
    desc: str | None = None
    assumptions: list[_Assumption] = field(default_factory=list)
    enforcements: list[_Enforcement] = field(default_factory=list)
    agent: Any = None


def _ansi(text):
    """Render a Rich Text via a force-truecolor Console for color assertions."""
    console = Console(
        record=True, width=120, force_terminal=True, color_system="truecolor"
    )
    console.print(text)
    return console.export_text(styles=True)


def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


# ---------------------------------------------------------------------------
# Per-renderer functions — exact symbols + colors per case.
# ---------------------------------------------------------------------------


def test_violation_uses_violation_color_and_fail_symbol():
    text = render_violation("no destructive SQL", "execute_sql")
    rendered = _ansi(text)
    assert "✗" in rendered
    assert "BLOCKED" in rendered
    assert "execute_sql" in rendered
    # PALETTE['violation'] = #FCA5A5 → 38;2;252;165;165
    assert "38;2;252;165;165" in rendered


def test_observed_uses_warning_color_and_warn_symbol():
    text = render_observed("pii_blocklist", "send_email")
    rendered = _ansi(text)
    assert "⚠" in rendered
    assert "WARN" in rendered
    assert "(observe)" in _strip_ansi(rendered)
    # PALETTE['warning'] = #FCD34D → 38;2;252;211;77
    assert "38;2;252;211;77" in rendered


def test_assume_satisfied_includes_contract_label_when_provided():
    text = render_assume_satisfied("freeze declared", contract_label="code_freeze")
    plain = _strip_ansi(_ansi(text))
    assert "assume" in plain
    assert "freeze declared" in plain
    assert "READY" in plain
    assert "contract code_freeze" in plain
    assert "ACTIVE" in plain


def test_assume_satisfied_omits_contract_label_when_unmapped():
    text = render_assume_satisfied("anonymous", contract_label=None)
    plain = _strip_ansi(_ansi(text))
    assert "READY" in plain
    assert "ACTIVE" not in plain  # don't claim activation we can't name


def test_assume_unsatisfied_uses_metadata_color():
    text = render_assume_unsatisfied("not yet")
    rendered = _ansi(text)
    plain = _strip_ansi(rendered)
    assert "not yet satisfied" in plain
    # PALETTE['metadata'] = #64748B → 38;2;100;116;139
    assert "38;2;100;116;139" in rendered


def test_pass_renders_check_symbol():
    text = render_pass("rate_limit", "safe_action", pipeline="det")
    plain = _strip_ansi(_ansi(text))
    assert "✓" in plain
    assert "PASS" in plain


def test_sto_retry_includes_score_when_present():
    text = render_sto_retry("tone_polite", "respond", score=0.42)
    plain = _strip_ansi(_ansi(text))
    assert "score 0.42" in plain
    assert "retrying with feedback" in plain


def test_sto_retry_omits_score_when_absent():
    text = render_sto_retry("tone_polite", "respond", score=None)
    plain = _strip_ansi(_ansi(text))
    assert "score" not in plain


# ---------------------------------------------------------------------------
# Dispatch — render_event verbosity gating + dedup.
# ---------------------------------------------------------------------------


def test_render_event_verbosity_0_hides_pass():
    e = _Event(constraint_name="rule_x", result=_Result(action="allow"), pipeline="det")
    assert render_event(e, verbosity=0) == []


def test_render_event_verbosity_0_shows_blocked():
    e = _Event(
        action="bad",
        constraint_name="rule_x",
        result=_Result(action="blocked"),
        pipeline="det",
    )
    out = render_event(e, verbosity=0)
    assert len(out) == 1
    assert "BLOCKED" in _strip_ansi(_ansi(out[0]))


def test_render_event_v1_hides_pass_lines():
    e = _Event(constraint_name="rule_x", result=_Result(action="allow"))
    assert render_event(e, verbosity=1) == []


def test_render_event_v2_shows_pass_lines():
    e = _Event(constraint_name="rule_x", result=_Result(action="allow"))
    out = render_event(e, verbosity=2)
    assert len(out) == 1
    assert "PASS" in _strip_ansi(_ansi(out[0]))


def test_render_event_dedup_satisfied_assumptions():
    """An assumption fires once at v=1; subsequent satisfactions stay silent."""
    seen: set[str] = set()
    e = _Event(
        constraint_name="assumption: freeze declared",
        result=_Result(action="allow"),
        pipeline="det",
    )
    first = render_event(e, verbosity=1, seen_satisfied=seen)
    assert len(first) == 1
    assert "READY" in _strip_ansi(_ansi(first[0]))

    second = render_event(e, verbosity=1, seen_satisfied=seen)
    assert second == []  # silent on repeat


def test_render_event_unsatisfied_assumption_hidden_at_v1():
    """An unsatisfied assumption ('escalated') is just dormancy — not noise."""
    e = _Event(
        constraint_name="assumption: freeze declared",
        result=_Result(action="escalated"),
    )
    assert render_event(e, verbosity=1) == []


def test_render_event_unsatisfied_assumption_visible_at_v2():
    e = _Event(
        constraint_name="assumption: freeze declared",
        result=_Result(action="escalated"),
    )
    out = render_event(e, verbosity=2)
    assert len(out) == 1
    assert "not yet satisfied" in _strip_ansi(_ansi(out[0]))


def test_render_event_observed_triggers_warn_branch():
    e = _Event(
        constraint_name="pii", action="send_email", result=_Result(action="observed")
    )
    out = render_event(e, verbosity=1)
    assert len(out) == 1
    assert "WARN" in _strip_ansi(_ansi(out[0]))


def test_render_event_sto_violation_uses_score():
    e = _Event(
        pipeline="sto",
        action="reply",
        constraint_name="tone",
        result=_Result(action="retrying"),
        sto_result=_Sto(score=0.31),
    )
    out = render_event(e, verbosity=1)
    assert len(out) == 1
    plain = _strip_ansi(_ansi(out[0]))
    assert "score 0.31" in plain
    assert "retrying with feedback" in plain


def test_render_event_uses_label_map_for_assume_satisfied():
    seen: set[str] = set()
    e = _Event(
        constraint_name="assumption: freeze declared",
        result=_Result(action="allow"),
    )
    label_map = {"freeze declared": "code_freeze"}
    out = render_event(
        e, verbosity=1, contract_label_map=label_map, seen_satisfied=seen
    )
    assert "code_freeze" in _strip_ansi(_ansi(out[0]))
    assert "ACTIVE" in _strip_ansi(_ansi(out[0]))


# ---------------------------------------------------------------------------
# Banner + label map.
# ---------------------------------------------------------------------------


def test_build_label_map_extracts_assumption_to_contract():
    contracts = [
        _Contract(
            desc="code_freeze",
            assumptions=[_Assumption(desc="freeze declared")],
        ),
        _Contract(
            desc="prod_readonly",
            assumptions=[_Assumption(desc="connected to prod")],
        ),
        # Bare contract — no assumptions; should not appear in the map.
        _Contract(desc="universal_safety"),
    ]
    out = build_label_map(contracts)
    assert out == {
        "freeze declared": "code_freeze",
        "connected to prod": "prod_readonly",
    }


def test_render_banner_emits_zone_structure():
    """All four zones present: top rule + section + table + trailing blank."""
    contracts = [
        _Contract(
            desc="code_freeze", assumptions=[_Assumption(desc="freeze declared")]
        ),
        _Contract(desc="universal_safety"),  # bare → ACTIVE from start
    ]
    console = Console(
        record=True, width=100, force_terminal=True, color_system="truecolor"
    )
    render_banner(contracts, console=console)
    plain = _strip_ansi(console.export_text())
    assert "Sponsio" in plain
    assert "contracts armed (2)" in plain
    assert "code_freeze" in plain
    assert "READY" in plain  # the conditional contract
    assert "ACTIVE" in plain  # the bare contract


def test_render_banner_empty_contracts_is_noop():
    console = Console(record=True, width=100, force_terminal=True)
    render_banner([], console=console)
    assert console.export_text() == ""
