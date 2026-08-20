"""Extraction-coverage probe for the evidence middleware (client side).

WHY THIS EXISTS
---------------
The verdict logic is provably correct on the claims it adjudicates
(cloud-side tier-1 eval scores 100%). But none of that fires if a claim
never enters the pipeline. The extractor only sees claims that arrive as
(a) JSON-*object* message content, or (b) tool-call argument dicts;
everything else is structurally invisible. Coverage — not comparator
accuracy — is the real error surface of the deterministic tiers.

WHAT THIS MEASURES (honestly)
-----------------------------
Not a population coverage %, which would need a labelled model-output
corpus. Instead a SHAPE MATRIX: one logical claim
(weekday=Friday, date=2000-01-01) expressed in ten output shapes a model
actually produces, run through the REAL ``structured_sources`` +
``extract_claims``. Each row is a deterministic fact about the extractor;
the dropped rows are its quantified blind spots, each mapping to a
concrete fix (lenient JSON / fence-stripping / nested lookup / free-text
extractor / field aliases).

Run:   python -m evals.extraction_coverage
Guard: tests/test_extraction_coverage.py
"""

from __future__ import annotations

from sponsio.integrations.evidence_middleware import (
    EvidenceConfig,
    extract_claims,
    structured_sources,
)

# The claim we want surfaced, however the model chose to phrase the output.
_CONFIG = EvidenceConfig.from_value(
    {
        "client": None,  # constructed, never called — extraction needs no network
        "claims": [
            {
                "predicate": "date_weekday_agreement",
                "claim_field": "weekday",
                "inputs": {"date": {"field": "date", "source": "user_utterance"}},
            }
        ],
    }
)

_JSON = '{"weekday": "Friday", "date": "2000-01-01"}'

# (label, message content, tool_call_args, why-it-matters)
_SHAPES = [
    ("clean JSON object", _JSON, None, "the happy path"),
    (
        "JSON in ```json fence",
        f"```json\n{_JSON}\n```",
        None,
        "models fence JSON constantly",
    ),
    (
        "JSON with prose prefix",
        f"Here is the result: {_JSON}",
        None,
        "chat models prepend prose",
    ),
    ("JSON array, not object", f"[{_JSON}]", None, "list wrapper -> not a dict"),
    (
        "nested under a key",
        '{"result": {"weekday": "Friday", "date": "2000-01-01"}}',
        None,
        "claim one level deep",
    ),
    (
        "tool_call args dict",
        None,
        [{"weekday": "Friday", "date": "2000-01-01"}],
        "structured tool call",
    ),
    ("tool args as JSON string", None, [_JSON], "arg serialized, not a dict"),
    ("prose only", "The weekday is Friday.", None, "free text — no structured field"),
    (
        "field-name variant",
        '{"day_of_week": "Friday", "date": "2000-01-01"}',
        None,
        "model named the field differently",
    ),
    (
        "field present but null",
        '{"weekday": null, "date": "2000-01-01"}',
        None,
        "field emitted, value missing",
    ),
]


def run_eval() -> dict:
    rows = []
    for label, content, tool_args, note in _SHAPES:
        sources = structured_sources(content, tool_args)
        claims = extract_claims(_CONFIG, sources)
        surfaced = len(claims) > 0
        value = claims[0].value if surfaced else None
        rows.append(
            {"shape": label, "surfaced": surfaced, "value": value, "note": note}
        )
    surfaced = sum(r["surfaced"] for r in rows)
    return {
        "total": len(rows),
        "surfaced": surfaced,
        "blind_spots": [r["shape"] for r in rows if not r["surfaced"]],
        "rows": rows,
    }


def format_report(results: dict) -> str:
    lines = ["=== Extraction coverage — output-shape matrix ==="]
    s, t = results["surfaced"], results["total"]
    lines.append(
        f"one claim, {t} realistic output shapes: surfaced {s}, blind {t - s}\n"
    )
    for r in results["rows"]:
        mark = "surfaced" if r["surfaced"] else "DROPPED "
        val = f"  -> value={r['value']!r}" if r["surfaced"] else ""
        lines.append(f"  [{mark}] {r['shape']:24} — {r['note']}{val}")
    lines.append("\nblind spots (claim present in the output but not surfaced):")
    for b in results["blind_spots"]:
        lines.append(f"  · {b}")
    lines.append(
        "\nreading: surfaced rows are what today's extractor covers; every blind"
        "\nspot is a way a real model can state the claim and have it go"
        "\nUNVERIFIED. Fixes map 1:1 — lenient JSON / fence-strip / nested lookup"
        "\n/ field aliases / a free-text extractor (the deferred text mode)."
    )
    return "\n".join(lines)


if __name__ == "__main__":
    print(format_report(run_eval()))
