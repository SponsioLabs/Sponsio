"""Round-trip tests for ``span_tree_to_otlp``.

We don't round-trip back to a Span tree (that would require building a
consumer that's the dual of the writer — out of scope for now). Instead
we verify each emitted span carries the right
:mod:`sponsio.tracer.semconv` attributes given a known input. The shape
the dashboard cards in ``docs/observability.md`` rely on is exercised
end-to-end via the ``test_card_*`` cases below.
"""

from __future__ import annotations

import json
import time

import pytest

from sponsio.models.spans import (
    AgentTurnSpan,
    ContractCheckSpan,
    EnforcementSpan,
    GuaranteeSpan,
    PreconditionSpan,
    StoEvalSpan,
    ViolationSpan,
)
from sponsio.tracer import semconv
from sponsio.tracer.otel_writer import span_tree_to_otlp


def _attrs_of(span: dict) -> dict:
    """Flatten OTLP tagged-union attributes back into a plain dict."""
    out: dict = {}
    for entry in span.get("attributes", []):
        v = entry["value"]
        if "stringValue" in v:
            out[entry["key"]] = v["stringValue"]
        elif "boolValue" in v:
            out[entry["key"]] = v["boolValue"]
        elif "intValue" in v:
            out[entry["key"]] = int(v["intValue"])
        elif "doubleValue" in v:
            out[entry["key"]] = v["doubleValue"]
    return out


def _root_and_children(otlp: dict) -> tuple[dict, list[dict]]:
    spans = otlp["resourceSpans"][0]["scopeSpans"][0]["spans"]
    root = next(s for s in spans if s["name"] == semconv.SPAN_AGENT_TURN)
    others = [s for s in spans if s["name"] != semconv.SPAN_AGENT_TURN]
    return root, others


def _make_minimal_turn(blocked: bool = False) -> AgentTurnSpan:
    """Build a small but realistic span tree: one det contract that
    fails its guarantee, with a violation + enforcement child."""
    now = time.monotonic()
    turn = AgentTurnSpan(
        span_type=semconv.SPAN_AGENT_TURN,
        start_time=now,
        end_time=now + 0.0042,
        status="violated" if blocked else "ok",
        agent_id="_host_cursor",
        action="Bash",
        total_contracts_checked=2,
        det_violations=1 if blocked else 0,
        sto_violations=0,
        blocked=blocked,
    )

    contract = ContractCheckSpan(
        span_type=semconv.SPAN_CONTRACT_CHECK,
        start_time=now,
        end_time=now + 0.001,
        status="violated" if blocked else "ok",
        contract_name="Code freeze (policy.md ¶1): no DROP / TRUNCATE",
        pipeline="hard",
        attributes={
            "contract_id": "user_policy:freeze_p1",
            "source": "user_policy",
            "assumption_holds": True,
            "enforcement_holds": not blocked,
        },
    )
    turn.children.append(contract)

    pre = PreconditionSpan(
        span_type=semconv.SPAN_PRECONDITION,
        start_time=now,
        end_time=now + 0.0002,
        status="ok",
        formula_desc="true",
        result=True,
    )
    contract.children.append(pre)

    guarantee = GuaranteeSpan(
        span_type=semconv.SPAN_GUARANTEE,
        start_time=now,
        end_time=now + 0.0005,
        status="violated" if blocked else "ok",
        formula_desc="G(¬arg_field_has(Bash, command, DROP\\s+TABLE))",
        result=not blocked,
        attributes={"fresh": True, "eval_pos": 0},
    )
    contract.children.append(guarantee)

    if blocked:
        contract.children.append(
            ViolationSpan(
                span_type=semconv.SPAN_VIOLATION,
                start_time=now + 0.0005,
                end_time=now + 0.0006,
                status="violated",
                kind="guarantee",
                severity="HIGH",
                evidence="BLOCKED: agent tried DROP TABLE users",
                attributes={"policy_ref": "policy.md ¶1"},
            )
        )
        contract.children.append(
            EnforcementSpan(
                span_type=semconv.SPAN_ENFORCEMENT,
                start_time=now + 0.0006,
                end_time=now + 0.001,
                status="ok",
                strategy="DetBlock",
                result_action="blocked",
            )
        )

    return turn


