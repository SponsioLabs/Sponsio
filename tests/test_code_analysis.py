"""Tests for sponsio/discovery/extractors/code_analysis.py."""

from sponsio.discovery.extractors.code_analysis import CodeAnalyzer


def _patterns(results) -> list[str]:
    return [r.formula.pattern_name for r in results if r.formula]


def _call_graph_only(results) -> list:
    """Filter to results produced by the call-graph pass (not heuristics)."""
    return [r for r in results if r.extractor == "code_analysis"]


class TestDecoratedTools:
    def test_finds_tool_decorator(self):
        source = '''
from langchain_core.tools import tool

@tool
def check_policy(order_id: str) -> str:
    """Check refund policy."""
    return "eligible"

@tool
def issue_refund(order_id: str) -> str:
    """Issue refund."""
    return "done"
'''
        analyzer = CodeAnalyzer()
        results = analyzer.extract_from_source(source)
        # Call graph between them is empty -> no call-graph constraints.
        # (Heuristics may still fire on `issue_refund` -> idempotent.)
        assert _call_graph_only(results) == []

    def test_finds_function_tool_decorator(self):
        source = """
from agents import function_tool

@function_tool
def my_tool():
    return "ok"
"""
        analyzer = CodeAnalyzer()
        results = analyzer.extract_from_source(source)
        # Single tool, no naming heuristics fire either.
        assert results == []


class TestCallGraph:
    def test_discovers_ordering_from_calls(self):
        source = """
from langchain_core.tools import tool

@tool
def validate_input(data: str) -> str:
    return "valid"

@tool
def process_order(data: str) -> str:
    validate_input(data)
    return "processed"
"""
        analyzer = CodeAnalyzer()
        results = analyzer.extract_from_source(source)
        cg = _call_graph_only(results)
        assert len(cg) == 1
        r = cg[0]
        assert r.formula.pattern_name == "must_precede"
        assert r.confidence == 0.7
        assert "validate_input" in r.nl_description

    def test_multiple_dependencies(self):
        source = """
from langchain_core.tools import tool

@tool
def auth():
    return "ok"

@tool
def validate():
    return "ok"

@tool
def execute():
    auth()
    validate()
    return "done"
"""
        analyzer = CodeAnalyzer()
        results = analyzer.extract_from_source(source)
        cg = _call_graph_only(results)
        assert len(cg) == 2
        names = {r.evidence["callee"] for r in cg}
        assert names == {"auth", "validate"}


class TestAgentTools:
    def test_finds_agent_constructor_tools(self):
        source = """
from sponsio.models.agent import Agent

agent = Agent(
    id="bot",
    tools=["check_policy", "issue_refund"],
)
"""
        analyzer = CodeAnalyzer()
        results = analyzer.extract_from_source(source)
        # String literals in tools list — discovered but the call graph
        # alone yields no constraints. (Heuristics may still propose
        # safety contracts based on the names; that's covered elsewhere.)
        assert len(_call_graph_only(results)) == 0

    def test_agent_constructor_resolves_local_function_tools(self, tmp_path):
        source = '''
from google.adk.agents.llm_agent import Agent

def search_flights(origin: str, destination: str) -> str:
    """Search available flights."""
    return "found"

root_agent = Agent(
    name="travel",
    model="gemini-flash-latest",
    tools=[search_flights],
)
'''
        analyzer = CodeAnalyzer()
        f = tmp_path / "agent.py"
        f.write_text(source)
        inventory = analyzer.get_tool_inventory([str(f)])
        tool = next(t for t in inventory if t["name"] == "search_flights")
        assert tool["docstring"] == "Search available flights."
        assert tool["params"] == "origin: str, destination: str"


class TestEdgeCases:
    def test_syntax_error_returns_empty(self):
        analyzer = CodeAnalyzer()
        results = analyzer.extract_from_source("def broken(:")
        assert results == []

    def test_empty_source(self):
        analyzer = CodeAnalyzer()
        results = analyzer.extract_from_source("")
        assert results == []

    def test_provenance_includes_file(self):
        source = """
from langchain_core.tools import tool

@tool
def a():
    return "ok"

@tool
def b():
    a()
    return "ok"
"""
        analyzer = CodeAnalyzer()
        results = analyzer.extract_from_source(source, filename="agents/bot.py")
        cg = _call_graph_only(results)
        assert len(cg) == 1
        assert "agents/bot.py" in cg[0].provenance


