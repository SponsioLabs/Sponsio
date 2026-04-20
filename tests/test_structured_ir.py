"""Tests for the structured IR pipeline (sponsio.generation.structured_ir).

Tests the deterministic compilation path: ConstraintIR → compile_ir() → DetFormula/StoFormula.
No LLM calls — these are pure unit tests on the synthesis logic.
"""

from __future__ import annotations


from sponsio.generation.structured_ir import (
    ConstraintIR,
    compile_ir,
    compile_ir_batch,
    parse_ir_item,
    get_available_relations,
    build_ir_system_prompt,
    build_ir_user_content,
)
from sponsio.patterns.library import DetFormula


# ---------------------------------------------------------------------------
# ConstraintIR construction
# ---------------------------------------------------------------------------


class TestConstraintIR:
    def test_defaults(self):
        ir = ConstraintIR()
        assert ir.subject == ""
        assert ir.object is None
        assert ir.relation == ""
        assert ir.scope == "global"
        assert ir.guard is None
        assert ir.quantifier is None
        assert ir.confidence == 0.5
        assert ir.constraint_type == "det"

    def test_full_construction(self):
        ir = ConstraintIR(
            subject="check_policy",
            object="issue_refund",
            relation="precedes",
            scope="conditional",
            guard="issue_refund is called",
            nl="must check policy before issuing refund",
            confidence=0.9,
        )
        assert ir.subject == "check_policy"
        assert ir.object == "issue_refund"
        assert ir.relation == "precedes"
        assert ir.scope == "conditional"


# ---------------------------------------------------------------------------
# Deterministic compilation: ordering patterns
# ---------------------------------------------------------------------------


class TestCompileIROrdering:
    def test_precedes(self):
        ir = ConstraintIR(
            subject="check_policy",
            object="issue_refund",
            relation="precedes",
            nl="check before refund",
        )
        result = compile_ir(ir)
        assert result.ok, result.error
        assert isinstance(result.compiled, DetFormula)
        assert result.compiled.pattern_name == "must_precede"
        assert result.paraphrase  # NL paraphrase generated

    def test_follows(self):
        ir = ConstraintIR(
            subject="query_db",
            object="log_entry",
            relation="follows",
            nl="query must be followed by log",
        )
        result = compile_ir(ir)
        assert result.ok, result.error
        assert result.compiled.pattern_name == "always_followed_by"

    def test_deadlines(self):
        ir = ConstraintIR(
            subject="receive_complaint",
            object="respond",
            relation="deadlines",
            quantifier=3,
            nl="respond within 3 steps",
        )
        result = compile_ir(ir)
        assert result.ok, result.error
        assert result.compiled.pattern_name == "deadline"

    def test_deadlines_missing_quantifier(self):
        ir = ConstraintIR(
            subject="trigger",
            object="action",
            relation="deadlines",
            # quantifier missing!
        )
        result = compile_ir(ir)
        assert not result.ok
        assert "quantifier" in result.error


# ---------------------------------------------------------------------------
# Deterministic compilation: exclusion patterns
# ---------------------------------------------------------------------------


class TestCompileIRExclusion:
    def test_excludes(self):
        ir = ConstraintIR(
            subject="approve",
            object="reject",
            relation="excludes",
            nl="approve and reject are exclusive",
        )
        result = compile_ir(ir)
        assert result.ok, result.error
        assert result.compiled.pattern_name == "mutual_exclusion"

    def test_guards(self):
        ir = ConstraintIR(
            subject="approve_refund",
            object="deny_refund",
            relation="guards",
            nl="cannot deny after approving",
        )
        result = compile_ir(ir)
        assert result.ok, result.error
        assert result.compiled.pattern_name == "no_reversal"

    def test_segregates(self):
        ir = ConstraintIR(
            subject="review",
            object="approve",
            relation="segregates",
            nl="reviewer and approver must differ",
        )
        result = compile_ir(ir)
        assert result.ok, result.error
        assert result.compiled.pattern_name == "segregation_of_duty"


