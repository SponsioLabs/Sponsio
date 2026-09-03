"""Where the mode comes from, pinned.

QUICKSTART said "explicit ctor arg > env var", which is backwards. The
real order is `SPONSIO_MODE` > ctor arg > yaml > observe, and the
asymmetry is deliberate: whoever runs the deployment has to be able to
flip enforcement without waiting for a release.

Backwards in that direction is the dangerous one. It tells a reader their
code wins, so a team with `SPONSIO_MODE=enforce` in the container reads
`mode="observe"` in the source and believes they are shadowing.
"""

from __future__ import annotations

import pytest

import sponsio
from sponsio import contract

RULE = [
    contract("gate")
    .assume("called `issue_refund`")
    .guarantees("must call `check_policy` before `issue_refund`")
]


def _blocked(**kwargs) -> bool:
    guard = sponsio.Sponsio(agent_id="b", verbose=False, contracts=RULE, **kwargs)
    return guard.guard_before("issue_refund", {}).stop_original


@pytest.mark.parametrize(
    "env,ctor,expect_stop",
    [
        ("enforce", "observe", True),
        ("observe", "enforce", False),
        (None, "enforce", True),
        (None, "observe", False),
    ],
)
def test_the_env_var_outranks_the_ctor_arg(monkeypatch, env, ctor, expect_stop) -> None:
    if env is None:
        monkeypatch.delenv("SPONSIO_MODE", raising=False)
    else:
        monkeypatch.setenv("SPONSIO_MODE", env)
    assert _blocked(mode=ctor) is expect_stop


def test_dashboard_goes_the_other_way(monkeypatch) -> None:
    """The asymmetry is the point, so the other side needs a test too:
    `dashboard` is a deploy-time choice made in code, not an operator's
    mid-incident flip, and its ctor arg wins."""
    monkeypatch.setenv("SPONSIO_DASHBOARD", "http://127.0.0.1:9999")
    guard = sponsio.Sponsio(
        agent_id="b", verbose=False, contracts=RULE, dashboard=False
    )
    assert getattr(guard, "_dashboard_url", None) is None


def test_quickstart_states_the_real_order() -> None:
    """The doc and the loader have already drifted once."""
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "QUICKSTART.md").read_text()
    assert "`SPONSIO_MODE` > ctor arg > yaml" in text, (
        "QUICKSTART no longer states the mode precedence the loader implements"
    )
    assert "explicit ctor arg > env var" not in text, (
        "QUICKSTART is back to claiming the ctor arg outranks the env var"
    )