class TestHeuristicConstraints:
    """Heuristic pass: naming/docstring patterns -> ProposedConstraints.

    These tests exist so AST-only mode (no --llm) is not silently empty
    on realistic LLM-dispatched agents.
    """

    def test_antonym_pair_yields_mutual_exclusion(self):
        source = '''
from langchain_core.tools import tool

@tool
def approve_refund(order_id: str) -> str:
    """Approve a refund."""
    return "ok"

@tool
def reject_refund(order_id: str) -> str:
    """Reject a refund."""
    return "no"
'''
        results = CodeAnalyzer().extract_from_source(source)
        patterns = _patterns(results)
        assert "mutual_exclusion" in patterns
        mutex = next(r for r in results if r.formula.pattern_name == "mutual_exclusion")
        assert sorted(mutex.evidence["args"]) == ["approve_refund", "reject_refund"]
        assert mutex.confidence == 0.6
        assert mutex.extractor == "code_analysis_heuristic"

    def test_antonym_pair_only_when_suffix_matches(self):
        source = """
from langchain_core.tools import tool

@tool
def approve_refund():
    return "ok"

@tool
def reject_user():
    return "no"
"""
        results = CodeAnalyzer().extract_from_source(source)
        assert "mutual_exclusion" not in _patterns(results)

    def test_confirm_plus_destructive_yields_must_precede(self):
        source = '''
from langchain_core.tools import tool

@tool
def confirm_action(payload: str) -> str:
    """Ask the user to confirm before proceeding."""
    return "ok"

@tool
def delete_record(record_id: str) -> str:
    """DESTRUCTIVE: permanently remove a record."""
    return "gone"
'''
        results = CodeAnalyzer().extract_from_source(source)
        precedes = [
            r for r in results if r.formula and r.formula.pattern_name == "must_precede"
        ]
        assert any(
            r.evidence.get("args") == ["confirm_action", "delete_record"]
            and r.evidence.get("heuristic") == "confirm_precedes_destructive"
            for r in precedes
        )

    def test_sensitive_to_broadcast_yields_no_data_leak(self):
        # Sensitive read paired with a *broadcast* sink (slack channel,
        # webhook, public publish) — this is the only shape that
        # qualifies after the precision tightening.
        source = '''
from langchain_core.tools import tool

@tool
def get_user_profile(uid: str) -> dict:
    """Fetch the user profile from the customer database."""
    return {}

@tool
def post_to_slack(channel: str, message: str) -> bool:
    """Post a message to a Slack channel."""
    return True
'''
        results = CodeAnalyzer().extract_from_source(source)
        leak = [
            r for r in results if r.formula and r.formula.pattern_name == "no_data_leak"
        ]
        assert leak, "expected no_data_leak between sensitive read and broadcast sink"
        assert leak[0].evidence["args"] == ["get_user_profile", "post_to_slack"]
        assert leak[0].confidence < 0.5

    def test_point_to_point_send_does_not_yield_no_data_leak(self):
        # PRECISION test: a generic point-to-point send (``to`` parameter,
        # no broadcast verb in the name) is the *workflow* of every
        # support / CRM / notification agent.  Flagging it generates
        # noise; the heuristic must NOT fire here.
        source = '''
from langchain_core.tools import tool

@tool
def lookup_customer(email: str) -> dict:
    """Look up a customer by email."""
    return {}

@tool
def send_email(to: str, body: str) -> bool:
    """Send a transactional email to a recipient."""
    return True
'''
        results = CodeAnalyzer().extract_from_source(source)
        leak = [
            r for r in results if r.formula and r.formula.pattern_name == "no_data_leak"
        ]
        assert not leak, (
            "no_data_leak should not fire on point-to-point sends "
            "(would flag the entire support-agent workflow)"
        )

    def test_financial_tool_yields_idempotent(self):
        source = '''
from langchain_core.tools import tool

@tool
def issue_refund(order_id: str, amount: float) -> str:
    """Issue a refund for an order."""
    return "refunded"
'''
        results = CodeAnalyzer().extract_from_source(source)
        idem = [
            r for r in results if r.formula and r.formula.pattern_name == "idempotent"
        ]
        assert idem, "expected idempotent suggestion for financial tool"
        assert idem[0].evidence["args"] == ["issue_refund"]

    def test_destructive_tool_yields_idempotent(self):
        """Structural-destruction verbs (delete/drop/wipe/terminate/...)
        should trigger idempotent independently of the financial path."""
        source = '''
from langchain_core.tools import tool

@tool
def delete_user(user_id: str) -> str:
    """Permanently remove a user account."""
    return "deleted"

@tool
def terminate_instance(instance_id: str) -> str:
    """Terminate a running VM instance."""
    return "terminated"

@tool
def dropTable(table: str) -> str:
    """Drop a database table."""
    return "dropped"
'''
        results = CodeAnalyzer().extract_from_source(source)
        idem = [
            r for r in results if r.formula and r.formula.pattern_name == "idempotent"
        ]
        tool_names = {r.evidence["args"][0] for r in idem}
        assert {"delete_user", "terminate_instance", "dropTable"}.issubset(tool_names)
        # Each destructive-only hit should be attributed to the new
        # heuristic, not the financial one.
        destructive_hits = [
            r for r in idem if r.evidence.get("heuristic") == "destructive_idempotent"
        ]
        destructive_names = {r.evidence["args"][0] for r in destructive_hits}
        assert {"delete_user", "terminate_instance", "dropTable"}.issubset(
            destructive_names
        )

    def test_destructive_financial_prefers_financial_label(self):
        """When a verb is BOTH destructive and financial (``transfer``),
        the provenance should read ``financial_idempotent`` — the more
        specific label — and not get duplicated."""
        source = '''
from langchain_core.tools import tool

@tool
def transfer_funds(source: str, destination: str, amount: float) -> str:
    """Transfer funds between accounts."""
    return "done"
'''
        results = CodeAnalyzer().extract_from_source(source)
        idem = [
            r
            for r in results
            if r.formula
            and r.formula.pattern_name == "idempotent"
            and r.evidence["args"] == ["transfer_funds"]
        ]
        assert len(idem) == 1, (
            f"expected exactly one idempotent contract, got {len(idem)}"
        )
        assert idem[0].evidence["heuristic"] == "financial_idempotent"

    def test_non_destructive_non_financial_no_idempotent(self):
        """Read-only tools should not trigger idempotent at all."""
        source = '''
from langchain_core.tools import tool

@tool
def get_order_status(order_id: str) -> str:
    """Fetch the status of an order."""
    return "shipped"
'''
        results = CodeAnalyzer().extract_from_source(source)
        idem = [
            r for r in results if r.formula and r.formula.pattern_name == "idempotent"
        ]
        assert idem == []

    def test_no_tools_means_no_heuristic_results(self):
        results = CodeAnalyzer().extract_from_source("# nothing here\n")
        assert results == []

    def test_yaml_emitter_consumes_heuristic_args(self, tmp_path):
        """End-to-end: heuristic proposals must round-trip through generate_yaml."""
        src_dir = tmp_path / "agent"
        src_dir.mkdir()
        (src_dir / "tools.py").write_text(
            '''
from langchain_core.tools import tool

@tool
def approve_refund(order_id: str) -> str:
    """Approve."""
    return "ok"

@tool
def reject_refund(order_id: str) -> str:
    """Reject."""
    return "no"
'''
        )
        yaml_text = CodeAnalyzer().generate_yaml([str(src_dir)], agent_id="bot")
        assert "pattern: mutual_exclusion" in yaml_text
        assert "args: [approve_refund, reject_refund]" in yaml_text
        # Confidence comment should appear because 0.6 < 0.9
        assert "confidence: 0.60" in yaml_text