# ---------------------------------------------------------------------------
# Deterministic compilation: access control
# ---------------------------------------------------------------------------


class TestCompileIRAccess:
    def test_requires_permission(self):
        ir = ConstraintIR(
            subject="delete_user",
            object="admin",
            relation="requires",
            nl="delete requires admin permission",
        )
        result = compile_ir(ir)
        assert result.ok, result.error
        assert result.compiled.pattern_name == "requires_permission"

    def test_no_data_leak(self):
        ir = ConstraintIR(
            subject="ssn",
            object="external_api",
            relation="no_data_leak",
            nl="PII must not leak",
        )
        result = compile_ir(ir)
        assert result.ok, result.error
        assert result.compiled.pattern_name == "no_data_leak"


# ---------------------------------------------------------------------------
# Deterministic compilation: rate/count patterns
# ---------------------------------------------------------------------------


class TestCompileIRRate:
    def test_limits(self):
        ir = ConstraintIR(
            subject="issue_refund",
            relation="limits",
            quantifier=3,
            nl="at most 3 refunds",
        )
        result = compile_ir(ir)
        assert result.ok, result.error
        assert result.compiled.pattern_name == "rate_limit"

    def test_bans(self):
        ir = ConstraintIR(
            subject="bash:rm -rf",
            relation="bans",
            nl="rm -rf is banned",
        )
        result = compile_ir(ir)
        assert result.ok, result.error
        assert result.compiled.pattern_name == "rate_limit"
        # bans defaults quantifier to 0

    def test_idempotent(self):
        ir = ConstraintIR(
            subject="deploy",
            relation="idempotent",
            nl="deploy must be idempotent",
        )
        result = compile_ir(ir)
        assert result.ok, result.error
        assert result.compiled.pattern_name == "idempotent"

    def test_confirms(self):
        ir = ConstraintIR(
            subject="delete",
            relation="confirms",
            nl="delete requires confirmation",
        )
        result = compile_ir(ir)
        assert result.ok, result.error
        assert result.compiled.pattern_name == "must_confirm"

    def test_cools(self):
        ir = ConstraintIR(
            subject="api_call",
            relation="cools",
            quantifier=2,
            nl="2 step cooldown",
        )
        result = compile_ir(ir)
        assert result.ok, result.error
        assert result.compiled.pattern_name == "cooldown"

    def test_retries(self):
        ir = ConstraintIR(
            subject="fetch_data",
            relation="retries",
            quantifier=5,
            nl="at most 5 retries",
        )
        result = compile_ir(ir)
        assert result.ok, result.error
        assert result.compiled.pattern_name == "bounded_retry"


# ---------------------------------------------------------------------------
# Deterministic compilation: arg/path patterns
# ---------------------------------------------------------------------------


class TestCompileIRArgPath:
    def test_arg_check(self):
        ir = ConstraintIR(
            subject="bash",
            relation="arg_check",
            params={"field": "command", "patterns": ["rm -rf", "sudo"]},
            nl="bash must not use rm -rf or sudo",
        )
        result = compile_ir(ir)
        assert result.ok, result.error
        assert result.compiled.pattern_name == "arg_blacklist"

    def test_arg_check_missing_params(self):
        ir = ConstraintIR(
            subject="bash",
            relation="arg_check",
            params={},  # missing field and patterns
        )
        result = compile_ir(ir)
        assert not result.ok
        assert "params.field" in result.error

    def test_scope_check(self):
        ir = ConstraintIR(
            subject="file_write",
            relation="scope_check",
            params={"prefixes": ["/workspace/", "/tmp/"]},
            nl="file writes restricted to workspace",
        )
        result = compile_ir(ir)
        assert result.ok, result.error
        assert result.compiled.pattern_name == "scope_limit"

    def test_length_check(self):
        ir = ConstraintIR(
            subject="bash",
            relation="length_check",
            params={"field": "command"},
            quantifier=500,
            nl="bash command limited to 500 chars",
        )
        result = compile_ir(ir)
        assert result.ok, result.error
        assert result.compiled.pattern_name == "arg_length_limit"

    def test_data_intact(self):
        ir = ConstraintIR(
            subject="grep",
            relation="data_intact",
            params={"original_paths": ["/data/original/"]},
            nl="grep must only read original data",
        )
        result = compile_ir(ir)
        assert result.ok, result.error
        assert result.compiled.pattern_name == "data_intact"


