"""End-of-session trace-tree view.

This is the post-hoc summary the user sees after their agent finishes
— the v1 CLI mockup form. It walks
:attr:`sponsio.runtime.monitor.RuntimeMonitor.turn_spans` (one root
span per ``check_action`` call, with nested contract checks /
preconditions / guarantees / violations / enforcements) and renders
the canonical Design B1 zones:

  ━━━ Sponsio ━━━ runtime contract enforcement ━━━━━━
    session  sess_xxxx          agent  …            mode  ENFORCE
    tenant   …                  env    …            sdk   …
    contracts armed ──────────────────────────────────
      C1  …                                        READY
      …
    trace ────────────────────────────────────────────
      00.000  ├─ user_instruction "…"              + 0ms     mcp
      00.012  │  └─ ⚙ assume[C1] freeze declared   ✓
      00.012  │     contract C1 → ACTIVE
      …
              ✗ enforce[C1] destructive SQL …  BLOCKED  14µs
  ━━━ VERDICT ━━━ BLOCKED ━━━━━━━━━━━━━━━━━━━━━━━━━━
    headline · N violations · M warnings
    K checks   X% deterministic   N LLM calls
    p50 …  p99 …  max …
    → sponsio explain C1     sponsio replay sess_xxxx

Live event streaming (``runtime.terminal.TerminalReporter``) runs in
parallel during the agent execution; this view fires once at
``finish_session`` to give the spec-mockup summary.
"""

from __future__ import annotations

import os
import statistics
from collections.abc import Iterable
from typing import Any

from rich.console import Console
from rich.text import Text

from sponsio.render.components import (
    assume_line,
    contracts_table,
    cta_line,
    enforce_violation_line,
    event_line,
    header_banner,
    header_meta,
    indent,
    perf_line,
    section_rule,
    state_transition_line,
    verdict_banner,
    verdict_summary,
)
from sponsio.render.derive import (
    args_summary,
    format_latency_ms,
    format_latency_us,
    format_relative_time,
    service_for_tool,
    short_contract_alias,
    short_session_id,
)
from sponsio.render.tokens import PALETTE

# ---------------------------------------------------------------------------
# Verdict aggregation — derive the bottom banner from the span tree.
# ---------------------------------------------------------------------------


def _walk_violations(turn_spans: list) -> tuple[int, int]:
    """Return ``(blocked_count, observed_count)`` across all turn spans."""
    blocked = observed = 0
    for turn in turn_spans:
        for child in turn.children:
            if getattr(child, "span_type", "") != "sponsio.contract_check":
                continue
            for enf in _enforcement_spans(child):
                if enf.result_action == "blocked":
                    blocked += 1
                elif enf.result_action == "observed":
                    observed += 1
    return blocked, observed


def _enforcement_spans(check_span) -> Iterable:
    """Yield every EnforcementSpan reachable under one ContractCheckSpan."""
    for c in check_span.children:
        if getattr(c, "span_type", "") == "sponsio.guarantee":
            for inner in c.children:
                if getattr(inner, "span_type", "") == "sponsio.enforcement":
                    yield inner
        elif getattr(c, "span_type", "") == "sponsio.enforcement":
            yield c


def _verdict_status(blocked: int, observed: int) -> str:
    if blocked > 0:
        return "BLOCKED"
    if observed > 0:
        return "WARN"
    return "PASS"


def _verdict_headline(blocked: int, observed: int, total_actions: int) -> str:
    if blocked > 0:
        return f"{blocked} action{'s' if blocked != 1 else ''} stopped pre-execution"
    if observed > 0:
        return f"{observed} would-have-blocked event{'s' if observed != 1 else ''} in shadow mode"
    if total_actions == 0:
        return "no actions executed"
    return "all observed actions satisfied their contracts"


