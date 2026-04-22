"""Tests for the rule-based NL parser improvements.

Covers: keyword rule expansion, bare tool name extraction, numeric parsing,
argument order heuristics, and the three newly-covered patterns
(arg_blacklist, scope_limit, data_intact).
"""

from __future__ import annotations

import pytest

from sponsio.generation.nl_to_contract import (
    _extract_actions,
    _extract_blacklist_patterns,
    _extract_paths,
    _match_keyword_rule,
    _parse_rate_limit_count,
    parse_nl_rule_based,
    parse_nl_unified,
)


# ---------------------------------------------------------------------------
# _extract_actions: backtick, quoted, bare snake_case, cue phrase
# ---------------------------------------------------------------------------


class TestExtractActions:
    def test_backtick(self):
        assert _extract_actions("tool `check_policy` before `refund`") == [
            "check_policy",
            "refund",
        ]

    def test_quoted(self):
        assert _extract_actions('tool "check_policy" before "refund"') == [
            "check_policy",
            "refund",
        ]

    def test_bare_snake_case(self):
        assert _extract_actions("send_email should always come after review_draft") == [
            "send_email",
            "review_draft",
        ]

    def test_cue_phrase_tool(self):
        actions = _extract_actions("tool deploy should only run once")
        assert "deploy" in actions

    def test_cue_phrase_call(self):
        actions = _extract_actions("never call delete without confirmation")
        assert "delete" in actions

    def test_cue_and_pattern(self):
        actions = _extract_actions("do not call approve and reject in the same session")
        assert "approve" in actions
        assert "reject" in actions

    def test_tool_before_command(self):
        actions = _extract_actions("bash command must not contain rm -rf")
        assert "bash" in actions

    def test_backtick_takes_priority(self):
        """When backticks are present, bare names are ignored."""
        actions = _extract_actions("tool `deploy` should only run once")
        assert actions == ["deploy"]

    def test_empty_string(self):
        assert _extract_actions("") == []

    def test_stop_words_filtered(self):
        """Common English words are not extracted as tool names."""
        actions = _extract_actions("call the function after this step")
        # "the" and "after" should not be extracted
        for w in ["the", "after", "this"]:
            assert w not in actions


# ---------------------------------------------------------------------------
# _extract_blacklist_patterns
# ---------------------------------------------------------------------------


class TestExtractBlacklistPatterns:
    def test_or_separated(self):
        result = _extract_blacklist_patterns("must not contain rm -rf or sudo")
        assert "rm -rf" in result
        assert "sudo" in result

    def test_quoted_patterns(self):
        result = _extract_blacklist_patterns("must not contain `rm -rf` or `sudo`")
        assert "rm -rf" in result
        assert "sudo" in result

    def test_comma_separated(self):
        result = _extract_blacklist_patterns("must not contain DROP TABLE, DELETE FROM")
        assert len(result) >= 2

    def test_no_match(self):
        result = _extract_blacklist_patterns("some random text")
        assert result == []


# ---------------------------------------------------------------------------
# _extract_paths
# ---------------------------------------------------------------------------


class TestExtractPaths:
    def test_backtick_paths(self):
        paths = _extract_paths("restrict to `/workspace/` only")
        assert "/workspace/" in paths

    def test_bare_paths(self):
        paths = _extract_paths("restrict file access to /workspace only")
        assert any("/workspace" in p for p in paths)

    def test_multiple_paths(self):
        paths = _extract_paths("only access `/data/` or `/workspace/`")
        assert len(paths) == 2

    def test_no_paths(self):
        paths = _extract_paths("some text without paths")
        assert paths == []


# ---------------------------------------------------------------------------
# _parse_rate_limit_count
# ---------------------------------------------------------------------------


class TestParseRateLimitCount:
    def test_at_most_n(self):
        assert _parse_rate_limit_count("at most 5 times") == 5

    def test_n_times(self):
        assert _parse_rate_limit_count("3 times per session") == 3

    def test_n_per(self):
        assert _parse_rate_limit_count("limit to 10 per session") == 10

    def test_max_n(self):
        assert _parse_rate_limit_count("maximum 7 calls") == 7

    def test_no_number(self):
        assert _parse_rate_limit_count("some text") is None


# ---------------------------------------------------------------------------
# _match_keyword_rule: new patterns
# ---------------------------------------------------------------------------


