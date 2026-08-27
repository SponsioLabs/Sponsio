"""Streaming a guarded run into a console.

The bridge is telemetry, and telemetry has one hard rule: it must never
change what the agent does. Most of what is pinned below is that rule seen
from different angles — a dead network, a failing upload, a broken recorder.

The projection tests use real span-tree shapes rather than invented ones,
because the whole module exists to read spans the runtime emits.
"""

from __future__ import annotations

import json

import pytest

from sponsio.bridge import attach
from sponsio.bridge.spans import args_preview, step_status, violations_from_turn


def _turn(
    *,
    blocked=False,
    checked=1,
    action="blocked",
    strategy="DetBlock",
    label="no destructive shell",
    redirect=None,
    agent="quant",
):
    enforcement = {
        "span_type": "sponsio.enforcement",
        "strategy": strategy,
        "result_action": action,
    }
    if redirect:
        enforcement["redirect_to"] = redirect
    return {
        "agent_id": agent,
        "blocked": blocked,
        "total_contracts_checked": checked,
        "duration_ms": 1.25,
        "children": [
            {
                "span_type": "sponsio.contract_check",
                "contract_name": "C1",
                "children": [
                    {"span_type": "sponsio.guarantee", "formula_desc": label},
                    {
                        "span_type": "sponsio.violation",
                        "kind": "guarantee",
                        "severity": "HIGH",
                        "evidence": "command=rm -rf /",
                    },
                    enforcement,
                ],
            }
        ],
    }


def _clean_turn(agent="quant"):
    return {
        "agent_id": agent,
        "blocked": False,
        "total_contracts_checked": 2,
        "duration_ms": 0.4,
        "children": [],
    }


class FakeSpan:
    def __init__(self, payload):
        self._payload = payload

    def to_dict(self):
        return self._payload


class FakeGuard:
    """Just enough guard: a mode, an id, and a last check span."""

    def __init__(self, mode="enforce", agent_id="quant"):
        self.mode = mode
        self.agent_id = agent_id
        self.last_check_span = None
        self.calls: list[tuple] = []

    def guard_before(self, tool, args=None, *rest, **kwargs):
        self.calls.append((tool, args))
        return {"allowed": True}

    def export_trace(self):
        return {"events": [{"tool": t, "args": a} for t, a in self.calls]}


class FakeClient:
    def __init__(self, *, configured=True, fail=False):
        self.configured = configured
        self.fail = fail
        self.sent: list[dict] = []

    def ingest_session(self, project, payload):
        if self.fail:
            raise RuntimeError("connection refused")
        self.sent.append(payload)
        return {"ok": True}


# -- projection ------------------------------------------------------------


def test_violation_carries_contract_and_enforcement():
    rows = violations_from_turn(_turn())
    assert len(rows) == 1
    row = rows[0]
    assert row["contractLabel"] == "no destructive shell"
    assert row["severity"] == "HIGH"
    assert row["enforcement"] == {"strategy": "DetBlock", "action": "blocked"}
    assert row["evidence"] == "command=rm -rf /"


def test_redirect_target_is_recorded_only_when_there_was_one():
    plain = violations_from_turn(_turn())[0]
    assert "redirectTo" not in plain["enforcement"]

    redirected = violations_from_turn(
        _turn(action="redirected", strategy="RedirectToSafe", redirect="query_public")
    )[0]
    assert redirected["enforcement"]["redirectTo"] == "query_public"


def test_a_check_without_a_violation_is_not_a_violation():
    turn = _turn()
    turn["children"][0]["children"] = [
        {"span_type": "sponsio.guarantee", "formula_desc": "no destructive shell"}
    ]
    assert violations_from_turn(turn) == []


def test_status_follows_the_action_not_the_mode():
    """The same violation is `blocked` when it stopped the call and
    `observed` when it did not. That difference is what shadow mode is."""
    assert step_status(_turn(), violations_from_turn(_turn())) == "blocked"

    observed = _turn(action="observed")
    assert step_status(observed, violations_from_turn(observed)) == "observed"