class TestAssumptionEmission:
    """The YAML emitter must render `A:` when proposal.assumption is set.

    This guards against the cross-extractor schema inconsistency where
    LLM extractor produces conditional rules but the emitter silently
    drops the assumption.
    """

    def _make_proposal_with_assumption(self):
        from sponsio.discovery._types import (
            ConstraintStatus,
            DiscoverySource,
            ProposedConstraint,
        )
        from sponsio.formulas.parser import parse_formula
        from sponsio.patterns.library import DetFormula, must_precede

        guarantee = must_precede("get_order_details", "modify_order")
        assumption_ast = parse_formula("called(modify_order)")
        assumption = DetFormula(
            formula=assumption_ast,
            desc="assumes: modify_order is called",
            pattern_name="assumption",
        )
        return ProposedConstraint(
            formula=guarantee,
            assumption=assumption,
            source=DiscoverySource.AUTO_EXTRACTED,
            extractor="code_analysis_llm",
            confidence=0.85,
            status=ConstraintStatus.PROPOSED,
            provenance="LLM",
            nl_description="get_order_details must precede modify_order",
            evidence={
                "pattern": "must_precede",
                "args": ["get_order_details", "modify_order"],
                "assumption_raw": "called(modify_order)",
            },
        )

    def test_yaml_emits_A_field_when_assumption_present(self, monkeypatch):
        proposal = self._make_proposal_with_assumption()
        analyzer = CodeAnalyzer()
        # Bypass the AST scan; we only care about generate_yaml's emitter.
        monkeypatch.setattr(analyzer, "extract", lambda paths: [proposal])
        monkeypatch.setattr(analyzer, "get_tool_inventory", lambda paths: [])
        yaml_text = analyzer.generate_yaml([], agent_id="bot")
        assert '- A: "called(modify_order)"' in yaml_text
        assert "E:" in yaml_text
        assert "pattern: must_precede" in yaml_text
        assert "args: [get_order_details, modify_order]" in yaml_text

    def test_yaml_round_trips_through_loader(self, monkeypatch, tmp_path):
        # Skip if PyYAML isn't installed (it's an optional sponsio[config] dep).
        pytest = __import__("pytest")
        try:
            import yaml  # noqa: F401
        except ImportError:
            pytest.skip("PyYAML not installed")

        from sponsio.config import load_config

        proposal = self._make_proposal_with_assumption()
        analyzer = CodeAnalyzer()
        monkeypatch.setattr(analyzer, "extract", lambda paths: [proposal])
        monkeypatch.setattr(analyzer, "get_tool_inventory", lambda paths: [])
        yaml_text = analyzer.generate_yaml([], agent_id="bot")

        out = tmp_path / "sponsio.yaml"
        out.write_text(yaml_text)
        cfg = load_config(out)
        contracts = cfg.agents["bot"].contracts
        assert len(contracts) == 1
        c = contracts[0]
        # Both fields populated — assumption was not dropped.
        assert c.assumption is not None
        assert c.enforcement is not None

    def test_assumption_emission_falls_back_to_desc(self, monkeypatch):
        # Simulate a proposal where evidence["assumption_raw"] is missing
        # (e.g. older LLM extractor or hand-built constraint). The emitter
        # must still produce a usable A:.
        proposal = self._make_proposal_with_assumption()
        proposal.evidence.pop("assumption_raw", None)
        analyzer = CodeAnalyzer()
        monkeypatch.setattr(analyzer, "extract", lambda paths: [proposal])
        monkeypatch.setattr(analyzer, "get_tool_inventory", lambda paths: [])
        yaml_text = analyzer.generate_yaml([], agent_id="bot")
        # `desc` was "assumes: modify_order is called" -> stripped prefix.
        assert '- A: "modify_order is called"' in yaml_text

    def test_dedup_key_treats_different_assumptions_as_distinct(self):
        from sponsio.discovery._types import ProposedConstraint
        from sponsio.formulas.parser import parse_formula
        from sponsio.patterns.library import DetFormula, must_precede

        guarantee = must_precede("get_user", "delete_user")
        a1 = DetFormula(
            formula=parse_formula("called(delete_user)"),
            desc="assumes: delete_user",
            pattern_name="assumption",
        )
        a2 = DetFormula(
            formula=parse_formula("called(modify_user)"),
            desc="assumes: modify_user",
            pattern_name="assumption",
        )
        p_unconditional = ProposedConstraint(formula=guarantee)
        p_a1 = ProposedConstraint(formula=guarantee, assumption=a1)
        p_a2 = ProposedConstraint(formula=guarantee, assumption=a2)

        keys = {
            CodeAnalyzer._dedup_key(p_unconditional),
            CodeAnalyzer._dedup_key(p_a1),
            CodeAnalyzer._dedup_key(p_a2),
        }
        assert len(keys) == 3, "different assumptions must produce distinct keys"


