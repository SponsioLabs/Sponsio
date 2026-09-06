"""What a deployment declines to send, and what that costs it.

The claim these tests defend is the one a regulated customer will ask
about directly: turning redaction on changes what the console can show
and changes nothing about what the runtime enforces. Enforcement is
local and deterministic, so the two are genuinely independent, but
"genuinely independent" is a property, and properties get tests.
"""

from __future__ import annotations

import json

import pytest

from sponsio.bridge import privacy

ARGS = {"amount": 250.0, "to": "ACC-9911", "memo": "patient MRN-88213", "urgent": True}


def test_full_is_the_default_and_passes_the_existing_preview_through():
    """Nobody's payload changes until they ask for it to."""
    assert privacy.resolve(None) == "full"
    assert (
        privacy.preview(ARGS, "full", full_preview="already truncated")
        == "already truncated"
    )


def test_shape_keeps_the_keys_and_drops_every_value():
    out = privacy.shape(ARGS)
    assert "amount" in out and "memo" in out
    assert "250" not in out and "MRN-88213" not in out and "ACC-9911" not in out


def test_shape_keeps_string_length_because_length_is_a_debugging_fact():
    """An oversize-argument rule fires on length, and "the model passed
    40 000 characters" is the finding, not the 40 000 characters."""
    assert "<str:8>" in privacy.shape({"to": "ACC-9911"})


def test_hashed_is_stable_across_key_order():
    """Duplicate and loop detection read "same arguments"; they must not
    read "same arguments" differently because a dict was built in another
    order."""
    a = privacy.digest({"x": 1, "y": 2})
    b = privacy.digest({"y": 2, "x": 1})
    assert a == b and a.startswith("sha256:")


def test_hashed_still_hides_the_content():
    assert "MRN-88213" not in privacy.digest(ARGS)


def test_metadata_sends_no_arguments_and_no_model_text():
    assert privacy.preview(ARGS, "metadata", full_preview="anything") == ""
    assert privacy.say("the patient's balance is $412", "metadata") == ""


def test_an_unrecognised_level_fails_closed_to_the_strictest(monkeypatch):
    """A typo in a deployment that meant to tighten must not silently
    send everything. It gets the strictest level there is, not merely a
    strict one."""
    monkeypatch.setenv("SPONSIO_PRIVACY", "redacted-ish")
    assert privacy.resolve("full") == privacy.STRICTEST == "tool_calls"


def test_the_environment_beats_the_argument(monkeypatch):
    """The person who decides what leaves a machine operates it; they do
    not necessarily control the code running on it."""
    monkeypatch.setenv("SPONSIO_PRIVACY", "hashed")
    assert privacy.resolve("full") == "hashed"


def test_metadata_keeps_the_verdict_and_drops_the_claim_content():
    """A MISMATCH is metadata. The value it was checked against and the
    corrected sentence are the model's output and the customer's data."""
    rows = [
        {
            "predicate": "balance",
            "verdict": "MISMATCH",
            "evidence": "authoritative: 412.00",
            "fix": "Your balance is $412.00, Dana.",
        }
    ]
    (kept,) = privacy.claims(rows, "metadata")
    assert kept["verdict"] == "MISMATCH" and kept["predicate"] == "balance"
    assert kept["evidence"] == "" and kept["fix"] == ""
    assert privacy.claims(rows, "hashed") == rows


def test_tool_calls_keeps_only_the_action_lane():
    assert privacy.keeps_step("tool_call", "tool_calls") is True
    assert privacy.keeps_step("assistant_output", "tool_calls") is False
    assert privacy.keeps_step("delegation", "tool_calls") is False
    assert privacy.keeps_step("assistant_output", "metadata") is True


def test_the_run_says_what_it_is_not_sending():
    """Otherwise an empty argument field is ambiguous: no arguments, or
    arguments withheld?"""
    stamp = privacy.describe("shape")
    assert stamp["sendsArguments"] is False
    assert stamp["sendsArgumentShapes"] is True
    assert stamp["level"] == "shape"


@pytest.mark.parametrize("level", privacy.LEVELS)
def test_enforcement_is_identical_at_every_level(level, monkeypatch, capsys, tmp_path):
    """The point of the whole module. The check runs before anything is
    sent, so what is sent cannot change the verdict."""
    import sponsio
    from sponsio.bridge import session as bridge

    monkeypatch.setenv("SPONSIO_PRIVACY", level)

    class Capture:
        configured = True

        def __init__(self):
            self.frames = []

        def ingest_session(self, project, payload):
            self.frames.append(payload)

    cap = Capture()
    guard = sponsio.Sponsio(
        agent_id="a",
        contracts=["tool `check_policy` must precede `issue_refund`"],
        mode="enforce",
        verbose=False,
    )
    session = bridge.attach(
        guard, client=cap, session_id=f"p-{level}", runs_dir=tmp_path
    )
    verdicts = [
        guard.guard_before("lookup", {"id": "A-1"}).allowed,
        guard.guard_before(
            "issue_refund", {"amount": 250.0, "memo": "MRN-88213"}
        ).allowed,
    ]
    session.finish()
    capsys.readouterr()

    assert verdicts == [True, False], "the contract decided differently under redaction"
    payload = json.dumps(cap.frames[-1])
    if level != "full":
        assert "MRN-88213" not in payload


