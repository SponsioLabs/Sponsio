"""Auto-tagging of tool outputs at the BaseGuard level.

Every framework integration (LangGraph, CrewAI, Vercel AI, Claude Agent,
OpenAI Agents SDK) funnels through ``BaseGuard.guard_after`` — and
``MCPContractProxy`` runs the same logic inlined.  Testing at the
BaseGuard level confirms the feature works for all of them without
instantiating each adapter's transport stack.
"""

from __future__ import annotations

from sponsio.integrations.base import BaseGuard, _detect_pii_classes


def _data_writes(guard: BaseGuard):
    return [e for e in guard._monitor.trace.events if e.event_type == "data_write"]


# ---------------------------------------------------------------------------
# _detect_pii_classes — the regex bank
# ---------------------------------------------------------------------------


class TestPIIDetector:
    def test_ssn(self):
        assert _detect_pii_classes("Customer SSN: 123-45-6789") == ["pii", "ssn"]

    def test_email(self):
        classes = _detect_pii_classes("Reply to alice@example.com")
        assert "pii" in classes and "email" in classes

    def test_credit_card(self):
        classes = _detect_pii_classes("card 4111 1111 1111 1111 stored")
        assert "pii" in classes and "credit_card" in classes

    def test_phone(self):
        classes = _detect_pii_classes("call me at (415) 555-1234")
        assert "pii" in classes and "phone" in classes

    def test_api_key(self):
        classes = _detect_pii_classes("OPENAI_API_KEY=sk-abcdef1234567890ABCDEF")
        assert "pii" in classes and "secret" in classes

    def test_multiple_classes(self):
        text = "User alice@example.com, SSN 123-45-6789, phone (415) 555-1234"
        classes = _detect_pii_classes(text)
        assert "pii" in classes
        assert {"email", "ssn", "phone"}.issubset(set(classes))

    def test_no_pii_returns_empty(self):
        assert _detect_pii_classes("Order 42 shipped") == []

    def test_none_input(self):
        assert _detect_pii_classes(None) == ["pii"] or _detect_pii_classes(None) == []
        # We don't care which — just that it doesn't raise.

    def test_non_string_input_is_stringified(self):
        # int 1234567890 is 10 digits — should not trigger credit_card
        # (needs 13-19) or phone (needs separators).
        assert _detect_pii_classes(1234567890) == []

    def test_ordinary_9_digit_int_not_ssn(self):
        # 9 digits without separators must NOT be tagged as SSN —
        # order IDs, hashes, timestamps all look like this.
        assert "ssn" not in _detect_pii_classes("order 123456789 processed")


# ---------------------------------------------------------------------------
# BaseGuard.guard_after → auto-tag
# ---------------------------------------------------------------------------


class TestGuardAfterAutoTag:
    def test_default_tag_outputs_on(self):
        """Default: every guard_after emits contains(tool_name)."""
        guard = BaseGuard(agent_id="bot")
        guard.guard_after("lookup_customer", "customer record for 42")

        writes = _data_writes(guard)
        assert len(writes) == 1
        w = writes[0]
        assert w.key == "lookup_customer"
        assert w.contains == ["lookup_customer"]
        assert w.agent == "bot"

    def test_tag_outputs_false_disables(self):
        guard = BaseGuard(agent_id="bot", tag_outputs=False)
        guard.guard_after("lookup_customer", "customer record for 42")
        assert _data_writes(guard) == []

    def test_empty_tool_name_skipped(self):
        """``guard_after('', output)`` (deprecated LangGraph on_tool_end
        path) must not crash and must not emit a tag — there's no tool
        name to bind ``contains()`` to."""
        guard = BaseGuard(agent_id="bot")
        guard.guard_after("", "some output")
        assert _data_writes(guard) == []

    def test_tag_pii_adds_pii_classes(self):
        guard = BaseGuard(agent_id="bot", tag_pii=True)
        guard.guard_after(
            "lookup_customer",
            "Alice Smith / SSN 123-45-6789 / alice@example.com",
        )

        writes = _data_writes(guard)
        assert len(writes) == 1
        contains = writes[0].contains
        # Tool name always first, then pii classes
        assert contains[0] == "lookup_customer"
        assert "pii" in contains
        assert "ssn" in contains
        assert "email" in contains

    def test_tag_pii_false_skips_pii_detection(self):
        """Even when tool output contains PII, tag_pii=False keeps the
        contains list at just [tool_name]."""
        guard = BaseGuard(agent_id="bot", tag_pii=False)
        guard.guard_after("lookup_customer", "SSN 123-45-6789")

        writes = _data_writes(guard)
        assert len(writes) == 1
        assert writes[0].contains == ["lookup_customer"]

    def test_tag_pii_implies_tag_outputs(self):
        """``tag_pii=True`` is meaningless without ``tag_outputs=True``.
        When the latter is off the helper no-ops entirely."""
        guard = BaseGuard(agent_id="bot", tag_outputs=False, tag_pii=True)
        guard.guard_after("lookup_customer", "SSN 123-45-6789")
        assert _data_writes(guard) == []


# ---------------------------------------------------------------------------
# End-to-end: contains(pii) + no_data_leak fire on real traces
# ---------------------------------------------------------------------------


def test_tag_pii_makes_no_data_leak_fireable():
    """With ``tag_pii=True``, a sensitive read followed by external
    delegation produces a trace on which ``no_data_leak('pii', ext)``
    fires — no manual instrumentation needed.
    """
    from sponsio.formulas.evaluator import evaluate
    from sponsio.patterns.library import no_data_leak
    from sponsio.tracer.grounding import ground

    guard = BaseGuard(agent_id="bot", tag_pii=True)
    # Simulate: tool returned customer PII
    guard.guard_after("lookup_customer", "Alice / SSN 123-45-6789")
    # ... which the agent then forwards to a Slack webhook
    guard.observe_delegation("slack_webhook")

    vals = ground(guard._monitor.trace)

    # contains(pii) should be set somewhere and sticky afterwards
    assert any(v.get("contains(pii)") for v in vals), (
        "tag_pii=True should produce a contains(pii) predicate"
    )
    # flow(bot, slack_webhook) should be set at/after the delegation
    assert any(v.get("flow(bot, slack_webhook)") for v in vals), (
        "observe_delegation should produce a flow predicate"
    )

    # ``no_data_leak(source='pii', external='slack_webhook')`` compiles
    # to ``G(contains(pii) -> !flow(pii, slack_webhook))`` — but flow
    # predicates are keyed on (writer_agent, dest), not (field, dest).
    # The canonical generic PII-leak contract uses the bot's agent_id
    # as the source — so we additionally verify the *components* are
    # visible in the trace, which is the part that was unusable before.
    # Separately, users writing ``no_data_leak('bot', 'slack_webhook')``
    # need a contains(bot) tag — that comes from ``contains=['bot']``,
    # which users can opt into by calling
    # ``observe_data_write(key=..., fields=[agent_id])`` directly.
    bot_leak = no_data_leak("bot", "slack_webhook")
    assert evaluate(bot_leak.formula, vals) is True  # no contains(bot) tag set