class TestParamShapeHeuristics:
    """Generic safety contracts derived from param-name conventions.

    These are framework-agnostic in the sense that every Python
    decorator-style tool exposes its params; bare-string-name tools
    skip these silently.
    """

    def test_text_input_yields_arg_length_limit(self):
        source = '''
from langchain_core.tools import tool

@tool
def web_search(query: str) -> str:
    """Search the web."""
    return ""
'''
        results = CodeAnalyzer().extract_from_source(source)
        length_limits = [
            r
            for r in results
            if r.formula and r.formula.pattern_name == "arg_length_limit"
        ]
        assert length_limits, "expected arg_length_limit on text param"
        ev = length_limits[0].evidence
        assert ev["args"][:2] == ["web_search", "query"]
        assert isinstance(ev["args"][2], int) and ev["args"][2] >= 1000

    def test_arg_length_limit_uses_per_role_caps(self):
        # The cap must reflect the param's semantic role, not a single
        # magic number.  ``query`` should be tight (defends against
        # prompt-stuffing); ``body`` should be loose (real emails are
        # huge, false positives train users to ignore the contract).
        source = """
from langchain_core.tools import tool

@tool
def search_kb(query: str) -> list: return []

@tool
def send_email(to: str, body: str) -> bool: return True

@tool
def chat(prompt: str) -> str: return ""
"""
        results = CodeAnalyzer().extract_from_source(source)
        caps_by_param = {
            (r.evidence["args"][0], r.evidence["args"][1]): r.evidence["args"][2]
            for r in results
            if r.formula and r.formula.pattern_name == "arg_length_limit"
        }
        assert caps_by_param[("search_kb", "query")] == 1_000
        assert caps_by_param[("send_email", "body")] == 100_000
        assert caps_by_param[("chat", "prompt")] == 50_000

    def test_skip_non_string_text_param(self):
        source = '''
from langchain_core.tools import tool

@tool
def paginate(query: int) -> str:
    """Page index."""
    return ""
'''
        results = CodeAnalyzer().extract_from_source(source)
        assert not [
            r
            for r in results
            if r.formula and r.formula.pattern_name == "arg_length_limit"
        ]

    def test_command_param_yields_arg_blacklist(self):
        source = '''
from langchain_core.tools import tool

@tool
def run_shell(command: str) -> str:
    """Run a shell command."""
    return ""
'''
        results = CodeAnalyzer().extract_from_source(source)
        bl = [
            r
            for r in results
            if r.formula
            and r.formula.pattern_name == "arg_blacklist"
            and r.evidence.get("heuristic") == "command_blacklist"
        ]
        assert bl, "expected arg_blacklist for shell command param"
        args = bl[0].evidence["args"]
        assert args[:2] == ["run_shell", "command"]
        assert isinstance(args[2], list) and any("rm" in p for p in args[2])

    def test_path_param_yields_arg_blacklist(self):
        source = '''
from langchain_core.tools import tool

@tool
def read_file(path: str) -> str:
    """Read a file."""
    return ""
'''
        results = CodeAnalyzer().extract_from_source(source)
        bl = [
            r
            for r in results
            if r.formula
            and r.formula.pattern_name == "arg_blacklist"
            and r.evidence.get("heuristic") == "path_blacklist"
        ]
        assert bl, "expected arg_blacklist for path param"

    def test_url_param_yields_ssrf_blacklist(self):
        source = '''
from langchain_core.tools import tool

@tool
def http_fetch(url: str) -> str:
    """Fetch a URL."""
    return ""
'''
        results = CodeAnalyzer().extract_from_source(source)
        bl = [
            r
            for r in results
            if r.formula
            and r.formula.pattern_name == "arg_blacklist"
            and r.evidence.get("heuristic") == "url_blacklist"
        ]
        assert bl, "expected arg_blacklist for url param"
        patterns = bl[0].evidence["args"][2]
        # SSRF defenses
        assert any("127" in p for p in patterns)
        assert any("file://" in p for p in patterns)

    def test_sql_blacklist_only_fires_on_db_tools(self):
        # `query: str` on a generic search tool must NOT trigger the
        # SQL blacklist (high false-positive risk).
        source_search = '''
from langchain_core.tools import tool

@tool
def fuzzy_search(query: str) -> list:
    """Fuzzy search documents."""
    return []
'''
        results = CodeAnalyzer().extract_from_source(source_search)
        assert not [
            r
            for r in results
            if r.formula
            and r.formula.pattern_name == "arg_blacklist"
            and r.evidence.get("heuristic") == "sql_blacklist"
        ]

        source_db = '''
from langchain_core.tools import tool

@tool
def execute_sql(sql: str) -> list:
    """Run a SQL statement against the postgres database."""
    return []
'''
        results = CodeAnalyzer().extract_from_source(source_db)
        bl = [
            r
            for r in results
            if r.formula
            and r.formula.pattern_name == "arg_blacklist"
            and r.evidence.get("heuristic") == "sql_blacklist"
        ]
        assert bl, "expected SQL blacklist on a clearly DB-shaped tool"

    def test_bare_name_tools_skip_param_heuristics_silently(self):
        # `Agent(tools=["x"])` registration has no params info — the
        # name-based heuristics still apply, but param-shape ones don't.
        source = """
from sponsio.models.agent import Agent

agent = Agent(id="bot", tools=["approve_x", "reject_x", "fetch_url"])
"""
        results = CodeAnalyzer().extract_from_source(source)
        # Antonym pair still fires (name-based)
        assert any(
            r.formula and r.formula.pattern_name == "mutual_exclusion" for r in results
        )
        # No arg_blacklist / arg_length_limit (no params discovered)
        assert not any(
            r.formula
            and r.formula.pattern_name in {"arg_blacklist", "arg_length_limit"}
            for r in results
        )

    def test_yaml_round_trips_nested_regex_args(self, tmp_path):
        # Generators that produce blacklist regex lists must survive
        # YAML serialization and reload back into a structured ConstraintEntry.
        try:
            import yaml  # noqa: F401
        except ImportError:
            __import__("pytest").skip("PyYAML not installed")

        from sponsio.config import load_config

        src_dir = tmp_path / "agent"
        src_dir.mkdir()
        (src_dir / "tools.py").write_text(
            '''
from langchain_core.tools import tool

@tool
def run_shell(command: str) -> str:
    """Run a shell command."""
    return ""
'''
        )
        yaml_text = CodeAnalyzer().generate_yaml([str(src_dir)], agent_id="bot")
        out = tmp_path / "sponsio.yaml"
        out.write_text(yaml_text)

        cfg = load_config(out)
        contracts = cfg.agents["bot"].contracts
        bl = [
            c
            for c in contracts
            if c.enforcement
            and getattr(c.enforcement, "pattern", "") == "arg_blacklist"
        ]
        assert bl, "round-trip lost the arg_blacklist contract"
        # The third arg must be a list of regex strings, not a string
        # representation of a list.
        third = bl[0].enforcement.args[2]
        assert isinstance(third, list)
        assert all(isinstance(p, str) for p in third)
        assert any("rm" in p for p in third)