# ---------------------------------------------------------------------------
# The output lane, end to end, and the one contradiction that is refused
# ---------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

SECRET_VALUE = "412.00"
SECRET_FIX = "Your balance is $412.00, Dana."


def _verified_claim():
    res = SimpleNamespace(
        verdict="MISMATCH",
        correction=SECRET_FIX,
        values=[SECRET_VALUE],
        action="rewrite",
        source="table:balances",
    )
    vc = SimpleNamespace(
        result=res, spec=SimpleNamespace(predicate="balance"), span=(0, 10)
    )
    return SimpleNamespace(evidence_claims=[vc])


def _run(level, monkeypatch, tmp_path):
    import sponsio
    from sponsio.bridge import session as bridge

    monkeypatch.setenv("SPONSIO_PRIVACY", level)

    class Capture:
        configured = True

        def __init__(self):
            self.frames = []

        def ingest_session(self, project, payload):
            self.frames.append(payload)

    cap = Capture()
    guard = sponsio.Sponsio(
        agent_id="a", contracts=["tool `x` at most 9 times"], verbose=False
    )
    s = bridge.attach(guard, client=cap, session_id=f"lane-{level}", runs_dir=tmp_path)
    guard.guard_before("lookup", {"mrn": "MRN-88213"})
    s.note("a", "delegating patient MRN-88213", type="delegation")
    s.record_output("The balance is " + SECRET_VALUE, _verified_claim())
    s.finish()
    return cap.frames[-1]["vm"], json.dumps(cap.frames[-1])


def test_metadata_still_records_the_model_turn_without_its_content(
    monkeypatch, tmp_path, capsys
):
    vm, raw = _run("metadata", monkeypatch, tmp_path)
    capsys.readouterr()
    assert "assistant_output" in [st["type"] for st in vm["steps"]]
    assert SECRET_VALUE not in raw and SECRET_FIX not in raw


def test_tool_calls_sends_the_action_lane_and_nothing_else(
    monkeypatch, tmp_path, capsys
):
    """The level a customer means by "only send us the tool calls"."""
    vm, raw = _run("tool_calls", monkeypatch, tmp_path)
    capsys.readouterr()
    assert [st["type"] for st in vm["steps"]] == ["tool_call"]
    assert vm["steps"][0]["tool"] == "lookup"
    assert "MRN-88213" not in raw and SECRET_VALUE not in raw and SECRET_FIX not in raw
    assert vm["privacy"]["sendsOutputLane"] is False


def test_tool_calls_refuses_an_evidence_configuration(monkeypatch, capsys):
    """Verifying a claim means sending it. A deployment cannot have both,
    and it should hear so at construction rather than on the first turn."""
    import sponsio
    from sponsio.bridge.privacy import PrivacyConflict

    monkeypatch.setenv("SPONSIO_PRIVACY", "tool_calls")
    with pytest.raises(PrivacyConflict):
        sponsio.Sponsio(
            agent_id="a",
            contracts=["tool `x` at most 9 times"],
            verbose=False,
            evidence={"claims": [{"predicate": "balance", "claim_field": "balance"}]},
        )
    capsys.readouterr()


def test_the_same_refusal_at_attach_when_the_level_comes_by_argument(
    monkeypatch, tmp_path, capsys
):
    """The environment gate cannot see attach(privacy=...); attach checks
    again so the argument path cannot slip past."""
    import sponsio
    from sponsio.bridge import session as bridge
    from sponsio.bridge.privacy import PrivacyConflict

    monkeypatch.delenv("SPONSIO_PRIVACY", raising=False)
    guard = sponsio.Sponsio(
        agent_id="a",
        contracts=["tool `x` at most 9 times"],
        verbose=False,
        evidence={"claims": [{"predicate": "balance", "claim_field": "balance"}]},
    )
    capsys.readouterr()
    with pytest.raises(PrivacyConflict):
        bridge.attach(
            guard,
            client=SimpleNamespace(configured=False),
            privacy="tool_calls",
            runs_dir=tmp_path,
        )
