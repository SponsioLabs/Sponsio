"""Thin evidence client, grounding atoms, patterns, and reporter lines.

The HTTP layer is mocked by monkeypatching ``CloudClient._request`` —
the same seam the transport uses — so no test needs a network or a real
service. All verdict values in canned responses are copied from the
server's verdict lattice; the client computes none of them.
"""

from __future__ import annotations

import json

import pytest
from rich.console import Console

from sponsio.cloud.client import CloudClient, CloudError
from sponsio.cloud.evidence import (
    EvidenceError,
    EvidenceResult,
    _normalize_inputs,
)
from sponsio.formulas.evaluator import evaluate
from sponsio.models.trace import Event, Trace
from sponsio.patterns.library import (
    claim_requires_evidence,
    underdetermined_must_clarify,
)
from sponsio.render.monitor import render_evidence
from sponsio.render.tokens import PALETTE
from sponsio.runtime.terminal import TerminalReporter
from sponsio.tracer.grounding import ground


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------


def make_client(monkeypatch, *responses, status=200):
    """A CloudClient whose transport replays canned JSON responses."""
    client = CloudClient(api_key="sk_test", url="https://cloud.test")
    calls = []
    queue = list(responses)

    def fake_request(method, path, *, body=None, content_type=None, timeout=None):
        calls.append(
            {
                "method": method,
                "path": path,
                "body": json.loads(body.decode()) if body else None,
            }
        )
        payload = queue.pop(0) if queue else {}
        return status, json.dumps(payload).encode(), {}

    monkeypatch.setattr(client, "_request", fake_request)
    return client, calls


PASS_RESPONSE = {
    "predicate": "date_weekday_agreement",
    "verdict": "PASS",
    "action": "release",
    "evidence": {
        "values": ["saturday"],
        "ts": 1787118534.0,
        "source": "algorithmic",
        "table_version": None,
    },
    "correction": None,
    "clarify_on": None,
    "attestation_id": "3ae09266-a341-4617-a920-40cd233283df",
}

MISMATCH_RESPONSE = {
    **PASS_RESPONSE,
    "verdict": "MISMATCH",
    "action": "block",
    "correction": "saturday",
}

UNDERDETERMINED_RESPONSE = {
    "predicate": "city_zip_candidates",
    "verdict": "UNDERDETERMINED",
    "action": "clarify",
    "evidence": {
        "values": ["94701", "94702", "94703", "94704", "94705", "94707", "94720"],
        "ts": 1787118534.1,
        "source": "tables:ref_postal_codes",
        "table_version": "geonames-2026-08-19",
    },
    "correction": None,
    "clarify_on": ["94701", "94702", "94703", "94704", "94705", "94707", "94720"],
    "attestation_id": "3212fb4b-1e39-4404-8b8c-638675db8630",
}


# ---------------------------------------------------------------------------
# request shape
# ---------------------------------------------------------------------------


class TestRequestShape:
    def test_tuple_inputs_normalize_to_tagged_wire_shape(self, monkeypatch):
        client, calls = make_client(monkeypatch, PASS_RESPONSE)
        client.evidence.verify(
            "date_weekday_agreement",
            value="Friday",
            inputs={"date": ("2000-01-01", "user_utterance")},
            session_id="s-1",
        )
        assert calls[0]["method"] == "POST"
        assert calls[0]["path"] == "/v1/evidence/verify"
        assert calls[0]["body"] == {
            "predicate": "date_weekday_agreement",
            "claim": {"value": "Friday", "text": None},
            "inputs": {"date": {"value": "2000-01-01", "source": "user_utterance"}},
            "session_id": "s-1",
        }

    def test_wire_shaped_inputs_pass_through(self, monkeypatch):
        client, calls = make_client(monkeypatch, PASS_RESPONSE)
        client.evidence.verify(
            "date_weekday_agreement",
            value="Friday",
            inputs={"date": {"value": "2000-01-01", "source": "customer_db"}},
        )
        assert calls[0]["body"]["inputs"]["date"]["source"] == "customer_db"

    def test_untagged_input_is_rejected_locally(self):
        with pytest.raises(ValueError, match="source tag"):
            _normalize_inputs({"date": "2000-01-01"})

    def test_batch_request_shape(self, monkeypatch):
        client, calls = make_client(
            monkeypatch,
            {"results": [PASS_RESPONSE, UNDERDETERMINED_RESPONSE]},
        )
        results = client.evidence.verify_batch(
            [
                {
                    "predicate": "date_weekday_agreement",
                    "value": "Friday",
                    "inputs": {"date": ("2000-01-01", "user_utterance")},
                },
                {
                    "predicate": "city_zip_candidates",
                    "value": "94720",
                    "inputs": {
                        "city": ("Berkeley", "user_utterance"),
                        "state": ("CA", "user_utterance"),
                    },
                },
            ]
        )
        assert calls[0]["path"] == "/v1/evidence/verify_batch"
        wire = calls[0]["body"]["claims"]
        assert wire[0]["claim"]["value"] == "Friday"
        assert wire[1]["inputs"]["state"] == {"value": "CA", "source": "user_utterance"}
        assert [r.verdict for r in results] == ["PASS", "UNDERDETERMINED"]


