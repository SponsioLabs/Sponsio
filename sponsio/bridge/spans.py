"""Span tree → view-model pieces.

Pure functions, so the projection can be tested without a guard, a network,
or a console. The span vocabulary is ``sponsio/tracer/semconv.py``; the
target shape is ``console/schema/view-model.ts``.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Enforcement actions the console renders as a step status. Anything else
# falls back to "blocked" rather than inventing a new status word the
# frontend has no colour for.
KNOWN_ACTIONS = ("blocked", "escalated", "redirected", "retrying", "warned", "observed")
# Actions that actually stopped the call. `observed` is not one of them: in
# shadow mode the contract fired and the call still ran.
STOPPING_ACTIONS = ("blocked", "escalated", "redirected")

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slug(text: str) -> str:
    return _SLUG_RE.sub("-", str(text).strip().lower()).strip("-") or "rule"


def args_preview(args: Any, *, limit: int = 160) -> str:
    """One line, truncated.

    Truncation happens before the value leaves the machine, so an argument
    that should never have been sent cannot be reconstructed from what was.
    """
    if args is None:
        return ""
    try:
        text = json.dumps(args, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(args)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def violations_from_turn(
    turn: dict, by_label: dict[str, dict] | None = None
) -> list[dict]:
    """The violations recorded under one agent_turn span.

    The human rule text lives on the guarantee's ``formula_desc``, which can
    differ from the label a rulebook authored — both spellings are indexed
    so a violation still matches its contract either way.
    """
    by_label = by_label or {}
    out: list[dict] = []
    for check in turn.get("children") or []:
        if check.get("span_type") != "sponsio.contract_check":
            continue
        pipeline = "sto" if check.get("pipeline") == "sto" else "det"
        label = check.get("contract_name", "")

        guarantee = violation = enforcement = None
        for child in check.get("children") or []:
            kind = child.get("span_type")
            if kind in ("sponsio.guarantee", "sponsio.precondition"):
                guarantee = child
            elif kind == "sponsio.violation":
                violation = child
            elif kind == "sponsio.enforcement":
                enforcement = child

        if not violation:
            continue
        if guarantee and guarantee.get("formula_desc"):
            label = guarantee["formula_desc"]

        meta = by_label.get(label, {})
        enforcement = enforcement or {}
        entry = {
            "contractId": meta.get("id", slug(label)),
            "contractLabel": meta.get("label", label),
            "category": meta.get("category", "Prohibited"),
            "kind": violation.get("kind", "guarantee"),
            "severity": violation.get("severity", "HIGH"),
            "pipeline": pipeline,
            "enforcement": {
                "strategy": enforcement.get("strategy", "DetBlock"),
                "action": enforcement.get("result_action", "blocked"),
            },
            "evidence": violation.get("evidence", ""),
            "reason": meta.get("reason", label),
        }
        # The safe tool that ran instead — only when there was one.
        if enforcement.get("redirect_to"):
            entry["enforcement"]["redirectTo"] = enforcement["redirect_to"]
        out.append(entry)
    return out


def step_status(turn: dict, violations: list[dict]) -> str:
    """What the console shows for this step.

    Driven by the enforcement action, not by the mode: the same violation is
    ``blocked`` when it stopped the call and ``observed`` when it did not,
    and that difference is the whole point of shadow mode.
    """
    if violations:
        action = violations[0]["enforcement"]["action"]
        return action if action in KNOWN_ACTIONS else "blocked"
    return "blocked" if turn.get("blocked") else "ok"