class TestNameTokenization:
    """Regression: ``\\b`` treats ``_`` as a word character AND there's
    no boundary inside ``deleteUser``, so heuristic regexes like
    ``\\brefund\\b`` would not match ``issue_refund`` *or*
    ``issueRefund`` without explicit tokenization.  Without this, every
    snake_case / camelCase / PascalCase tool whose docstring doesn't
    restate the verb silently slips past name-based heuristics —
    which is the common case in real-world agents (terse / missing
    docstrings) and is the dominant convention in TypeScript ports.
    """

    def test_unit_tokenize_name(self):
        from sponsio.discovery.extractors.code_analysis import _tokenize_name

        assert _tokenize_name("issue_refund") == "issue refund"
        assert _tokenize_name("deleteUser") == "delete User"
        assert _tokenize_name("DeleteUser") == "Delete User"
        assert _tokenize_name("HTTPRequestParser") == "HTTP Request Parser"
        assert _tokenize_name("post_to_Slack") == "post to Slack"
        assert _tokenize_name("simple") == "simple"

    def test_camel_case_destructive_pairs_with_confirm(self):
        # Both names camelCase, no docstrings — a TS-port shape.
        source = """
from langchain_core.tools import tool

@tool
def confirmAction(actionId: str) -> bool: return True

@tool
def deleteUser(userId: str) -> bool: return True
"""
        results = CodeAnalyzer().extract_from_source(source)
        names = _patterns(results)
        assert "must_precede" in names

    def test_pascal_case_financial_yields_idempotent(self):
        source = """
from langchain_core.tools import tool

@tool
def TransferFunds(amount: float, toAccount: str) -> bool: return True
"""
        results = CodeAnalyzer().extract_from_source(source)
        names = _patterns(results)
        assert "idempotent" in names