def _perf_stats(turn_spans: list) -> tuple[int, int, list[float]]:
    """Return ``(total_checks, sto_count, check_latencies_us)``."""
    total = 0
    sto = 0
    lat_us: list[float] = []
    for turn in turn_spans:
        for child in turn.children:
            if getattr(child, "span_type", "") != "sponsio.contract_check":
                continue
            total += 1
            if getattr(child, "pipeline", "hard") == "sto":
                sto += 1
            d = getattr(child, "duration_ms", None)
            if d is not None:
                lat_us.append(d * 1000.0)
    return total, sto, lat_us


def _format_us(value: float | None) -> str:
    if value is None:
        return "—"
    return format_latency_us(value)


# ---------------------------------------------------------------------------
# Tree rendering — one tool call (AgentTurnSpan) → tree node + nested checks.
# ---------------------------------------------------------------------------


def _contract_alias_for(
    check_name: str, contracts: list, alias_map: dict[str, str]
) -> str:
    """Resolve a check.contract_name back to its C1/C2 alias.

    The aggregator gives us contract names in their declaration order,
    so any contract not present at session start (rare — usually means
    a runtime mismatch) gets a stable hash-based fallback.
    """
    if check_name in alias_map:
        return alias_map[check_name]
    # Fallback: keep something stable + scannable.
    return f"C?{abs(hash(check_name)) % 100:02d}"


def _render_turn(
    turn,
    *,
    session_start: float,
    is_last: bool,
    contracts: list,
    alias_map: dict[str, str],
    activated_contracts: set[str],
) -> list[Text]:
    """Render one tool-call AgentTurnSpan + its nested checks as Text rows.

    ``activated_contracts`` is mutated in place — the renderer adds an
    alias the first time we emit its ``contract Cn → ACTIVE`` line, so
    subsequent turns don't repeat the activation announcement (each
    contract activates exactly once per session).
    """
    rows: list[Text] = []
    branch = "└─" if is_last else "├─"
    timestamp = format_relative_time(session_start, turn.start_time)
    latency = format_latency_ms(turn.duration_ms or 0)
    args = args_summary(getattr(turn, "attributes", {}).get("args"))
    service = service_for_tool(turn.action)
    rows.append(
        event_line(
            timestamp=timestamp,
            tool=turn.action or "<unknown>",
            args=args,
            latency=latency,
            service=service,
            branch=branch,
        )
    )

    # Walk contract checks. We split the rendering into two passes:
    #   pass 1: assumes (with state transitions) — they explain *why*
    #           a contract just became ACTIVE.
    #   pass 2: violations — the ✗ enforce lines, indented at the same
    #           level as turn but without a tree branch (cross-cutting).
    violation_rows: list[Text] = []
    for check in turn.children:
        if getattr(check, "span_type", "") != "sponsio.contract_check":
            continue
        alias = _contract_alias_for(check.contract_name, contracts, alias_map)
        for child in check.children:
            kind = getattr(child, "span_type", "")
            if kind == "sponsio.precondition":
                # Only announce a satisfied assumption the *first* time
                # the contract becomes ACTIVE; subsequent turns re-evaluate
                # the assumption but the contract was already armed.
                if child.result and alias not in activated_contracts:
                    activated_contracts.add(alias)
                    rows.append(
                        assume_line(
                            contract_alias=alias,
                            summary=child.formula_desc,
                            latency=_check_latency_str(check),
                        )
                    )
                    rows.append(state_transition_line(alias, "ACTIVE"))
            elif kind == "sponsio.guarantee" and not child.result:
                # Walk for the actual EnforcementSpan to get the verdict word.
                status = "BLOCKED"
                for inner in child.children:
                    if getattr(inner, "span_type", "") == "sponsio.enforcement":
                        status = inner.result_action.upper()
                        break
                violation_rows.append(
                    enforce_violation_line(
                        contract_alias=alias,
                        summary=child.formula_desc,
                        status=status,
                        latency=_check_latency_str(check),
                    )
                )
    rows.extend(violation_rows)
    return rows


def _check_latency_str(check) -> str:
    d = getattr(check, "duration_ms", None)
    if d is None:
        return ""
    return format_latency_us(d * 1000)


# ---------------------------------------------------------------------------
# Top-level render.
# ---------------------------------------------------------------------------