def test_an_unknown_action_does_not_invent_a_status():
    """The console has no colour for a word it has never seen."""
    weird = _turn(action="quarantined")
    assert step_status(weird, violations_from_turn(weird)) == "blocked"


def test_args_are_truncated_before_they_leave_the_machine():
    preview = args_preview({"blob": "x" * 500})
    assert len(preview) <= 160
    assert preview.endswith("…")


def test_args_preview_survives_unserializable_values():
    assert args_preview({"f": object()})


# -- recording -------------------------------------------------------------


def test_auto_attach_records_every_guarded_call(tmp_path):
    guard, client = FakeGuard(), FakeClient()
    run = attach(guard, client=client, runs_dir=tmp_path, auto=True)

    guard.last_check_span = FakeSpan(_clean_turn())
    guard.guard_before("query_prices", {"pod": "alpha"})
    guard.last_check_span = FakeSpan(_turn(blocked=True))
    guard.guard_before("bash", {"command": "rm -rf /"})

    assert [s["tool"] for s in run.steps] == ["query_prices", "bash"]
    assert [s["status"] for s in run.steps] == ["ok", "blocked"]
    assert run.summary()["stopped"] == 1


def test_the_guarded_call_still_returns_its_result(tmp_path):
    guard, client = FakeGuard(), FakeClient()
    attach(guard, client=client, runs_dir=tmp_path)
    guard.last_check_span = FakeSpan(_clean_turn())

    assert guard.guard_before("t", {}) == {"allowed": True}


def test_multi_agent_steps_keep_their_own_agent(tmp_path):
    guard, client = FakeGuard(), FakeClient()
    run = attach(guard, client=client, runs_dir=tmp_path, auto=False)

    guard.last_check_span = FakeSpan(_clean_turn(agent="data_agent"))
    run.record("query_prices", {}, agent="data_agent")
    run.call("quant", "report_agent")
    guard.last_check_span = FakeSpan(_clean_turn(agent="report_agent"))
    run.record("publish", {}, agent="report_agent")

    assert {s["agentId"] for s in run.steps} >= {"data_agent", "report_agent"}
    assert run.edges[0] == {
        "from": "quant",
        "to": "report_agent",
        "kind": "call",
        "ts": 1,
    }


def test_each_agent_gets_its_own_trace_id(tmp_path):
    guard, client = FakeGuard(), FakeClient()
    run = attach(guard, client=client, runs_dir=tmp_path, auto=False)

    guard.last_check_span = FakeSpan(_clean_turn())
    a = run.record("t1", {}, agent="one")
    b = run.record("t2", {}, agent="two")
    assert a["traceId"] != b["traceId"]


# -- telemetry must not change the run -------------------------------------


def test_a_dead_network_does_not_break_the_run(tmp_path):
    guard, client = FakeGuard(), FakeClient(fail=True)
    run = attach(guard, client=client, runs_dir=tmp_path)
    guard.last_check_span = FakeSpan(_clean_turn())

    guard.guard_before("t", {})

    assert guard.calls == [("t", {})], "the guarded call still happened"
    assert run.send_failures > 0


def test_no_key_means_no_upload_and_no_error(tmp_path):
    guard, client = FakeGuard(), FakeClient(configured=False)
    run = attach(guard, client=client, runs_dir=tmp_path)
    guard.last_check_span = FakeSpan(_clean_turn())

    guard.guard_before("t", {})

    assert client.sent == []
    assert run.send_failures == 0, "not being configured is not a failure"


def test_finish_only_claims_a_session_when_one_was_uploaded(tmp_path, capsys):
    guard, client = FakeGuard(), FakeClient(fail=True)
    run = attach(guard, client=client, runs_dir=tmp_path)
    guard.last_check_span = FakeSpan(_clean_turn())
    guard.guard_before("t", {})

    result = run.finish()
    out = capsys.readouterr().out

    assert result["uploaded"] is False
    assert "not uploaded" in out
    assert run.session_id not in out, (
        "a session id printed for a run that never uploaded points at nothing"
    )


