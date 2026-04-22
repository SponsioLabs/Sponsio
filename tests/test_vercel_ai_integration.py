"""Unit tests for sponsio/integrations/vercel_ai.py — Vercel AI SDK middleware."""

from __future__ import annotations

from sponsio.integrations.vercel_ai import VercelAIGuard


# ---------------------------------------------------------------------------
# Guard init + guard_before / guard_after (framework-independent)
# ---------------------------------------------------------------------------


class TestVercelAIGuardInit:
    def test_creates_with_contracts(self):
        guard = VercelAIGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        assert guard.agent_id == "agent"
        assert guard.last_check is None

    def test_custom_agent_id(self):
        guard = VercelAIGuard(
            agent_id="publish_bot",
            contracts=["tool `A` must precede `B`"],
        )
        assert guard.agent_id == "publish_bot"

    def test_no_contracts_allows_everything(self):
        guard = VercelAIGuard()
        result = guard.guard_before("anything", {})
        assert result.allowed

    def test_empty_contracts_allows_everything(self):
        guard = VercelAIGuard(contracts=[])
        result = guard.guard_before("anything", {})
        assert result.allowed


class TestVercelAIGuardBefore:
    def test_allowed_tool(self):
        guard = VercelAIGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        result = guard.guard_before("check_policy", {})
        assert result.allowed
        assert not result.blocked

    def test_blocked_tool(self):
        guard = VercelAIGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        result = guard.guard_before("issue_refund", {})
        assert result.blocked
        guard.last_check = result
        assert guard.last_check.blocked

    def test_correct_order_allowed(self):
        guard = VercelAIGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        r1 = guard.guard_before("check_policy", {})
        assert r1.allowed

        r2 = guard.guard_before("issue_refund", {})
        assert r2.allowed

    def test_unrelated_tool_allowed(self):
        guard = VercelAIGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        result = guard.guard_before("lookup_customer", {})
        assert result.allowed

    def test_mutual_exclusion_blocked(self):
        guard = VercelAIGuard(
            contracts=["tools `approve` and `reject` are mutually exclusive"]
        )
        r1 = guard.guard_before("approve", {})
        assert r1.allowed

        r2 = guard.guard_before("reject", {})
        assert r2.blocked

    def test_rate_limit_blocked(self):
        guard = VercelAIGuard(contracts=["tool `send_newsletter` at most 1 times"])
        r1 = guard.guard_before("send_newsletter", {})
        assert r1.allowed
        guard.guard_after("send_newsletter", "ok")

        r2 = guard.guard_before("send_newsletter", {})
        assert r2.blocked


class TestVercelAIGuardViolations:
    def test_violations_recorded(self):
        guard = VercelAIGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        guard.guard_before("issue_refund", {})
        assert len(guard.violations) > 0

    def test_reset_clears_violations(self):
        guard = VercelAIGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        guard.guard_before("issue_refund", {})
        assert len(guard.violations) > 0

        guard.reset()
        assert len(guard.violations) == 0

    def test_summary_with_violations(self):
        guard = VercelAIGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        guard.guard_before("issue_refund", {})
        assert "violation" in guard.summary().lower()

    def test_summary_no_violations(self):
        guard = VercelAIGuard(contracts=["tool `A` must precede `B`"])
        assert "No violations" in guard.summary()


# ---------------------------------------------------------------------------
# Middleware creation
# ---------------------------------------------------------------------------


class TestMiddleware:
    def test_middleware_raises_without_sdk(self):
        guard = VercelAIGuard(contracts=["tool `A` must precede `B`"])
        try:
            mw = guard.wrap()
            # If vercel-ai-sdk happens to be installed, middleware should
            # be a valid object
            assert mw is not None
        except ImportError as e:
            assert "vercel-ai-sdk" in str(e)


# ---------------------------------------------------------------------------
# Framework registry
# ---------------------------------------------------------------------------


class TestFrameworkRegistry:
    def test_init_vercel_ai(self):
        import sponsio

        guard = sponsio.Sponsio(
            framework="vercel_ai",
            agent_id="test_bot",
            contracts=["tool `A` must precede `B`"],
        )
        assert isinstance(guard, VercelAIGuard)
        assert guard.agent_id == "test_bot"

    def test_init_vercel_ai_hyphen(self):
        import sponsio

        guard = sponsio.Sponsio(
            framework="vercel-ai",
            agent_id="test_bot",
            contracts=["tool `A` must precede `B`"],
        )
        assert isinstance(guard, VercelAIGuard)

    def test_lazy_import(self):
        from sponsio import VercelAIGuard as VG

        guard = VG(contracts=["tool `A` must precede `B`"])
        assert guard is not None