def render_session(
    *,
    console: Console,
    agent_id: str,
    mode: str,
    contracts: list,
    turn_spans: list,
    session_id: str | None = None,
    tenant: str | None = None,
    env: str | None = None,
    sdk: str | None = None,
    ctas: list[str] | None = None,
    include_replay_cta: bool = True,
) -> None:
    """Render the end-of-session view to ``console``.

    Args:
        console:    Rich Console (typically pinned to stderr).
        agent_id:   The :attr:`BaseGuard.agent_id` value.
        mode:       ``"enforce"`` or ``"observe"`` — drives the header
                    metadata grid's mode cell.
        contracts:  ``BaseGuard._system._contracts`` so we can list
                    them in the "contracts armed" section + resolve
                    aliases for the trace tree.
        turn_spans: ``RuntimeMonitor.turn_spans`` — the structured
                    span tree the verifier emits.
        session_id: Display ID. If omitted, a stable short ID is
                    derived from ``agent_id`` + the process start time
                    so multiple runs of the same agent collide rarely.
        tenant / env / sdk: Optional metadata-grid fields. Render as
                    ``"—"`` when not provided.
        ctas:       Lines for the CTA footer. Defaults to
                    ``sponsio explain <first-violator>`` +
                    ``sponsio replay <session_id>``.
        include_replay_cta: Whether to include the
                    ``sponsio replay <session_id>`` line in the
                    auto-generated CTAs.  Set to ``False`` when the
                    session is ephemeral (e.g. ``sponsio demo``,
                    test fixtures) — the replay command would
                    error with "no session matched" because no
                    log file was persisted.  No effect when ``ctas``
                    is supplied explicitly.
    """
    if session_id is None:
        session_id = short_session_id(f"{agent_id}-{os.getpid()}")
    sdk_label = sdk or _detect_sdk()
    blocked, observed = _walk_violations(turn_spans)
    total_checks, sto_count, lat_us = _perf_stats(turn_spans)
    status = _verdict_status(blocked, observed)
    headline = _verdict_headline(blocked, observed, len(turn_spans))

    # 1. Top banner.
    console.print(header_banner())
    console.print()

    # 2. Header metadata grid.
    pairs: list[tuple[str, str]] = [
        ("session", session_id),
        ("agent", agent_id),
        ("mode", mode.upper()),
        ("tenant", tenant or "—"),
        ("env", env or _env_default()),
        ("sdk", sdk_label),
    ]
    console.print(indent(header_meta(pairs)))
    console.print()

    # 3. Contracts armed list.
    alias_map: dict[str, str] = {}
    if contracts:
        rows: list[tuple[str, str, str]] = []
        for i, c in enumerate(contracts):
            alias = short_contract_alias(_contract_label(c), i)
            label = _contract_label(c) or "(unnamed)"
            alias_map[label] = alias
            is_bare = not (getattr(c, "assumptions", []) or [])
            row_status = "ACTIVE" if is_bare else "READY"
            rows.append((alias, label, row_status))
        console.print(indent(section_rule("contracts armed")))
        console.print(indent(contracts_table(rows)))
        console.print()

    # 4. Trace tree.
    if turn_spans:
        console.print(indent(section_rule("trace")))
        # Use the first turn's start_time as t=0.
        session_start = turn_spans[0].start_time
        # Bare contracts (no assumption) are ACTIVE from step 0; pre-seed
        # the dedup set so we don't announce activation for them.
        activated_contracts: set[str] = {
            short_contract_alias(_contract_label(c), i)
            for i, c in enumerate(contracts)
            if not (getattr(c, "assumptions", []) or [])
        }
        for i, turn in enumerate(turn_spans):
            is_last = i == len(turn_spans) - 1
            for row in _render_turn(
                turn,
                session_start=session_start,
                is_last=is_last,
                contracts=contracts,
                alias_map=alias_map,
                activated_contracts=activated_contracts,
            ):
                console.print(indent(row))
        console.print()

    # 5. Verdict banner + summary.
    console.print(verdict_banner(status))
    console.print()
    console.print(
        indent(
            verdict_summary(
                headline,
                violations=blocked,
                warnings=observed,
            )
        )
    )
    console.print()

    # 6. Perf summary.
    det_pct = ((total_checks - sto_count) / total_checks * 100) if total_checks else 100
    console.print(
        indent(
            perf_line(
                total_checks=total_checks,
                deterministic_pct=det_pct,
                llm_calls=sto_count,
            )
        )
    )
    if lat_us:
        p50 = _format_us(statistics.median(lat_us))
        p99 = _format_us(_quantile(lat_us, 0.99))
        max_ = _format_us(max(lat_us))
        console.print(indent(perf_line_latency(p50=p50, p99=p99, max_=max_)))
    console.print()

    # 7. CTA footer.
    if ctas is None:
        ctas = _default_ctas(
            turn_spans, alias_map, session_id, include_replay=include_replay_cta
        )
    if ctas:
        console.print(cta_line(ctas))