# ---------------------------------------------------------------------------
# Sto constraints
# ---------------------------------------------------------------------------


class TestCompileIRSto:
    def test_pii(self):
        ir = ConstraintIR(
            constraint_type="sto",
            subject="agent",
            sto_category="pii",
            sto_params={"fields": ["ssn", "email"]},
            nl="no PII in responses",
        )
        result = compile_ir(ir)
        assert result.ok, result.error
        assert result.constraint_type == "sto"

    def test_length(self):
        ir = ConstraintIR(
            constraint_type="sto",
            subject="agent",
            sto_category="length",
            sto_params={"max_words": 200},
            nl="keep responses under 200 words",
        )
        result = compile_ir(ir)
        assert result.ok, result.error

    def test_tone(self):
        ir = ConstraintIR(
            constraint_type="sto",
            subject="agent",
            sto_category="tone",
            sto_params={"desired_tone": "professional"},
            nl="maintain professional tone",
        )
        result = compile_ir(ir)
        assert result.ok, result.error

    def test_format(self):
        ir = ConstraintIR(
            constraint_type="sto",
            subject="agent",
            sto_category="format",
            sto_params={"format": "json"},
            nl="output must be valid JSON",
        )
        result = compile_ir(ir)
        assert result.ok, result.error


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestCompileIRErrors:
    def test_unknown_relation(self):
        ir = ConstraintIR(
            subject="A",
            object="B",
            relation="nonexistent",
        )
        result = compile_ir(ir)
        assert not result.ok
        assert "Unknown relation" in result.error

    def test_missing_object(self):
        ir = ConstraintIR(
            subject="A",
            relation="precedes",
            # object missing
        )
        result = compile_ir(ir)
        assert not result.ok
        assert "object" in result.error

    def test_missing_quantifier_for_limits(self):
        ir = ConstraintIR(
            subject="A",
            relation="limits",
            # quantifier missing
        )
        result = compile_ir(ir)
        assert not result.ok
        assert "quantifier" in result.error


# ---------------------------------------------------------------------------
# NL paraphrase generation
# ---------------------------------------------------------------------------


class TestNLParaphrase:
    def test_precedes_paraphrase(self):
        ir = ConstraintIR(
            subject="check_policy",
            object="issue_refund",
            relation="precedes",
        )
        result = compile_ir(ir)
        assert result.ok
        assert result.paraphrase
        # The paraphrase should mention both tools
        assert (
            "check_policy" in result.paraphrase or "issue_refund" in result.paraphrase
        )

    def test_rate_limit_paraphrase(self):
        ir = ConstraintIR(
            subject="issue_refund",
            relation="limits",
            quantifier=3,
        )
        result = compile_ir(ir)
        assert result.ok
        assert result.paraphrase
        assert "3" in result.paraphrase or "issue_refund" in result.paraphrase


# ---------------------------------------------------------------------------
# Conditional scope / guard compilation
# ---------------------------------------------------------------------------


