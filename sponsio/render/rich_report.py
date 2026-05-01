"""Rich renderer for ``sponsio report`` output.

Takes a :class:`~sponsio.reporting.aggregator.Report` and produces the
Design B1 layout: header banner → metadata grid → contracts table →
verdict banner → perf summary → CTA footer.

This is the terminal-oriented path. The pre-existing markdown / html /
json renderers in ``sponsio.reporting.renderer`` are kept as-is for
file / pipe / Slack-paste use cases.
"""

from __future__ import annotations

from datetime import datetime, timezone

from rich.console import Console
from rich.text import Text

from sponsio.render.components import (
    contract_stats_table,
    cta_line,
    header_banner,
    header_meta,
    indent,
    perf_line,
    section_rule,
    verdict_banner,
    verdict_summary,
)
from sponsio.render.derive import short_contract_alias
from sponsio.render.tokens import PALETTE, SVG_THEME
from sponsio.reporting.aggregator import Report

_MAX_CONTRACT_ROWS = 15


def _verdict_status(report: Report) -> str:
    """Map report counts to a verdict word for the bottom banner."""
    if report.blocked > 0:
        return "BLOCKED"
    if report.observed > 0 or report.retrying > 0:
        return "WARN"
    return "PASS"


def _verdict_headline(report: Report, status: str) -> str:
    if status == "BLOCKED":
        return f"{report.blocked} actions stopped pre-execution"
    if status == "WARN":
        return f"{report.observed + report.retrying} would-have-blocked events in shadow mode"
    if report.total_events == 0:
        return "no events in window"
    return "all observed events satisfied their contracts"


def _format_window(start: float, end: float) -> str:
    if start == 0.0 and end == 0.0:
        return "—"
    fmt = "%Y-%m-%d %H:%M"
    s = datetime.fromtimestamp(start, tz=timezone.utc).strftime(fmt)
    e = datetime.fromtimestamp(end, tz=timezone.utc).strftime(fmt)
    return f"{s} → {e} UTC"


def render_report(
    report: Report,
    *,
    console: Console | None = None,
    cta: list[str] | None = None,
) -> Console:
    """Render ``report`` to a Rich console (creating one if needed).

    Returns the console so callers can chain ``.save_svg(...)`` for the
    ``--save-svg`` flag without holding a separate reference.
    """
    if console is None:
        console = Console(record=True)

    # 1. Header banner.
    console.print(header_banner())
    console.print()

    # 2. Header metadata grid — three pairs per row.
    agents_label = ", ".join(report.agents) if report.agents else "—"
    pairs: list[tuple[str, str]] = [
        ("agents", agents_label[:32]),
        ("sessions", str(report.total_sessions)),
        ("events", str(report.total_events)),
        ("window", _format_window(report.window_start, report.window_end)),
        ("pass_rate", f"{report.pass_rate * 100:.0f}%"),
        ("violations", str(report.violations)),
    ]
    console.print(indent(header_meta(pairs)))
    console.print()

    # 3. Contracts table — only emitted if there's at least one contract
    # with activity. Empty contract list is the "all clean" case and
    # gets covered by the verdict banner alone.
    if report.by_contract:
        # Cap the table at the top N most-violating contracts. With 30+
        # contracts the table is unreadable and dwarfs the verdict zone;
        # users wanting the full dump can use --format=markdown / json.
        top = sorted(report.by_contract, key=lambda c: c.violations, reverse=True)[
            :_MAX_CONTRACT_ROWS
        ]
        truncated = max(0, len(report.by_contract) - len(top))
        title = "contracts with activity"
        if truncated:
            title = f"{title} — top {len(top)} of {len(report.by_contract)}"
        console.print(indent(section_rule(title)))
        rows = [
            (
                short_contract_alias(c.constraint, i),
                c.constraint,
                c.pipeline,
                c.blocked,
                c.observed,
                c.retrying,
                _trim(c.sample_message, 60),
            )
            for i, c in enumerate(top)
        ]
        console.print(indent(contract_stats_table(rows)))
        if truncated:
            console.print(
                indent(
                    Text(
                        f"… and {truncated} more — see `sponsio report --format=markdown` for full list",
                        style=PALETTE["metadata"],
                    )
                )
            )
        console.print()

    # 4. Verdict banner + summary.
    status = _verdict_status(report)
    console.print(verdict_banner(status))
    console.print()
    headline = _verdict_headline(report, status)
    console.print(
        indent(
            verdict_summary(
                headline,
                violations=report.violations,
                warnings=report.observed + report.retrying,
            )
        )
    )
    console.print()

    # 5. Perf summary — det vs sto split. Latency stats aren't tracked
    # by the report aggregator yet; show the deterministic-share figure
    # that *is* meaningful here.
    det_count = sum(c.violations for c in report.by_contract if c.pipeline == "det")
    sto_count = sum(c.violations for c in report.by_contract if c.pipeline == "sto")
    total_checks = max(report.total_events, 1)
    det_pct = (
        (det_count / max(report.violations, 1)) * 100 if report.violations else 100
    )
    console.print(
        indent(
            perf_line(
                total_checks=total_checks,
                deterministic_pct=det_pct,
                llm_calls=sto_count,
            )
        )
    )
    console.print()

    # 6. CTA footer.
    ctas = cta or _default_ctas(report)
    if ctas:
        console.print(cta_line(ctas))

    return console


def save_svg(console: Console, path: str, title: str) -> None:
    """Wrap ``console.save_svg`` so callers don't need to know the theme."""
    console.save_svg(path, title=title)
    # Note: Rich's save_svg uses its own theme; SVG_THEME is exposed as
    # a hook for future customisation (Rich's API takes Theme in
    # newer versions, not our dict — keeping this layer of indirection
    # so the call site doesn't need to be touched again).
    _ = SVG_THEME


def _default_ctas(report: Report) -> list[str]:
    out: list[str] = []
    if report.blocked > 0 and report.by_contract:
        first = next(
            (c.constraint for c in report.by_contract if c.blocked > 0),
            None,
        )
        if first:
            out.append(f"sponsio explain {first}")
    out.append("sponsio host trace --follow")
    return out


def _trim(s: str | None, max_len: int) -> str:
    if not s:
        return ""
    s = s.replace("\n", " ").strip()
    return s if len(s) <= max_len else s[: max_len - 1] + "…"
