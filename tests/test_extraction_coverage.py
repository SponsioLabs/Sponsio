"""Coverage ledger for the evidence extractor (evals/extraction_coverage).

Pins the extractor's current blind spots so they are documented, so a
regression can't quietly add one, and so an improvement (fence-stripping,
nested lookup, field aliases, a free-text extractor) forces a conscious
update to this list rather than passing unnoticed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.extraction_coverage import run_eval  # noqa: E402

# The claim-present-but-not-surfaced shapes as of today. Each is a way a
# real model can state a configured claim and have it go UNVERIFIED.
# Shrinking this set is a coverage improvement — update it deliberately.
_KNOWN_BLIND_SPOTS = {
    "JSON in ```json fence",
    "JSON with prose prefix",
    "JSON array, not object",
    "nested under a key",
    "tool args as JSON string",
    "prose only",
    "field-name variant",
}


def test_extractor_surfaces_the_clean_structured_shapes():
    rows = {r["shape"]: r for r in run_eval()["rows"]}
    assert rows["clean JSON object"]["surfaced"]
    assert rows["tool_call args dict"]["surfaced"]


def test_blind_spots_match_the_ledger():
    blind = set(run_eval()["blind_spots"])
    assert blind == _KNOWN_BLIND_SPOTS, (
        "extractor coverage changed — update _KNOWN_BLIND_SPOTS. "
        f"added={blind - _KNOWN_BLIND_SPOTS} removed={_KNOWN_BLIND_SPOTS - blind}"
    )