class TestKeywordRuleMatching:
    def test_arg_blacklist_keyword(self):
        result = _match_keyword_rule("command must not contain rm -rf")
        assert result is not None
        assert result[0] == "arg_blacklist"

    def test_scope_limit_keyword(self):
        result = _match_keyword_rule("restrict file access to /workspace")
        assert result is not None
        assert result[0] == "scope_limit"

    def test_data_intact_keyword(self):
        result = _match_keyword_rule("data must remain unmodified")
        assert result is not None
        assert result[0] == "data_intact"

    def test_come_after_matches_followed_by(self):
        result = _match_keyword_rule("A should always come after B")
        assert result is not None
        assert result[0] == "always_followed_by"

    def test_same_session_matches_mutual_exclusion(self):
        result = _match_keyword_rule("not in the same session")
        assert result is not None
        assert result[0] == "mutual_exclusion"

    def test_only_run_once_matches_idempotent(self):
        result = _match_keyword_rule("should only run once")
        assert result is not None
        assert result[0] == "idempotent"


# ---------------------------------------------------------------------------
# parse_nl_rule_based: end-to-end NL → ParsedConstraint
# ---------------------------------------------------------------------------


class TestParseNLRuleBased:
    """End-to-end tests for the improved rule-based parser."""

    # --- Backtick phrasings (should still work) ---

    def test_must_precede_backtick(self):
        r = parse_nl_rule_based("tool `check_policy` must precede `issue_refund`")
        assert r.ok
        assert r.pattern_name == "must_precede"

    def test_rate_limit_backtick(self):
        r = parse_nl_rule_based("tool `transfer` at most 3 times")
        assert r.ok
        assert r.pattern_name == "rate_limit"

    def test_mutual_exclusion_backtick(self):
        r = parse_nl_rule_based("tools `approve` and `reject` are mutually exclusive")
        assert r.ok
        assert r.pattern_name == "mutual_exclusion"

    # --- Bare snake_case names ---

    def test_must_precede_snake_case(self):
        r = parse_nl_rule_based("check_policy must precede issue_refund")
        assert r.ok
        assert r.pattern_name == "must_precede"

    def test_always_followed_by_snake_case(self):
        r = parse_nl_rule_based("send_email should always come after review_draft")
        assert r.ok
        assert r.pattern_name == "always_followed_by"

    def test_always_followed_by_after_calling(self):
        r = parse_nl_rule_based("after calling fetch_data, always call log_result")
        assert r.ok
        assert r.pattern_name == "always_followed_by"

    # --- Cue phrase extraction ---

    def test_idempotent_tool_cue(self):
        r = parse_nl_rule_based("tool deploy should only run once")
        assert r.ok
        assert r.pattern_name == "idempotent"

    def test_must_confirm_call_cue(self):
        r = parse_nl_rule_based("never call delete without confirmation")
        assert r.ok
        assert r.pattern_name == "must_confirm"

    def test_mutual_exclusion_call_and(self):
        r = parse_nl_rule_based("do not call approve and reject in the same session")
        assert r.ok
        assert r.pattern_name == "mutual_exclusion"

    # --- New patterns: arg_blacklist, scope_limit, data_intact ---

    def test_arg_blacklist_bare(self):
        r = parse_nl_rule_based("bash command must not contain rm -rf or sudo")
        assert r.ok
        assert r.pattern_name == "arg_blacklist"

    def test_arg_blacklist_backtick(self):
        r = parse_nl_rule_based(
            "tool `bash` command must not contain `rm -rf` or `sudo`"
        )
        assert r.ok
        assert r.pattern_name == "arg_blacklist"

    def test_scope_limit_bare_path(self):
        r = parse_nl_rule_based("restrict file access to /workspace only")
        assert r.ok
        assert r.pattern_name == "scope_limit"

    def test_scope_limit_backtick(self):
        r = parse_nl_rule_based("tool `file_ops` restricted to `/workspace/`")
        assert r.ok
        assert r.pattern_name == "scope_limit"

    # --- Argument order heuristics ---

    def test_always_followed_by_come_after_swaps_args(self):
        """'A should come after B' → always_followed_by(B, A)."""
        r = parse_nl_rule_based("send_email should always come after review_draft")
        assert r.ok
        assert r.pattern_name == "always_followed_by"
        # trigger=review_draft, response=send_email
        assert r.args[0] == "review_draft"
        assert r.args[1] == "send_email"

    def test_always_followed_by_after_calling_keeps_order(self):
        """'after calling A, always call B' → always_followed_by(A, B)."""
        r = parse_nl_rule_based("after calling fetch_data, always call log_result")
        assert r.ok
        assert r.args[0] == "fetch_data"
        assert r.args[1] == "log_result"

    def test_no_reversal_must_not_follow_swaps(self):
        """'X must not follow Y' → no_reversal(Y, X)."""
        r = parse_nl_rule_based("tool `reject` must not follow `approve`")
        assert r.ok
        assert r.pattern_name == "no_reversal"
        assert r.args[0] == "approve"
        assert r.args[1] == "reject"

    # --- Cases that legitimately need LLM (rule-based should fail gracefully) ---

    def test_pure_english_fails_gracefully(self):
        """Pure English without tool-like names should fail, not crash."""
        r = parse_nl_rule_based("verify identity is required before transferring funds")
        assert not r.ok
        assert r.pattern_name == "must_precede"
        assert r.error  # Has a meaningful error message

    def test_ambiguous_single_word_fails_gracefully(self):
        """Ambiguous single-word tool names should fail, not crash."""
        r = parse_nl_rule_based("limit queries to 10 per session")
        assert not r.ok
        assert r.pattern_name == "rate_limit"

    # --- parse_nl_unified integration ---

    def test_unified_routes_hard_correctly(self):
        r = parse_nl_unified("tool `check_policy` must precede `issue_refund`")
        assert r.is_det
        assert r.hard.pattern_name == "must_precede"

    def test_unified_pii_is_now_det(self):
        # P2 reclassification: regex-based PII is det, not sto.
        r = parse_nl_unified("response must not contain PII")
        assert r.is_det
        assert r.hard.pattern_name == "no_pii"

    def test_unified_soft_tone(self):
        r = parse_nl_unified("response must be professional")
        assert r.is_sto
        assert r.sto.category == "tone"

    def test_unified_unknown_raises_syntax_error(self):
        """Unrecognized NL without LLM translator → ContractSyntaxError.

        Previously the parser silently produced a ``category="custom"``
        sto stub here — a contract object that passed every downstream
        check but enforced nothing at runtime. Raising is the only way
        to stop silent no-ops from slipping into production.
        """
        from sponsio.generation.nl_to_contract import ContractSyntaxError

        with pytest.raises(ContractSyntaxError):
            parse_nl_unified("do something completely novel and weird")