class TestSnakeCaseBoundary:
    """Regression: ``\\b`` treats ``_`` as a word character, so heuristic
    regexes like ``\\brefund\\b`` won't match ``issue_refund`` unless we
    tokenize the tool name on underscores.  Without this, every
    snake_case Python tool whose docstring doesn't restate the verb
    silently slips past name-based heuristics — which is the common
    case in real-world agents (terse / missing docstrings).
    """

    def test_destructive_name_alone_triggers_when_paired(self):
        # Confirm + destructive both snake_case, no docstrings.
        source = """
from langchain_core.tools import tool

@tool
def confirm_action(action_id: str) -> bool: return True

@tool
def delete_user(user_id: str) -> bool: return True
"""
        results = CodeAnalyzer().extract_from_source(source)
        names = _patterns(results)
        assert "must_precede" in names

    def test_financial_name_alone_triggers_idempotent(self):
        source = """
from langchain_core.tools import tool

@tool
def transfer_funds(amount: float, to_account: str) -> bool: return True
"""
        results = CodeAnalyzer().extract_from_source(source)
        names = _patterns(results)
        assert "idempotent" in names

    def test_sensitive_to_external_no_docstrings(self):
        source = """
from langchain_core.tools import tool

@tool
def fetch_user_profile(user_id: str) -> dict: return {}

@tool
def post_to_slack(channel: str, message: str) -> bool: return True
"""
        results = CodeAnalyzer().extract_from_source(source)
        names = _patterns(results)
        assert "no_data_leak" in names