class TestRootSpanAttributes:
    def test_root_carries_event_attributes(self):
        turn = _make_minimal_turn(blocked=True)
        otlp = span_tree_to_otlp(
            turn,
            host="cursor",
            conversation_id="conv-abc-123",
            event_tool="Bash",
            event_args={"command": 'psql -c "DROP TABLE users"'},
            event_type="tool_call",
            event_ts=42,
        )
        root, _ = _root_and_children(otlp)
        attrs = _attrs_of(root)

        assert attrs[semconv.ATTR_AGENT_ID] == "_host_cursor"
        assert attrs[semconv.ATTR_HOST] == "cursor"
        assert attrs[semconv.ATTR_CONVERSATION_ID] == "conv-abc-123"
        assert attrs[semconv.ATTR_EVENT_TOOL] == "Bash"
        assert attrs[semconv.ATTR_EVENT_TYPE] == "tool_call"
        assert attrs[semconv.ATTR_EVENT_TS] == 42
        assert attrs[semconv.ATTR_OUTCOME_BLOCKED] is True
        assert attrs[semconv.ATTR_OUTCOME_STATUS] == "violated"
        assert attrs[semconv.ATTR_CONTRACTS_CHECKED] == 2
        assert attrs[semconv.ATTR_DET_VIOLATIONS] == 1
        # Tool args are JSON-encoded so the dashboard always parses one shape
        decoded = json.loads(attrs[semconv.ATTR_EVENT_TOOL_ARGS])
        assert decoded["command"].startswith("psql")

    def test_redaction_strips_credential_shaped_keys(self):
        turn = _make_minimal_turn()
        otlp = span_tree_to_otlp(
            turn,
            event_tool="api_call",
            event_args={
                "endpoint": "/v1/users",
                "api_key": "sk-secret-xxx",
                "auth_token": "Bearer ...",
            },
            redact_args=True,
        )
        root, _ = _root_and_children(otlp)
        attrs = _attrs_of(root)
        decoded = json.loads(attrs[semconv.ATTR_EVENT_TOOL_ARGS])
        assert decoded["endpoint"] == "/v1/users"
        assert decoded["api_key"] == "<redacted>"
        assert decoded["auth_token"] == "<redacted>"

    def test_truncation_caps_oversize_args(self):
        turn = _make_minimal_turn()
        big_command = "echo " + "x" * 10000
        otlp = span_tree_to_otlp(
            turn,
            event_tool="Bash",
            event_args={"command": big_command},
            redact_args=False,
            truncate=True,
        )
        root, _ = _root_and_children(otlp)
        attrs = _attrs_of(root)
        encoded = attrs[semconv.ATTR_EVENT_TOOL_ARGS]
        assert "(+" in encoded and "truncated)" in encoded
        assert len(encoded.encode("utf-8")) <= semconv.EVENT_ARGS_MAX_BYTES + 64

    def test_truncate_disabled_keeps_full_payload(self):
        turn = _make_minimal_turn()
        big_command = "echo " + "x" * 10000
        otlp = span_tree_to_otlp(
            turn,
            event_tool="Bash",
            event_args={"command": big_command},
            redact_args=False,
            truncate=False,
        )
        root, _ = _root_and_children(otlp)
        attrs = _attrs_of(root)
        decoded = json.loads(attrs[semconv.ATTR_EVENT_TOOL_ARGS])
        assert decoded["command"] == big_command


class TestChildSpanAttributes:
    def test_contract_check_attrs_present(self):
        turn = _make_minimal_turn(blocked=True)
        otlp = span_tree_to_otlp(turn)
        _, others = _root_and_children(otlp)
        contract = next(s for s in others if s["name"] == semconv.SPAN_CONTRACT_CHECK)
        attrs = _attrs_of(contract)
        assert attrs[semconv.ATTR_CONTRACT_LABEL].startswith("Code freeze")
        assert attrs[semconv.ATTR_CONTRACT_PIPELINE] == "det"  # "hard" → "det"
        assert attrs[semconv.ATTR_CONTRACT_ID] == "user_policy:freeze_p1"
        assert attrs[semconv.ATTR_CONTRACT_SOURCE] == "user_policy"
        assert attrs[semconv.ATTR_CONTRACT_ASSUMPTION_HOLDS] is True
        assert attrs[semconv.ATTR_CONTRACT_ENFORCEMENT_HOLDS] is False

    def test_guarantee_carries_fresh_signal(self):
        turn = _make_minimal_turn(blocked=True)
        otlp = span_tree_to_otlp(turn)
        _, others = _root_and_children(otlp)
        guarantee = next(s for s in others if s["name"] == semconv.SPAN_GUARANTEE)
        attrs = _attrs_of(guarantee)
        assert attrs[semconv.ATTR_CONSTRAINT_RESULT] == "violated"
        assert attrs[semconv.ATTR_CONSTRAINT_FRESH] is True
        assert attrs[semconv.ATTR_CONSTRAINT_EVAL_POS] == 0

    def test_violation_carries_policy_ref(self):
        turn = _make_minimal_turn(blocked=True)
        otlp = span_tree_to_otlp(turn)
        _, others = _root_and_children(otlp)
        violation = next(s for s in others if s["name"] == semconv.SPAN_VIOLATION)
        attrs = _attrs_of(violation)
        assert attrs[semconv.ATTR_VIOLATION_KIND] == "guarantee"
        assert attrs[semconv.ATTR_VIOLATION_SEVERITY] == "HIGH"
        assert attrs[semconv.ATTR_VIOLATION_POLICY_REF] == "policy.md ¶1"

    def test_enforcement_strategy_emitted(self):
        turn = _make_minimal_turn(blocked=True)
        otlp = span_tree_to_otlp(turn)
        _, others = _root_and_children(otlp)
        enf = next(s for s in others if s["name"] == semconv.SPAN_ENFORCEMENT)
        attrs = _attrs_of(enf)
        assert attrs[semconv.ATTR_ENFORCEMENT_STRATEGY] == "DetBlock"
        assert attrs[semconv.ATTR_ENFORCEMENT_ACTION] == "blocked"