# ---------------------------------------------------------------------------
# response parsing
# ---------------------------------------------------------------------------


class TestResponseParsing:
    def test_pass(self, monkeypatch):
        client, _ = make_client(monkeypatch, PASS_RESPONSE)
        result = client.evidence.verify("date_weekday_agreement", value="Saturday")
        assert result.passed
        assert not result.blocked
        assert result.action == "release"
        assert result.values == ("saturday",)
        assert result.attestation_id == "3ae09266-a341-4617-a920-40cd233283df"

    def test_mismatch_carries_correction(self, monkeypatch):
        client, _ = make_client(monkeypatch, MISMATCH_RESPONSE)
        result = client.evidence.verify("date_weekday_agreement", value="Friday")
        assert result.verdict == "MISMATCH"
        assert result.blocked
        assert not result.passed
        assert result.correction == "saturday"

    def test_underdetermined_carries_candidates(self, monkeypatch):
        client, _ = make_client(monkeypatch, UNDERDETERMINED_RESPONSE)
        result = client.evidence.verify("city_zip_candidates", value="94720")
        assert result.verdict == "UNDERDETERMINED"
        assert result.needs_clarification
        assert result.clarify_on[:2] == ("94701", "94702")
        assert result.table_version == "geonames-2026-08-19"
        assert result.source == "tables:ref_postal_codes"


# ---------------------------------------------------------------------------
# failure policy — typed exceptions, never fabricated verdicts
# ---------------------------------------------------------------------------


class TestFailurePolicy:
    def test_401_raises_typed_error(self, monkeypatch):
        client, _ = make_client(monkeypatch, {"detail": "bad key"}, status=401)
        with pytest.raises(EvidenceError) as excinfo:
            client.evidence.verify("date_weekday_agreement", value="x")
        assert excinfo.value.status == 401

    def test_404_raises_with_server_detail(self, monkeypatch):
        client, _ = make_client(
            monkeypatch, {"detail": "unknown predicate 'nope'"}, status=404
        )
        with pytest.raises(EvidenceError, match="unknown predicate"):
            client.evidence.verify("nope", value="x")

    def test_transport_failure_raises_typed_error(self, monkeypatch):
        client = CloudClient(api_key="sk_test", url="https://cloud.test")

        def broken_request(*args, **kwargs):
            raise CloudError("cannot reach https://cloud.test: timed out")

        monkeypatch.setattr(client, "_request", broken_request)
        with pytest.raises(EvidenceError, match="cannot reach"):
            client.evidence.verify("date_weekday_agreement", value="x")

    def test_evidence_error_is_a_cloud_error(self):
        # One except-clause covers both: callers catch CloudError.
        assert issubclass(EvidenceError, CloudError)

    def test_non_json_body_raises(self, monkeypatch):
        client = CloudClient(api_key="sk_test", url="https://cloud.test")
        monkeypatch.setattr(
            client, "_request", lambda *a, **k: (200, b"<html>proxy</html>", {})
        )
        with pytest.raises(EvidenceError, match="non-JSON"):
            client.evidence.verify("date_weekday_agreement", value="x")


# ---------------------------------------------------------------------------
# grounding — the fed event produces the atom family
# ---------------------------------------------------------------------------


def _result(verdict="PASS", action="release", predicate="date_weekday_agreement"):
    return EvidenceResult(predicate=predicate, verdict=verdict, action=action)