def test_finish_names_the_session_when_it_did_upload(tmp_path, capsys):
    guard, client = FakeGuard(), FakeClient()
    run = attach(guard, client=client, runs_dir=tmp_path)
    guard.last_check_span = FakeSpan(_clean_turn())
    guard.guard_before("t", {})

    result = run.finish()

    assert result["uploaded"] is True
    assert run.session_id in capsys.readouterr().out


# -- artifacts and payload -------------------------------------------------


def test_finish_writes_the_local_trace_and_session(tmp_path):
    guard, client = FakeGuard(), FakeClient()
    run = attach(guard, client=client, runs_dir=tmp_path)
    guard.last_check_span = FakeSpan(_clean_turn())
    guard.guard_before("t", {"secret": "full value"})

    paths = run.finish()["paths"]

    assert json.loads(open(paths["session"]).read())["steps"]
    trace = json.loads(open(paths["trace"]).read())
    assert trace["events"][0]["args"] == {"secret": "full value"}, (
        "the local trace keeps full arguments; it is what a coding agent mines"
    )


def test_the_uploaded_payload_carries_only_the_preview(tmp_path):
    guard, client = FakeGuard(), FakeClient()
    run = attach(guard, client=client, runs_dir=tmp_path)
    guard.last_check_span = FakeSpan(_clean_turn())
    guard.guard_before("t", {"secret": "x" * 500})

    step = client.sent[-1]["vm"]["steps"][0]
    assert len(step["argsPreview"]) <= 160
    assert "argsFull" not in step
    _ = run


