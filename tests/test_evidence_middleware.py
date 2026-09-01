"""Phase 2: evidence middleware at observe_llm_call + openai enforcement.

All HTTP is mocked at the EvidenceClient boundary (a fake with canned
EvidenceResults); no network anywhere. Verdict/action words in the canned
results are the server's vocabulary — the middleware computes none of
them.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from sponsio.cloud.evidence import EvidenceClient, EvidenceError, EvidenceResult
from sponsio.integrations.base import BaseGuard
from sponsio.integrations.evidence_middleware import (
    EvidenceConfig,
    EvidenceConfigError,
    extract_claims,
    format_block_notice,
    format_clarify_notice,
    structured_sources,
)

# ---------------------------------------------------------------------------
# canned server verdicts
# ---------------------------------------------------------------------------


def _pass(predicate="date_weekday_agreement"):
    return EvidenceResult(predicate=predicate, verdict="PASS", action="release")


def _mismatch(predicate="date_weekday_agreement", correction="saturday"):
    return EvidenceResult(
        predicate=predicate,
        verdict="MISMATCH",
        action="block",
        correction=correction,
    )


def _underdetermined(predicate="city_zip_candidates", n=7):
    return EvidenceResult(
        predicate=predicate,
        verdict="UNDERDETERMINED",
        action="clarify",
        clarify_on=tuple(f"9470{i}" for i in range(n)),
    )


class FakeEvidenceClient(EvidenceClient):
    """Records verify_batch calls; replays canned results or raises."""

    def __init__(self, results=None, error: Exception | None = None):
        # No CloudClient: nothing here does I/O.
        self.calls: list[list[dict]] = []
        self._results = list(results or [])
        self._error = error

    def verify_batch(self, claims, *, session_id=None):
        self.calls.append([dict(c) for c in claims])
        if self._error is not None:
            raise self._error
        return list(self._results)


WEEKDAY_CLAIM = {
    "predicate": "date_weekday_agreement",
    "claim_field": "weekday",
    "inputs": {"date": {"field": "date", "source": "model_output"}},
}
ZIP_CLAIM = {
    "predicate": "city_zip_candidates",
    "claim_field": "zip_code",
    "inputs": {
        "city": {"field": "city", "source": "model_output"},
        "state": {"field": "state", "source": "model_output"},
    },
}


def make_config(client, claims=(WEEKDAY_CLAIM,), on_error="block"):
    return EvidenceConfig.from_value(
        {"client": client, "on_error": on_error, "claims": list(claims)}
    )


def make_guard(client, claims=(WEEKDAY_CLAIM,), on_error="block") -> BaseGuard:
    return BaseGuard(
        contracts=[],
        verbose=False,
        init_banner=False,
        auto_summary=False,
        evidence=make_config(client, claims, on_error),
    )


WEEKDAY_JSON = json.dumps({"weekday": "Friday", "date": "2000-01-01"})


# ---------------------------------------------------------------------------
# config parsing
# ---------------------------------------------------------------------------


class TestConfigParsing:
    def test_defaults(self):
        config = make_config(FakeEvidenceClient())
        assert config.on_error == "block"  # fail closed by default
        assert config.claims[0].predicate == "date_weekday_agreement"
        assert config.claims[0].inputs["date"].source == "model_output"

    def test_config_instance_passes_through(self):
        config = make_config(FakeEvidenceClient())
        assert EvidenceConfig.from_value(config) is config

    def test_bad_on_error_rejected(self):
        with pytest.raises(EvidenceConfigError, match="on_error"):
            make_config(FakeEvidenceClient(), on_error="shrug")

    def test_claim_without_predicate_rejected(self):
        with pytest.raises(EvidenceConfigError, match="predicate"):
            make_config(FakeEvidenceClient(), claims=[{"claim_field": "x"}])

    def test_claim_without_claim_field_rejected(self):
        with pytest.raises(EvidenceConfigError, match="claim_field"):
            make_config(FakeEvidenceClient(), claims=[{"predicate": "p"}])

    def test_input_without_source_rejected(self):
        with pytest.raises(EvidenceConfigError, match="'field' and 'source'"):
            make_config(
                FakeEvidenceClient(),
                claims=[
                    {
                        "predicate": "p",
                        "claim_field": "x",
                        "inputs": {"date": {"field": "date"}},
                    }
                ],
            )

    def test_malformed_config_fails_at_guard_construction(self):
        with pytest.raises(EvidenceConfigError):
            BaseGuard(
                contracts=[],
                verbose=False,
                init_banner=False,
                auto_summary=False,
                evidence={"on_error": "nope", "claims": []},
            )


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------


class TestExtraction:
    def test_from_json_content(self):
        config = make_config(FakeEvidenceClient())
        claims = extract_claims(config, structured_sources(WEEKDAY_JSON, None))
        assert len(claims) == 1
        assert claims[0].value == "Friday"
        assert claims[0].wire == {
            "predicate": "date_weekday_agreement",
            "value": "Friday",
            "inputs": {"date": ("2000-01-01", "model_output")},
        }

    def test_from_tool_call_args(self):
        config = make_config(FakeEvidenceClient(), claims=[ZIP_CLAIM])
        sources = structured_sources(
            "plain prose, not JSON",
            [{"zip_code": "94720", "city": "Berkeley", "state": "CA"}],
        )
        claims = extract_claims(config, sources)
        assert len(claims) == 1
        assert claims[0].wire["inputs"]["city"] == ("Berkeley", "model_output")

    def test_absent_claim_field_is_no_claim(self):
        config = make_config(FakeEvidenceClient())
        claims = extract_claims(
            config, structured_sources(json.dumps({"other": 1}), None)
        )
        assert claims == []

    def test_non_json_content_and_no_tool_args_is_no_claim(self):
        config = make_config(FakeEvidenceClient())
        assert extract_claims(config, structured_sources("hello world", None)) == []

    def test_json_content_wins_over_tool_args(self):
        config = make_config(FakeEvidenceClient())
        sources = structured_sources(
            WEEKDAY_JSON, [{"weekday": "Sunday", "date": "1999-12-31"}]
        )
        claims = extract_claims(config, sources)
        assert claims[0].value == "Friday"  # content JSON has precedence

    def test_missing_input_field_sends_claim_without_that_input(self):
        config = make_config(FakeEvidenceClient())
        claims = extract_claims(
            config, structured_sources(json.dumps({"weekday": "Friday"}), None)
        )
        assert claims[0].wire["inputs"] == {}  # resolver will answer UNAVAILABLE


# ---------------------------------------------------------------------------
# observe_llm_call folding
# ---------------------------------------------------------------------------


class TestObserveLLMCallFolding:
    def test_verify_batch_called_once_per_response(self):
        client = FakeEvidenceClient([_pass(), _underdetermined()])
        guard = make_guard(client, claims=(WEEKDAY_CLAIM, ZIP_CLAIM))
        content = json.dumps(
            {
                "weekday": "Saturday",
                "date": "2000-01-01",
                "zip_code": "94720",
                "city": "Berkeley",
                "state": "CA",
            }
        )
        result = guard.observe_llm_call(response=content)
        assert len(client.calls) == 1
        assert len(client.calls[0]) == 2  # both claims in the one batch
        assert len(result.evidence_claims) == 2

    def test_block_folds_into_stopping_check_result(self):
        client = FakeEvidenceClient([_mismatch()])
        guard = make_guard(client)
        result = guard.observe_llm_call(response=WEEKDAY_JSON)
        assert result.evidence_stopped is True
        assert result.allowed is False
        assert result.stop_original is True  # Phase-1 canonical predicate
        assert any(
            v.rule_id == "evidence:date_weekday_agreement"
            for v in result.det_violations
        )

    def test_pass_does_not_stop(self):
        client = FakeEvidenceClient([_pass()])
        guard = make_guard(client)
        result = guard.observe_llm_call(response=WEEKDAY_JSON)
        assert result.evidence_stopped is False
        assert result.stop_original is False
        assert result.allowed is True

    def test_clarify_carries_candidates_and_does_not_stop(self):
        client = FakeEvidenceClient([_underdetermined()])
        guard = make_guard(client, claims=(ZIP_CLAIM,))
        content = json.dumps({"zip_code": "94720", "city": "Berkeley", "state": "CA"})
        result = guard.observe_llm_call(response=content)
        assert result.evidence_stopped is False
        assert result.stop_original is False
        clarifications = result.evidence_clarifications
        assert len(clarifications) == 1
        assert clarifications[0].result.clarify_on[:2] == ("94700", "94701")

    def test_on_error_block_stops(self):
        client = FakeEvidenceClient(error=EvidenceError("cannot reach cloud"))
        guard = make_guard(client, on_error="block")
        result = guard.observe_llm_call(response=WEEKDAY_JSON)
        assert result.evidence_stopped is True
        assert result.stop_original is True
        assert "cannot reach cloud" in (result.evidence_error or "")

    def test_on_error_allow_releases_but_records_error(self):
        client = FakeEvidenceClient(error=EvidenceError("cannot reach cloud"))
        guard = make_guard(client, on_error="allow")
        result = guard.observe_llm_call(response=WEEKDAY_JSON)
        assert result.evidence_stopped is False
        assert result.stop_original is False
        assert "cannot reach cloud" in (result.evidence_error or "")

    def test_verdicts_are_fed_into_the_trace_as_evidence_events(self):
        client = FakeEvidenceClient([_mismatch()])
        guard = make_guard(client)
        guard.observe_llm_call(response=WEEKDAY_JSON)
        evidence_events = [
            e for e in guard._monitor.trace.events if e.event_type == "evidence"
        ]
        assert len(evidence_events) == 1
        assert evidence_events[0].key == "date_weekday_agreement"
        # The event carries the display enrichment (claim/correction/source/
        # values) alongside verdict/action; grounding reads only the latter.
        assert evidence_events[0].args == {
            "verdict": "MISMATCH",
            "action": "block",
            "claim": "Friday",
            "correction": "saturday",
            "source": None,
            "values": [],
        }

    def test_no_claims_means_no_batch_call(self):
        client = FakeEvidenceClient([_pass()])
        guard = make_guard(client)
        result = guard.observe_llm_call(response="free text, nothing structured")
        assert client.calls == []
        assert result.evidence_claims == []
        assert result.evidence_stopped is False

    def test_unconfigured_guard_is_unchanged(self):
        guard = BaseGuard(
            contracts=[], verbose=False, init_banner=False, auto_summary=False
        )
        result = guard.observe_llm_call(response=WEEKDAY_JSON)
        assert result.allowed is True
        assert result.evidence_claims == []
        assert result.evidence_error is None
        assert result.evidence_stopped is False
        assert (
            len([e for e in guard._monitor.trace.events if e.event_type == "evidence"])
            == 0
        )


# ---------------------------------------------------------------------------
# openai enforcement (sync + async), SDK patched — mocked transport
# ---------------------------------------------------------------------------


def _mock_response(content: str):
    message = MagicMock()
    message.content = content
    message.tool_calls = None
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    response.usage = None
    return response


class TestOpenAIEnforcement:
    def _guard(self, client, claims=(WEEKDAY_CLAIM,)):
        from sponsio.integrations.openai import OpenAIGuard

        return OpenAIGuard(
            contracts=[],
            verbose=False,
            init_banner=False,
            auto_summary=False,
            evidence=make_config(client, claims),
        )

    def test_block_rewrites_assistant_text(self):
        guard = self._guard(FakeEvidenceClient([_mismatch()]))
        response = _mock_response(WEEKDAY_JSON)
        results = guard.check_response(response)
        assert not any(r.stop_original for r in results)  # no tool calls
        rewritten = guard._apply_evidence_notices(response)
        text = rewritten.choices[0].message.content
        assert text.startswith("[BLOCKED] claim 'weekday'=Friday failed verification")
        assert "evidence says: saturday" in text

    def test_clarify_rewrites_with_question_capped_at_five(self):
        guard = self._guard(
            FakeEvidenceClient([_underdetermined(n=7)]), claims=(ZIP_CLAIM,)
        )
        response = _mock_response(
            json.dumps({"zip_code": "94720", "city": "Berkeley", "state": "CA"})
        )
        guard.check_response(response)
        text = guard._apply_evidence_notices(response).choices[0].message.content
        assert "ambiguous" in text
        assert "94700, 94701, 94702, 94703, 94704" in text
        assert "(+2 more)" in text
        assert "94706" not in text

    def test_error_block_notice(self):
        guard = self._guard(FakeEvidenceClient(error=EvidenceError("down")))
        response = _mock_response(WEEKDAY_JSON)
        guard.check_response(response)
        text = guard._apply_evidence_notices(response).choices[0].message.content
        assert "verification unavailable" in text
        assert "on_error=block" in text

    def test_unconfigured_guard_leaves_response_untouched(self):
        from sponsio.integrations.openai import OpenAIGuard

        guard = OpenAIGuard(
            contracts=[], verbose=False, init_banner=False, auto_summary=False
        )
        response = _mock_response(WEEKDAY_JSON)
        guard.check_response(response)
        rewritten = guard._apply_evidence_notices(response)
        assert rewritten is response
        assert rewritten.choices[0].message.content == WEEKDAY_JSON

    def test_patched_sync_and_async_paths(self, monkeypatch):
        openai = pytest.importorskip("openai")
        from sponsio.integrations import openai as openai_integration

        response = _mock_response(WEEKDAY_JSON)
        monkeypatch.setattr(
            openai.resources.chat.completions.Completions,
            "create",
            lambda self, *a, **k: response,
        )

        async def fake_async_create(self, *a, **k):
            return _mock_response(WEEKDAY_JSON)

        monkeypatch.setattr(
            openai.resources.chat.completions.AsyncCompletions,
            "create",
            fake_async_create,
        )

        openai_integration.patch_openai(
            contracts=[],
            evidence=make_config(FakeEvidenceClient([_mismatch(), _mismatch()])),
        )
        try:
            out = openai.resources.chat.completions.Completions.create(
                MagicMock(), model="gpt-x", messages=[]
            )
            assert "[BLOCKED]" in out.choices[0].message.content

            import asyncio

            out_async = asyncio.run(
                openai.resources.chat.completions.AsyncCompletions.create(
                    MagicMock(), model="gpt-x", messages=[]
                )
            )
            assert "[BLOCKED]" in out_async.choices[0].message.content
        finally:
            openai_integration.unpatch_openai()

    def test_stream_with_evidence_raises_not_implemented(self, monkeypatch):
        openai = pytest.importorskip("openai")
        from sponsio.integrations import openai as openai_integration

        monkeypatch.setattr(
            openai.resources.chat.completions.Completions,
            "create",
            lambda self, *a, **k: _mock_response("{}"),
        )
        openai_integration.patch_openai(
            contracts=[], evidence=make_config(FakeEvidenceClient())
        )
        try:
            with pytest.raises(NotImplementedError, match="stream"):
                openai.resources.chat.completions.Completions.create(
                    MagicMock(), model="gpt-x", messages=[], stream=True
                )
        finally:
            openai_integration.unpatch_openai()


# ---------------------------------------------------------------------------
# notice formatting
# ---------------------------------------------------------------------------


class TestNotices:
    def test_block_notice_without_correction(self):
        from sponsio.integrations.evidence_middleware import VerifiedClaim

        claim = VerifiedClaim(
            spec=make_config(FakeEvidenceClient()).claims[0],
            value="Friday",
            result=EvidenceResult(
                predicate="date_weekday_agreement",
                verdict="NO_EVIDENCE",
                action="block",
            ),
        )
        text = format_block_notice([claim])
        assert "NO_EVIDENCE" in text
        assert "evidence says" not in text

    def test_clarify_notice_under_limit_has_no_more_suffix(self):
        from sponsio.integrations.evidence_middleware import VerifiedClaim

        claim = VerifiedClaim(
            spec=EvidenceConfig.from_value(
                {"client": FakeEvidenceClient(), "claims": [ZIP_CLAIM]}
            ).claims[0],
            value="94720",
            result=_underdetermined(n=3),
        )
        text = format_clarify_notice([claim])
        assert "more" not in text