class TestGrounding:
    def test_to_event_grounds_the_atom_family(self):
        event = _result("MISMATCH", "block").to_event(ts=3, agent="assistant")
        valuations = ground(Trace(events=[event]))
        v = valuations[0]
        assert v["claim_emitted(date_weekday_agreement)"] is True
        assert v["evidence_verdict(date_weekday_agreement, MISMATCH)"] is True
        assert v["evidence_action(date_weekday_agreement, block)"] is True
        assert "evidence_verdict(date_weekday_agreement, PASS)" not in v

    def test_evidence_events_coexist_with_tool_calls(self):
        trace = Trace(
            events=[
                Event(ts=1, agent="a", event_type="tool_call", tool="lookup"),
                _result("PASS").to_event(ts=2, agent="a"),
            ]
        )
        valuations = ground(trace)
        assert valuations[0]["called(lookup)"] is True
        assert valuations[1]["evidence_verdict(date_weekday_agreement, PASS)"] is True


# ---------------------------------------------------------------------------
# patterns
# ---------------------------------------------------------------------------


class TestPatterns:
    def test_claim_requires_evidence_satisfied_by_pass(self):
        contract = claim_requires_evidence("date_weekday_agreement")
        trace = ground(Trace(events=[_result("PASS").to_event(ts=1)]))
        assert evaluate(contract.formula, trace) is True

    def test_claim_requires_evidence_violated_by_mismatch(self):
        contract = claim_requires_evidence("date_weekday_agreement")
        trace = ground(Trace(events=[_result("MISMATCH", "block").to_event(ts=1)]))
        assert evaluate(contract.formula, trace) is False

    def test_underdetermined_must_clarify(self):
        contract = underdetermined_must_clarify("city_zip_candidates")
        ok = ground(
            Trace(
                events=[
                    _result(
                        "UNDERDETERMINED", "clarify", "city_zip_candidates"
                    ).to_event(ts=1)
                ]
            )
        )
        assert evaluate(contract.formula, ok) is True
        bad = ground(
            Trace(
                events=[
                    _result(
                        "UNDERDETERMINED", "release", "city_zip_candidates"
                    ).to_event(ts=1)
                ]
            )
        )
        assert evaluate(contract.formula, bad) is False

    def test_patterns_are_registered_for_yaml_use(self):
        from sponsio.generation.dsl_to_contract import get_available_patterns

        registry = get_available_patterns()
        assert "claim_requires_evidence" in registry
        assert "underdetermined_must_clarify" in registry


# ---------------------------------------------------------------------------
# reporter output
# ---------------------------------------------------------------------------


def _plain(text_obj) -> str:
    console = Console(record=True, width=200, force_terminal=False)
    console.print(text_obj)
    return console.export_text().strip()


class TestReporterLines:
    def test_pass_line(self):
        line = render_evidence(_result("PASS", "release"))
        assert _plain(line) == '✓ evidence "date_weekday_agreement" → PASS'
        assert PALETTE["success"] in str(
            line.markup if hasattr(line, "markup") else line.spans
        )

    def test_mismatch_line_shows_correction(self):
        result = EvidenceResult(
            predicate="date_weekday_agreement",
            verdict="MISMATCH",
            action="block",
            correction="saturday",
        )
        assert (
            _plain(render_evidence(result))
            == '✗ evidence "date_weekday_agreement" → MISMATCH · correction "saturday"'
        )

    def test_underdetermined_line_caps_candidates(self):
        result = EvidenceResult(
            predicate="city_zip_candidates",
            verdict="UNDERDETERMINED",
            action="clarify",
            clarify_on=tuple(f"9470{i}" for i in range(9)),
        )
        rendered = _plain(render_evidence(result))
        assert rendered.startswith('⚠ evidence "city_zip_candidates" → UNDERDETERMINED')
        assert "94700, 94701, 94702, 94703, 94704 +4 more" in rendered

    def test_reporter_prints_through_its_console_with_verbosity_gate(self):
        reporter = TerminalReporter(verbosity=0, colorize=False)
        recording = Console(record=True, width=200, force_terminal=False)
        reporter._console = recording
        reporter.report_evidence(_result("PASS"))  # suppressed at v0
        reporter.report_evidence(_result("MISMATCH", "block"))
        output = recording.export_text()
        assert "PASS" not in output
        assert "MISMATCH" in output