# ---------------------------------------------------------------------------
# Coverage matrix: every pattern via NL string
# ---------------------------------------------------------------------------


class TestPatternCoverageViaNL:
    """Verify that every pattern can be reached via at least one NL phrasing."""

    @pytest.mark.parametrize(
        "nl, expected_pattern",
        [
            ("tool `A` must precede `B`", "must_precede"),
            ("`check_policy` before `issue_refund`", "must_precede"),
            ("tool `X` at most 3 times", "rate_limit"),
            ("`transfer` at most 5 times", "rate_limit"),
            ("tools `approve` and `reject` are mutually exclusive", "mutual_exclusion"),
            ("do not call approve and reject in the same session", "mutual_exclusion"),
            ("tool `approve` cannot be reversed by `reject`", "no_reversal"),
            ("tool `deploy` must be idempotent", "idempotent"),
            ("tool deploy should only run once", "idempotent"),
            ("tool `X` must be confirmed before execution", "must_confirm"),
            ("never call delete without confirmation", "must_confirm"),
            ("`write_api` cooldown of 2 steps", "cooldown"),
            ("tool `deploy_staging` at most 3 retries", "bounded_retry"),
            (
                "tools `submit` and `approve` require segregation of duty",
                "segregation_of_duty",
            ),
            ("`trigger` must be followed by `response`", "always_followed_by"),
            ("send_email should always come after review_draft", "always_followed_by"),
            ("after calling fetch_data, always call log_result", "always_followed_by"),
            ("tool `delete` requires permission `admin`", "requires_permission"),
            # no_data_leak with tool-like names routes to no_reversal (by design)
            ("no data leak from `db` to `external`", "no_reversal"),
            ("no data leak from `pii` to `external_api`", "no_data_leak"),
            ("`action` within 3 steps of `trigger`", "deadline"),
            ("bash command must not contain rm -rf or sudo", "arg_blacklist"),
            ("restrict file access to /workspace only", "scope_limit"),
        ],
    )
    def test_nl_to_pattern(self, nl, expected_pattern):
        result = parse_nl_rule_based(nl)
        assert result.ok, f"Failed to parse: {nl!r} — error: {result.error}"
        assert result.pattern_name == expected_pattern, (
            f"Expected {expected_pattern}, got {result.pattern_name} for: {nl!r}"
        )
