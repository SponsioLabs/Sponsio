"""Tests for sponsio/discovery/extractors/code_analysis.py."""

from sponsio.discovery.extractors.code_analysis import CodeAnalyzer


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
        # No call graph between them, so no constraints
        # But tools should be discovered
        assert len(results) == 0  # no inter-tool calls

    def test_finds_function_tool_decorator(self):
        source = """
from agents import function_tool

@function_tool
def my_tool():
    return "ok"
"""
        analyzer = CodeAnalyzer()
        results = analyzer.extract_from_source(source)
        assert len(results) == 0  # single tool, no dependencies


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
        assert len(results) == 1
        r = results[0]
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
        assert len(results) == 2
        names = {r.evidence["callee"] for r in results}
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
        # String literals in tools list — discovered but no call graph
        assert len(results) == 0


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
        assert len(results) == 1
        assert "agents/bot.py" in results[0].provenance