class TestConditionalScope:
    def test_conditional_with_guard_formula(self):
        ir = ConstraintIR(
            subject="check_policy",
            object="issue_refund",
            relation="precedes",
            scope="conditional",
            guard="called(issue_refund)",
            nl="check before refund (only if refunding)",
        )
        result = compile_ir(ir)
        assert result.ok
        assert result.compiled_assumption is not None

    def test_conditional_with_nl_guard(self):
        ir = ConstraintIR(
            subject="check_policy",
            object="issue_refund",
            relation="precedes",
            scope="conditional",
            guard="issue_refund is called",
            nl="check before refund",
        )
        result = compile_ir(ir)
        assert result.ok
        # Should fall back to creating assumption from object name
        assert result.compiled_assumption is not None

    def test_global_scope_no_assumption(self):
        ir = ConstraintIR(
            subject="bash:rm -rf",
            relation="bans",
            scope="global",
            nl="rm -rf always banned",
        )
        result = compile_ir(ir)
        assert result.ok
        assert result.compiled_assumption is None


# ---------------------------------------------------------------------------
# JSON parsing (parse_ir_item)
# ---------------------------------------------------------------------------


class TestParseIRItem:
    def test_basic_det(self):
        item = {
            "type": "det",
            "subject": "check_policy",
            "object": "issue_refund",
            "relation": "precedes",
            "scope": "global",
            "nl": "check before refund",
            "confidence": 0.9,
        }
        ir = parse_ir_item(item)
        assert ir.subject == "check_policy"
        assert ir.object == "issue_refund"
        assert ir.relation == "precedes"
        assert ir.confidence == 0.9
        assert ir.constraint_type == "det"

    def test_sto_item(self):
        item = {
            "type": "sto",
            "subject": "agent",
            "sto_category": "pii",
            "sto_params": {"fields": ["ssn"]},
            "nl": "no PII",
            "confidence": 0.8,
        }
        ir = parse_ir_item(item)
        assert ir.constraint_type == "sto"
        assert ir.sto_category == "pii"

    def test_with_params(self):
        item = {
            "type": "det",
            "subject": "bash",
            "relation": "arg_check",
            "params": {"field": "command", "patterns": ["rm -rf"]},
            "nl": "no rm -rf",
        }
        ir = parse_ir_item(item)
        assert ir.params == {"field": "command", "patterns": ["rm -rf"]}

    def test_quantifier_as_string(self):
        """LLMs sometimes return numbers as strings."""
        item = {
            "type": "det",
            "subject": "api_call",
            "relation": "limits",
            "quantifier": "5",
            "nl": "at most 5 calls",
        }
        ir = parse_ir_item(item)
        assert ir.quantifier == 5

    def test_missing_fields_get_defaults(self):
        item = {}
        ir = parse_ir_item(item)
        assert ir.subject == ""
        assert ir.object is None
        assert ir.confidence == 0.5
        assert ir.scope == "global"


# ---------------------------------------------------------------------------
# Batch compilation
# ---------------------------------------------------------------------------


class TestCompileIRBatch:
    def test_batch(self):
        items = [
            {
                "type": "det",
                "subject": "check_policy",
                "object": "issue_refund",
                "relation": "precedes",
                "nl": "check first",
                "confidence": 0.9,
            },
            {
                "type": "det",
                "subject": "bash:rm -rf",
                "relation": "bans",
                "nl": "no rm",
                "confidence": 0.95,
            },
            {
                "type": "sto",
                "subject": "agent",
                "sto_category": "pii",
                "sto_params": {},
                "nl": "no pii",
                "confidence": 0.8,
            },
        ]
        results = compile_ir_batch(items)
        assert len(results) == 3
        assert all(r.ok for r in results), [r.error for r in results]

    def test_batch_min_confidence(self):
        items = [
            {
                "type": "det",
                "subject": "A",
                "object": "B",
                "relation": "precedes",
                "confidence": 0.9,
            },
            {
                "type": "det",
                "subject": "C",
                "object": "D",
                "relation": "precedes",
                "confidence": 0.3,
            },
        ]
        results = compile_ir_batch(items, min_confidence=0.5)
        assert len(results) == 1

    def test_batch_with_errors(self):
        items = [
            {"type": "det", "subject": "A", "relation": "precedes"},  # missing object
            {
                "type": "det",
                "subject": "B",
                "object": "C",
                "relation": "precedes",
                "confidence": 0.8,
            },
        ]
        results = compile_ir_batch(items)
        assert len(results) == 2
        assert not results[0].ok
        assert results[1].ok