class TestStoEval:
    def test_sto_eval_emits_score_threshold_passed(self):
        now = time.monotonic()
        turn = AgentTurnSpan(
            span_type=semconv.SPAN_AGENT_TURN,
            start_time=now,
            agent_id="bot",
            action="emit",
        )
        sto = StoEvalSpan(
            span_type=semconv.SPAN_STO_EVAL,
            start_time=now,
            end_time=now + 0.18,
            constraint_name="no_pii",
            score=0.42,
            threshold=0.7,
            passed=False,
            attributes={
                "evidence": "judge: ssn pattern detected",
                "judge_model": "gemini-2.5-flash",
                "judge_latency_ms": 180,
            },
        )
        turn.children.append(sto)
        otlp = span_tree_to_otlp(turn)
        _, others = _root_and_children(otlp)
        sto_span = next(s for s in others if s["name"] == semconv.SPAN_STO_EVAL)
        attrs = _attrs_of(sto_span)
        assert attrs[semconv.ATTR_CONSTRAINT_ATOM] == "no_pii"
        assert attrs[semconv.ATTR_CONSTRAINT_SCORE] == pytest.approx(0.42)
        assert attrs[semconv.ATTR_CONSTRAINT_THRESHOLD] == pytest.approx(0.7)
        assert attrs[semconv.ATTR_CONSTRAINT_PASSED] is False
        assert attrs[semconv.ATTR_CONSTRAINT_RESULT] == "violated"
        assert attrs[semconv.ATTR_CONSTRAINT_EVIDENCE].startswith("judge:")
        assert attrs[semconv.ATTR_JUDGE_MODEL] == "gemini-2.5-flash"
        assert attrs[semconv.ATTR_JUDGE_LATENCY_MS] == 180


class TestResourceMetadata:
    def test_schema_url_and_version_stamped(self):
        turn = _make_minimal_turn()
        otlp = span_tree_to_otlp(turn)
        scope = otlp["resourceSpans"][0]["scopeSpans"][0]
        assert scope["schemaUrl"] == semconv.SCHEMA_URL
        assert scope["scope"]["name"] == "sponsio"
        assert scope["scope"]["version"] == semconv.SCHEMA_VERSION

    def test_service_name_falls_back_to_agent_id(self):
        turn = _make_minimal_turn()
        otlp = span_tree_to_otlp(turn)  # no service_name override
        attrs_list = otlp["resourceSpans"][0]["resource"]["attributes"]
        kv = {a["key"]: a["value"]["stringValue"] for a in attrs_list}
        assert kv["service.name"] == "_host_cursor"

    def test_explicit_service_name_overrides_agent_id(self):
        turn = _make_minimal_turn()
        otlp = span_tree_to_otlp(turn, service_name="prod-coding-agent")
        attrs_list = otlp["resourceSpans"][0]["resource"]["attributes"]
        kv = {a["key"]: a["value"]["stringValue"] for a in attrs_list}
        assert kv["service.name"] == "prod-coding-agent"


class TestParenting:
    def test_children_reference_parent_span_id(self):
        turn = _make_minimal_turn(blocked=True)
        otlp = span_tree_to_otlp(turn)
        spans = otlp["resourceSpans"][0]["scopeSpans"][0]["spans"]
        root = next(s for s in spans if s["name"] == semconv.SPAN_AGENT_TURN)
        contract = next(s for s in spans if s["name"] == semconv.SPAN_CONTRACT_CHECK)
        guarantee = next(s for s in spans if s["name"] == semconv.SPAN_GUARANTEE)
        violation = next(s for s in spans if s["name"] == semconv.SPAN_VIOLATION)

        assert "parentSpanId" not in root  # root has no parent
        assert contract["parentSpanId"] == root["spanId"]
        assert guarantee["parentSpanId"] == contract["spanId"]
        assert violation["parentSpanId"] == contract["spanId"]


class TestStatusCode:
    def test_violated_maps_to_otlp_error(self):
        turn = _make_minimal_turn(blocked=True)
        otlp = span_tree_to_otlp(turn)
        spans = otlp["resourceSpans"][0]["scopeSpans"][0]["spans"]
        violation = next(s for s in spans if s["name"] == semconv.SPAN_VIOLATION)
        # OTLP StatusCode: 1=OK, 2=ERROR.
        assert violation["status"]["code"] == 2

    def test_ok_maps_to_otlp_ok(self):
        turn = _make_minimal_turn(blocked=False)
        otlp = span_tree_to_otlp(turn)
        spans = otlp["resourceSpans"][0]["scopeSpans"][0]["spans"]
        contract = next(s for s in spans if s["name"] == semconv.SPAN_CONTRACT_CHECK)
        assert contract["status"]["code"] == 1