class TestGeneratorPipeline:
    def test_generators_list_is_introspectable(self):
        # The pipeline metadata is part of the public-ish surface — adding
        # a new generator without updating _GENERATORS would silently no-op.
        names = CodeAnalyzer._GENERATORS
        assert "_gen_call_graph" in names
        assert "_gen_text_input_length_limit" in names
        # Every name resolves to a real method
        for n in names:
            assert callable(getattr(CodeAnalyzer, n))


class TestVerboseMode:
    def test_progress_callback_invoked_on_extract(self, tmp_path):
        src = tmp_path / "a.py"
        src.write_text(
            """
from langchain_core.tools import tool

@tool
def approve_x():
    return "ok"

@tool
def reject_x():
    return "no"
"""
        )
        messages: list[str] = []
        analyzer = CodeAnalyzer(progress=messages.append)
        analyzer.extract([str(src)])
        joined = "\n".join(messages)
        assert "AST scan:" in joined
        assert "tool(s)" in joined
        assert "contract(s)" in joined

    def test_progress_silent_by_default(self, tmp_path, capsys):
        src = tmp_path / "a.py"
        src.write_text("x = 1\n")
        CodeAnalyzer().extract([str(src)])
        captured = capsys.readouterr()
        assert captured.err == ""
        assert captured.out == ""