# ---------------------------------------------------------------------------
# IRCompilationResult properties
# ---------------------------------------------------------------------------


class TestIRCompilationResultProps:
    def test_properties(self):
        ir = ConstraintIR(
            subject="A",
            object="B",
            relation="precedes",
            nl="A before B",
            source_quote="must do A first",
            confidence=0.85,
        )
        result = compile_ir(ir)
        assert result.constraint_type == "det"
        assert result.confidence == 0.85
        assert result.nl_description == "A before B"
        assert result.source_quote == "must do A first"
        assert result.pattern_name == "precedes"
        assert result.args == ["A", "B"]


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------


class TestPromptBuilders:
    def test_system_prompt_nl(self):
        prompt = build_ir_system_prompt("nl")
        assert "relation" in prompt
        assert "precedes" in prompt
        # IR prompt should NOT expose LTL operator syntax (G, F, U, X, Implies)
        # as formula-building instructions
        assert "G(Implies(" not in prompt
        assert "U(Not(" not in prompt

    def test_system_prompt_code_with_tools(self):
        tools = [
            {"name": "check_policy", "docstring": "Check refund eligibility"},
            {"name": "issue_refund", "docstring": "Process refund"},
        ]
        prompt = build_ir_system_prompt("code", tool_inventory=tools)
        assert "check_policy" in prompt
        assert "issue_refund" in prompt

    def test_user_content_document(self):
        content = build_ir_user_content("document", "All refunds require approval.")
        assert "All refunds require approval." in content

    def test_user_content_code_with_inventory(self):
        tools = [
            {
                "name": "my_tool",
                "docstring": "does stuff",
                "source": "def my_tool(): pass",
            }
        ]
        content = build_ir_user_content(
            "code", "# main agent code", tool_inventory=tools, source_files=["# file 1"]
        )
        assert "my_tool" in content
        assert "# file 1" in content


# ---------------------------------------------------------------------------
# Available relations registry
# ---------------------------------------------------------------------------


class TestAvailableRelations:
    def test_all_relations_have_pattern(self):
        relations = get_available_relations()
        assert len(relations) >= 16  # at least as many as patterns
        for rel, pattern in relations.items():
            assert pattern, f"Relation '{rel}' has no pattern"

    def test_coverage_of_pattern_registry(self):
        """Every pattern in _PATTERN_REGISTRY should be reachable via at least one IR relation."""
        from sponsio.generation.nl_to_contract import _PATTERN_REGISTRY

        reachable = set(get_available_relations().values())
        # These patterns are covered by IR relations
        # never_together is deprecated → skip
        # data_intact is covered
        for name in _PATTERN_REGISTRY:
            if name == "never_together":
                continue  # deprecated
            assert name in reachable, (
                f"Pattern '{name}' is not reachable via any IR relation. "
                f"Add an IR relation that maps to it."
            )


# ---------------------------------------------------------------------------
# tool:pattern format (bash:sed -i style)
# ---------------------------------------------------------------------------


class TestToolPatternFormat:
    def test_bans_with_tool_pattern(self):
        ir = ConstraintIR(
            subject="bash:sed -i",
            relation="bans",
            nl="sed -i is banned",
        )
        result = compile_ir(ir)
        assert result.ok, result.error
        # The formula should use called_with atom
        formula_str = str(result.compiled.formula)
        assert "called_with" in formula_str or "count_with" in formula_str

    def test_limits_with_tool_pattern(self):
        ir = ConstraintIR(
            subject="bash:python -c",
            relation="limits",
            quantifier=1,
            nl="python -c at most once",
        )
        result = compile_ir(ir)
        assert result.ok, result.error
