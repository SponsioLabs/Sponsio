"""A redirect verdict, pinned. The TS twin asserts the same numbers.

The two runtimes disagreed about every `redirect_to_safe` verdict and no
test noticed, because `scenarios.json` carries inline NL contracts and
`redirect_to_safe` has no plain-English form, so it was never in there.

What each field means, and why asserting only `blocked` was not enough:

* `blocked` is a hard refusal. A redirect is not one.
* `allowed` follows `blocked`, because the agent flow continues down the
  safe path.
* `stop_original` is the one an adapter branches on. It is true for both
  a block and a redirect: the original call must not run either way.
* `redirected_to` names the substitute, so an adapter that can dispatch
  it does not have to parse a message to find out what to call.

`expected.json` is the shared answer key: this file writes nothing, and
the TS test reads the same numbers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import sponsio

HERE = Path(__file__).parent
EXPECTED = json.loads((HERE / "redirect_expected.json").read_text())


@pytest.fixture
def guard():
    return sponsio.Sponsio(
        config=str(HERE / "redirect_parity.yaml"),
        agent_id="bot",
        mode="enforce",
        verbose=False,
    )


@pytest.mark.parametrize("case", EXPECTED, ids=[c["tool"] for c in EXPECTED])
def test_the_verdict_matches_the_shared_answer_key(guard, case) -> None:
    result = guard.guard_before(case["tool"], {})
    got = {
        "blocked": result.blocked,
        "allowed": result.allowed,
        "redirected": result.redirected,
        "redirectedTo": result.redirected_to,
        "stopOriginal": result.stop_original,
    }
    assert got == case["expect"], (
        f"{case['tool']}: {case['why']}\n  expected {case['expect']}\n  got      {got}"
    )
