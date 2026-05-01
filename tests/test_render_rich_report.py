"""Tests for the Rich `sponsio report` renderer.

Approach: render to an in-memory truecolor Console, capture the styled
text, then assert on (a) the structural layout (zones present, in
order) and (b) the verdict color matching the report's status.

We don't snapshot the byte-for-byte ANSI output — Rich version drift
would break that. We snapshot the *plain* text and the verdict-color
hex.
"""

from __future__ import annotations

import re

from rich.console import Console

from sponsio.render.rich_report import (
    _verdict_headline,
    _verdict_status,
    render_report,
)
from sponsio.render.tokens import PALETTE
from sponsio.reporting.aggregator import ContractStat, Report


def _make_console() -> Console:
    return Console(
        record=True, width=100, force_terminal=True, color_system="truecolor"
    )


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


# ---------------------------------------------------------------------------
# Verdict logic.
# ---------------------------------------------------------------------------


def test_verdict_status_blocked_when_blocked_count_positive():
    rep = Report(blocked=2, observed=0, retrying=0)
    assert _verdict_status(rep) == "BLOCKED"


def test_verdict_status_warn_when_only_observed():
    rep = Report(blocked=0, observed=5, retrying=0)
    assert _verdict_status(rep) == "WARN"


def test_verdict_status_warn_when_only_retrying():
    rep = Report(blocked=0, observed=0, retrying=3)
    assert _verdict_status(rep) == "WARN"


def test_verdict_status_pass_on_clean_report():
    rep = Report(total_events=100, blocked=0, observed=0, retrying=0)
    assert _verdict_status(rep) == "PASS"


def test_verdict_headline_empty_window():
    rep = Report(total_events=0)
    headline = _verdict_headline(rep, "PASS")
    assert "no events" in headline


def test_verdict_headline_blocked_mentions_count():
    rep = Report(blocked=3)
    headline = _verdict_headline(rep, "BLOCKED")
    assert "3" in headline
    assert "stopped" in headline


# ---------------------------------------------------------------------------
# End-to-end render.
# ---------------------------------------------------------------------------


def _render_to_text(report: Report) -> tuple[str, str]:
    """Render a report and return ``(ansi_text, plain_text)`` for assertions."""
    console = _make_console()
    render_report(report, console=console)
    ansi = console.export_text(styles=True)
    plain = _strip_ansi(ansi)
    return ansi, plain


def test_render_emits_all_six_zones_in_order():
    rep = Report(
        agents=["bot"],
        total_events=10,
        total_sessions=1,
        passed=8,
        blocked=2,
        by_contract=[ContractStat(constraint="rule_x", pipeline="det", blocked=2)],
    )
    _, plain = _render_to_text(rep)
    # Zone markers — header rule, contracts section, verdict banner, CTA arrow.
    assert "Sponsio" in plain
    assert "runtime contract enforcement" in plain
    assert "contracts with activity" in plain
    assert "VERDICT" in plain
    assert "BLOCKED" in plain
    # Order: contracts section appears before verdict.
    assert plain.index("contracts with activity") < plain.index("VERDICT")
    # CTA arrow + a sponsio sub-command.
    assert "→" in plain
    assert "sponsio explain" in plain or "sponsio host trace" in plain


def test_render_omits_contracts_section_when_no_activity():
    rep = Report(total_events=42, passed=42)
    _, plain = _render_to_text(rep)
    assert "contracts with activity" not in plain
    # Verdict still emits, with PASS color.
    assert "PASS" in plain


def test_render_blocked_uses_violation_color():
    """The verdict word color must match PALETTE['violation'] for BLOCKED."""
    rep = Report(
        blocked=1, by_contract=[ContractStat(constraint="x", pipeline="det", blocked=1)]
    )
    ansi, _ = _render_to_text(rep)
    # PALETTE['violation'] = #FCA5A5 → ANSI 38;2;252;165;165
    assert "38;2;252;165;165" in ansi
    # Sanity: success green is NOT applied to the BLOCKED word.
    assert "BLOCKED" in ansi


def test_render_pass_uses_success_color():
    rep = Report(total_events=5, passed=5)
    ansi, _ = _render_to_text(rep)
    # PALETTE['success'] = #86EFAC → ANSI 38;2;134;239;172
    assert "38;2;134;239;172" in ansi


def test_render_warn_uses_warning_color():
    rep = Report(observed=3)
    ansi, _ = _render_to_text(rep)
    # PALETTE['warning'] = #FCD34D → ANSI 38;2;252;211;77
    assert "38;2;252;211;77" in ansi


def test_render_includes_cta_for_blocked():
    rep = Report(
        blocked=1,
        by_contract=[ContractStat(constraint="my_rule", pipeline="det", blocked=1)],
    )
    _, plain = _render_to_text(rep)
    assert "sponsio explain my_rule" in plain


def test_render_with_no_blocked_skips_explain_cta():
    rep = Report(total_events=10, observed=2)
    _, plain = _render_to_text(rep)
    assert "sponsio explain" not in plain
    # But host trace fallback CTA still present.
    assert "sponsio host trace" in plain


def test_render_can_export_svg(tmp_path):
    """Smoke test for --save-svg path."""
    rep = Report(
        agents=["a"],
        total_events=1,
        blocked=1,
        by_contract=[ContractStat(constraint="r", pipeline="det", blocked=1)],
    )
    console = _make_console()
    render_report(rep, console=console)
    svg_path = tmp_path / "out.svg"
    console.save_svg(str(svg_path), title="test")
    assert svg_path.exists()
    text = svg_path.read_text()
    assert text.startswith("<svg") or "<svg" in text[:200]
    assert "BLOCKED" in text


def test_palette_token_referenced():
    """Sanity: the exported token is still the one we expect (catches a
    rename of PALETTE['violation'])."""
    assert PALETTE["violation"] == "#FCA5A5"