def perf_line_latency(
    *, p50: str, p99: str, max_: str, qps_human: str | None = None
) -> Text:
    """Latency-only perf line: ``p50  Nµs  p99  Mµs  max  Pµs``."""
    parts: list[tuple[str, str]] = [
        ("p50  ", PALETTE["metadata"]),
        (p50, PALETTE["fg"]),
        ("   p99  ", PALETTE["metadata"]),
        (p99, PALETTE["fg"]),
        ("   max  ", PALETTE["metadata"]),
        (max_, PALETTE["fg"]),
    ]
    if qps_human:
        parts.extend([("   ", ""), (qps_human, f"bold {PALETTE['brand']}")])
    return Text.assemble(*parts)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return s[k]


def _contract_label(c: Any) -> str:
    """Mirror sponsio.render.monitor._contract_label."""
    desc = getattr(c, "desc", None)
    if desc:
        return str(desc)
    # ``enforcements`` was renamed to ``guarantees`` in the schema-
    # rename migration (E: → G:); the old attribute no longer exists
    # on Contract, so this fallback was silently returning empty
    # strings for every contract that lacked an explicit ``desc:``
    # before this fix.
    guarantees = getattr(c, "guarantees", []) or []
    if guarantees:
        return str(getattr(guarantees[0], "desc", "") or "")
    return getattr(getattr(c, "agent", None), "id", "") or ""


def _detect_sdk() -> str:
    """Best-effort detection of which framework SDK is loaded.

    Avoids importing the SDKs ourselves — only checks
    ``sys.modules`` so we don't drag in heavy libraries just to
    label the banner. Returns ``"—"`` when nothing matches.
    """
    import sys

    candidates = (
        ("openai", "openai"),
        ("anthropic", "anthropic"),
        ("langgraph", "langgraph"),
        ("crewai", "crewai"),
        ("google.genai", "gemini"),
        ("vercel_ai_sdk", "vercel-ai"),
        ("agents", "openai-agents"),
    )
    for mod, label in candidates:
        if mod in sys.modules:
            try:
                version = getattr(sys.modules[mod], "__version__", "")
                return f"{label}@{version}" if version else label
            except Exception:
                return label
    return "—"


def _env_default() -> str:
    return os.environ.get("SPONSIO_ENV", "—")


def _default_ctas(
    turn_spans: list,
    alias_map: dict[str, str],
    session_id: str,
    *,
    include_replay: bool = True,
) -> list[str]:
    out: list[str] = []
    # First contract that blocked — surface it for `sponsio explain`.
    for turn in turn_spans:
        for check in turn.children:
            if getattr(check, "span_type", "") != "sponsio.contract_check":
                continue
            for child in check.children:
                if (
                    getattr(child, "span_type", "") == "sponsio.guarantee"
                    and not child.result
                ):
                    alias = alias_map.get(check.contract_name)
                    if alias:
                        out.append(f"sponsio explain {alias}")
                        if include_replay:
                            out.append(f"sponsio replay {session_id}")
                        return out
    if include_replay:
        out.append(f"sponsio replay {session_id}")
    return out