def test_the_rulebook_stamp_rides_along_when_the_config_came_from_the_cloud(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SPONSIO_RULEBOOK_STAMP", "alpha quant@v7 sha:abc")
    guard, client = FakeGuard(), FakeClient()
    run = attach(guard, client=client, runs_dir=tmp_path)
    guard.last_check_span = FakeSpan(_clean_turn())
    guard.guard_before("t", {})

    vm = client.sent[-1]["vm"]
    # The run names ITS OWN book, in the form the console links to and the
    # server keys tests by; the whole checkout rides along as provenance.
    assert vm["rulebook"] == "quant@v7"
    assert vm["rulebookRef"] == "alpha quant@v7 sha:abc"
    assert vm["session"]["rulebookVersion"] == 7
    _ = run


def test_a_single_agent_checkout_stamp_still_names_this_agents_book(
    tmp_path, monkeypatch
):
    # a single-agent pull is stamped "<project>@vN" with no agent prefix
    monkeypatch.setenv("SPONSIO_RULEBOOK_STAMP", "alpha@v5 sha:abc")
    guard, client = FakeGuard(), FakeClient()
    attach(guard, client=client, runs_dir=tmp_path)
    guard.last_check_span = FakeSpan(_clean_turn())
    guard.guard_before("t", {})

    vm = client.sent[-1]["vm"]
    assert vm["rulebook"] == "quant@v5"
    assert vm["session"]["rulebookVersion"] == 5


def test_a_local_run_claims_no_rulebook_version(tmp_path, monkeypatch):
    monkeypatch.delenv("SPONSIO_RULEBOOK_STAMP", raising=False)
    guard, client = FakeGuard(), FakeClient()
    run = attach(guard, client=client, runs_dir=tmp_path)
    guard.last_check_span = FakeSpan(_clean_turn())
    guard.guard_before("t", {})

    assert "rulebook" not in client.sent[-1]["vm"]
    _ = run


def test_the_run_key_is_stable_across_frames(tmp_path):
    """Ingest is idempotent on the key; a key that changed per frame would
    make one run look like many."""
    guard, client = FakeGuard(), FakeClient()
    run = attach(guard, client=client, runs_dir=tmp_path)
    guard.last_check_span = FakeSpan(_clean_turn())

    guard.guard_before("a", {})
    guard.guard_before("b", {})
    run.finish()

    assert {frame["key"] for frame in client.sent} == {run.session_id}


def test_each_frame_carries_the_whole_run_so_far(tmp_path):
    guard, client = FakeGuard(), FakeClient()
    run = attach(guard, client=client, runs_dir=tmp_path)
    # Both knobs off: the floor AND the size-proportional backoff.
    run.SEND_MIN_INTERVAL_S = 0.0
    run.SEND_MAX_KB_PER_S = float("inf")
    guard.last_check_span = FakeSpan(_clean_turn())

    guard.guard_before("a", {})
    guard.guard_before("b", {})

    # Frames are cumulative snapshots, never deltas.
    assert [len(f["vm"]["steps"]) for f in client.sent] == [1, 2]


def test_frames_coalesce_so_a_long_run_does_not_upload_the_run_squared(tmp_path):
    """Every frame is the whole run, so one frame per step costs O(steps^2)
    bytes. Steps taken inside the interval share a frame instead."""
    guard, client = FakeGuard(), FakeClient()
    run = attach(guard, client=client, runs_dir=tmp_path)
    guard.last_check_span = FakeSpan(_clean_turn())

    for _ in range(50):
        guard.guard_before("a", {})

    assert len(client.sent) < 50
    # ...and the run is still whole once it ends.
    run.finish()
    assert len(client.sent[-1]["vm"]["steps"]) == 50


def test_live_flips_false_on_finish(tmp_path):
    guard, client = FakeGuard(), FakeClient()
    run = attach(guard, client=client, runs_dir=tmp_path)
    guard.last_check_span = FakeSpan(_clean_turn())
    guard.guard_before("a", {})

    run.finish()
    assert client.sent[-1]["live"] is False


@pytest.mark.parametrize("mode", ["enforce", "observe"])
def test_the_payload_reports_the_mode_the_run_used(tmp_path, mode):
    guard, client = FakeGuard(mode=mode), FakeClient()
    run = attach(guard, client=client, runs_dir=tmp_path)
    guard.last_check_span = FakeSpan(_clean_turn())
    guard.guard_before("a", {})

    assert client.sent[-1]["vm"]["session"]["mode"] == mode
    _ = run


# -- the rulebook the run enforced -----------------------------------------


class FakeGuarantee:
    def __init__(self, desc):
        self.desc = desc


class FakeContract:
    def __init__(self, desc, mode=None):
        self.desc = desc
        self.mode = mode
        self.guarantees = [FakeGuarantee(desc)]


class FakeSystem:
    def __init__(self, contracts):
        self._contracts = contracts


def test_the_run_carries_the_guards_book_without_being_told(tmp_path):
    """A console showing a run with an empty Rulebook reads as "nothing was
    checked" when in fact everything was."""
    guard, client = FakeGuard(mode="observe"), FakeClient()
    guard._system = FakeSystem([FakeContract("no rm"), FakeContract("cap notify")])

    run = attach(guard, client=client, runs_dir=tmp_path)

    assert {c["label"] for c in run.contracts.values()} == {"no rm", "cap notify"}
    assert run.summary()["contractsTotal"] == 2


def test_contract_status_reflects_each_rules_own_mode(tmp_path):
    """An observing book with one armed rule shows exactly that, instead of
    claiming all or nothing."""
    guard, client = FakeGuard(mode="observe"), FakeClient()
    guard._system = FakeSystem(
        [FakeContract("armed one", mode="enforce"), FakeContract("watched one")]
    )

    run = attach(guard, client=client, runs_dir=tmp_path)
    by_label = {c["label"]: c["status"] for c in run.contracts.values()}

    assert by_label == {"armed one": "armed", "watched one": "watching"}


def test_an_explicit_contract_list_still_wins(tmp_path):
    guard, client = FakeGuard(), FakeClient()
    guard._system = FakeSystem([FakeContract("from the guard")])

    run = attach(
        guard,
        client=client,
        runs_dir=tmp_path,
        contracts=[{"id": "x", "label": "supplied by the caller"}],
    )

    assert [c["label"] for c in run.contracts.values()] == ["supplied by the caller"]


def test_a_guard_without_a_system_is_not_an_error(tmp_path):
    guard, client = FakeGuard(), FakeClient()
    run = attach(guard, client=client, runs_dir=tmp_path)
    assert run.contracts == {}
