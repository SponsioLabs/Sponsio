"""Phase 3: Extract constraints from agent source code via AST analysis.

Two-stage pipeline:

1. **AST pass** (deterministic, zero dependencies):
   Discovers tools (``@tool``, ``@function_tool``, ``Agent(tools=[...])``,
   ``graph.add_node()``) and analyzes the call graph to infer ordering.
   Also extracts docstrings and function signatures for context.

2. **LLM pass** (optional, requires ``openai``):
   Sends the tool inventory + source context to
   ``UnifiedExtractor.extract_from_code()`` for deeper inference across
   all 16 det patterns and 6 sto categories.

Usage::

    from sponsio.discovery.extractors import CodeAnalyzer

    # AST-only (default, zero deps)
    analyzer = CodeAnalyzer()
    proposals = analyzer.extract(["agents/customer_service.py"])

    # AST + LLM (richer inference)
    analyzer = CodeAnalyzer(use_llm=True)
    proposals = analyzer.extract(["agents/customer_service.py"])

    for p in proposals:
        print(p.nl_description, p.confidence)
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from sponsio.discovery._types import (
    ConstraintStatus,
    DiscoverySource,
    ProposedConstraint,
)
from sponsio.patterns.library import (
    must_precede,
)

logger = logging.getLogger(__name__)


@dataclass
class ToolInfo:
    """Information about a discovered tool registration.

    Attributes:
        name: Tool function name.
        filepath: Source file path.
        line: Line number of the definition.
        calls: Other tool names called from this tool.
        docstring: Extracted docstring (for LLM context).
        params: Parameter names and annotations (for LLM context).
        source: Source code of the function body (for LLM context).
    """

    name: str
    filepath: str
    line: int
    calls: list[str] = field(default_factory=list)
    docstring: str = ""
    params: str = ""
    source: str = ""


class CodeAnalyzer:
    """Extract constraints from Python source code using AST analysis.

    Stage 1 (always runs): Deterministic AST analysis.
    Stage 2 (optional): LLM-based inference via UnifiedExtractor.

    Looks for:
    - Tool registrations (@tool decorators, Agent(tools=[...]))
    - LangGraph graph.add_node() registrations
    - Call graph dependencies between tools
    - Docstrings, parameter signatures, and source bodies

    Args:
        use_llm: If True, runs LLM inference after AST analysis.
        llm_model: OpenAI model for LLM pass.
        api_key: OpenAI API key. If None, uses ``OPENAI_API_KEY``.
        client: Pre-configured ``openai.OpenAI`` client.
        min_confidence: Minimum confidence for LLM-inferred constraints.
    """

    def __init__(
        self,
        use_llm: bool = False,
        llm_model: str | None = None,
        api_key: Optional[str] = None,
        client: Any = None,
        provider: str | None = None,
        min_confidence: float = 0.5,
        use_structured_ir: bool = False,
    ) -> None:
        self._use_llm = use_llm
        self._llm_model = llm_model
        self._api_key = api_key
        self._client = client
        self._provider = provider
        self._min_confidence = min_confidence
        self._use_ir = use_structured_ir

    def extract(self, source_paths: list[str | Path]) -> list[ProposedConstraint]:
        """Analyze source files and extract constraint candidates.

        Runs the AST pass on all files, then optionally runs the LLM pass
        with the discovered tool inventory as context.

        Args:
            source_paths: Python source files or directories to analyze.

        Returns:
            List of proposed constraints.
        """
        all_tools: list[ToolInfo] = []
        all_sources: list[str] = []

        # Scan all relevant files — .py for AST, others for LLM context
        _SCAN_EXTENSIONS = {
            ".py",
            ".sh",
            ".json",
            ".yaml",
            ".yml",
            ".md",
            ".txt",
            ".csv",
        }

        for path_str in source_paths:
            path = Path(path_str)
            if path.is_dir():
                for file in path.rglob("*"):
                    if file.suffix not in _SCAN_EXTENSIONS:
                        continue
                    if file.suffix == ".py":
                        all_tools.extend(self._analyze_file(file))
                    try:
                        all_sources.append(f"# File: {file.name}\n{file.read_text()}")
                    except Exception:
                        pass
            elif path.is_file():
                if path.suffix == ".py":
                    all_tools.extend(self._analyze_file(path))
                if path.suffix in _SCAN_EXTENSIONS:
                    try:
                        all_sources.append(f"# File: {path.name}\n{path.read_text()}")
                    except Exception:
                        pass

        # Stage 1: AST-based constraints (deterministic)
        results = self._tools_to_constraints(all_tools)

        # Stage 2: LLM-based inference (optional)
        # Runs even when AST found no tools — LLM can discover tools from source
        if self._use_llm and all_sources:
            llm_results = self._llm_inference(all_tools, all_sources, existing=results)
            existing_keys = {self._dedup_key(r) for r in results}
            for r in llm_results:
                key = self._dedup_key(r)
                if key not in existing_keys:
                    results.append(r)
                    existing_keys.add(key)

        return results

    def extract_from_source(
        self, source: str, filename: str = "<string>"
    ) -> list[ProposedConstraint]:
        """Analyze source code string directly (useful for testing).

        Args:
            source: Python source code as a string.
            filename: Virtual filename for provenance.

        Returns:
            List of proposed constraints.
        """
        tools = self._analyze_source(source, filename)
        results = self._tools_to_constraints(tools)

        if self._use_llm:
            llm_results = self._llm_inference(tools, [source], existing=results)
            existing_keys = {self._dedup_key(r) for r in results}
            for r in llm_results:
                key = self._dedup_key(r)
                if key not in existing_keys:
                    results.append(r)
                    existing_keys.add(key)

        return results

    @staticmethod
    def _dedup_key(r: "ProposedConstraint") -> tuple:
        """Key for deduplicating proposals across AST and LLM results."""
        if r.formula:
            return ("det", str(r.formula.formula))
        elif r.sto:
            return ("sto", r.sto.category, r.nl_description)
        return ("unknown", r.nl_description)

    # -----------------------------------------------------------------
    # Source selection for LLM
    # -----------------------------------------------------------------

    @staticmethod
    def _select_sources(
        sources: list[str],
        tools: list["ToolInfo"],
        max_chars: int = 80_000,
    ) -> list[str]:
        """Select the most relevant source files within a token budget.

        Priority:
        1. Files containing known tool names (from AST discovery)
        2. .py files (likely contain tool definitions)
        3. .json/.yaml files (may contain tool schemas)
        4. .md/.txt files (documentation, lowest priority)

        Large files are truncated to fit the budget.
        """
        tool_names = {t.name for t in tools}

        # Score each source by relevance
        scored: list[tuple[int, int, str]] = []
        for i, src in enumerate(sources):
            # Extract filename from "# File: xxx\n..." header
            first_line = src.split("\n", 1)[0]
            filename = first_line.replace("# File: ", "").strip()

            score = 0
            # Agent prompts/task descriptions — critical for attack surface
            if any(kw in filename.lower() for kw in ("prompt", "task", "system")):
                score += 15
            elif any(
                kw in src[:500]
                for kw in ("system_prompt", "user_prompt", "harmful_behavior")
            ):
                score += 15
            # Files containing tool names are most relevant
            for name in tool_names:
                if name in src:
                    score += 10
                    break
            # Priority by extension
            if filename.endswith((".py", ".sh")):
                score += 5
            elif filename.endswith((".yaml", ".yml")):
                score += 3
            elif filename.endswith(".json"):
                score += 2
            else:
                score += 1

            scored.append((score, i, src))

        # Sort by score descending
        scored.sort(key=lambda x: -x[0])

        # Fill within budget
        selected: list[str] = []
        remaining = max_chars
        for _score, _idx, src in scored:
            if remaining <= 0:
                break
            if len(src) > remaining:
                # Truncate large files
                src = src[:remaining] + "\n# ... (truncated)"
            selected.append(src)
            remaining -= len(src)

        return selected

    # -----------------------------------------------------------------
    # AST analysis
    # -----------------------------------------------------------------

    def _analyze_file(self, path: Path) -> list[ToolInfo]:
        """Analyze a single Python file."""
        try:
            source = path.read_text()
        except Exception:
            return []
        return self._analyze_source(source, str(path))

    def _analyze_source(self, source: str, filename: str) -> list[ToolInfo]:
        """Analyze Python source code and extract tool info."""
        try:
            tree = ast.parse(source, filename=filename)
        except SyntaxError:
            return []

        tools: list[ToolInfo] = []
        tools.extend(self._find_decorated_tools(tree, source, filename))
        tools.extend(self._find_agent_tools(tree, filename))
        tools.extend(self._find_langgraph_nodes(tree, source, filename))
        self._analyze_call_graph(tree, tools)
        self._find_graph_edges(tree, tools)
        return tools

    def _find_decorated_tools(
        self, tree: ast.AST, source: str, filename: str
    ) -> list[ToolInfo]:
        """Find functions decorated with @tool or @function_tool.

        Extracts docstrings, parameter signatures, and source bodies
        for LLM context.
        """
        source_lines = source.split("\n") if source else []
        tools: list[ToolInfo] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                dec_name = self._get_decorator_name(decorator)
                if dec_name in ("tool", "function_tool"):
                    tool_info = ToolInfo(
                        name=node.name,
                        filepath=filename,
                        line=node.lineno,
                    )
                    # Extract docstring
                    tool_info.docstring = ast.get_docstring(node) or ""
                    # Extract parameter signature
                    tool_info.params = self._extract_params(node)
                    # Extract source body (limited to 30 lines)
                    if source_lines and hasattr(node, "end_lineno"):
                        start = node.lineno - 1
                        end = min(node.end_lineno or start + 30, start + 30)
                        tool_info.source = "\n".join(source_lines[start:end])
                    tools.append(tool_info)
                    break
        return tools

    def _find_agent_tools(self, tree: ast.AST, filename: str) -> list[ToolInfo]:
        """Find tools from Agent(tools=[...]) or similar constructor patterns."""
        tools: list[ToolInfo] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func_name = self._get_call_name(node)
            if func_name not in ("Agent", "Crew"):
                continue
            for keyword in node.keywords:
                if keyword.arg == "tools" and isinstance(keyword.value, ast.List):
                    for elt in keyword.value.elts:
                        name = self._extract_name(elt)
                        if name:
                            tools.append(
                                ToolInfo(
                                    name=name,
                                    filepath=filename,
                                    line=node.lineno,
                                )
                            )
        return tools

    def _find_langgraph_nodes(
        self, tree: ast.AST, source: str, filename: str
    ) -> list[ToolInfo]:
        """Find tools from LangGraph graph.add_node() calls."""
        tools: list[ToolInfo] = []
        seen_names: set[str] = set()
        source_lines = source.splitlines()

        # Collect variable names assigned from StateGraph() or MessageGraph()
        graph_vars: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                func = node.value.func
                func_name = ""
                if isinstance(func, ast.Name):
                    func_name = func.id
                elif isinstance(func, ast.Attribute):
                    func_name = func.attr
                if func_name in ("StateGraph", "MessageGraph", "Graph"):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            graph_vars.add(target.id)
        # Common convention names as fallback
        graph_vars.update({"graph", "builder", "workflow"})

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # Match graph.add_node("name", func) — only on known graph variables
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "add_node":
                continue
            # Check the object is a known graph variable
            if isinstance(node.func.value, ast.Name):
                if node.func.value.id not in graph_vars:
                    continue
            else:
                continue  # skip chained calls like foo.bar.add_node()
            if len(node.args) >= 1:
                name = self._extract_name(node.args[0])
                if name and name not in seen_names:
                    seen_names.add(name)
                    tool_info = ToolInfo(
                        name=name,
                        filepath=filename,
                        line=node.lineno,
                    )
                    # Resolve method reference: self._node_X → find definition
                    if len(node.args) >= 2:
                        func_ref = node.args[1]
                        method_name = None
                        if isinstance(func_ref, ast.Attribute):
                            method_name = func_ref.attr
                        elif isinstance(func_ref, ast.Name):
                            method_name = func_ref.id
                        if method_name:
                            self._resolve_func_body(
                                tree, method_name, source_lines, tool_info
                            )
                    tools.append(tool_info)
        return tools

    def _resolve_func_body(
        self,
        tree: ast.AST,
        func_name: str,
        source_lines: list[str],
        tool_info: "ToolInfo",
    ) -> None:
        """Find a function/method definition and populate tool_info."""
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == func_name:
                    tool_info.docstring = ast.get_docstring(node) or ""
                    tool_info.params = self._extract_params(node)
                    if source_lines and hasattr(node, "end_lineno"):
                        start = node.lineno - 1
                        end = min(node.end_lineno or start + 30, start + 30)
                        tool_info.source = "\n".join(source_lines[start:end])
                    break

    def _find_graph_edges(self, tree: ast.AST, tools: list[ToolInfo]) -> None:
        """Extract ordering from any ``*.add_edge("A", "B")`` calls.

        Framework-agnostic: matches LangGraph, NetworkX, custom DAGs,
        or any object with an ``add_edge`` method.  Only edges between
        known tool names are recorded.
        """
        tool_names = {t.name for t in tools}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "add_edge":
                continue
            if len(node.args) >= 2:
                src = self._extract_name(node.args[0])
                dst = self._extract_name(node.args[1])
                if src and dst and src in tool_names and dst in tool_names:
                    for t in tools:
                        if t.name == dst and src not in t.calls:
                            t.calls.append(src)

    def _analyze_call_graph(self, tree: ast.AST, tools: list[ToolInfo]) -> None:
        """Analyze which tools call other tools (in-function calls)."""
        tool_names = {t.name for t in tools}
        func_map: dict[str, ast.FunctionDef] = {}

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in tool_names:
                    func_map[node.name] = node

        for tool in tools:
            func_node = func_map.get(tool.name)
            if func_node is None:
                continue
            for child in ast.walk(func_node):
                if isinstance(child, ast.Call):
                    callee = self._get_call_name(child)
                    if callee and callee in tool_names and callee != tool.name:
                        tool.calls.append(callee)

    # -----------------------------------------------------------------
    # Constraint generation
    # -----------------------------------------------------------------

    def _tools_to_constraints(self, tools: list[ToolInfo]) -> list[ProposedConstraint]:
        """Convert tool analysis findings to constraint candidates."""
        results: list[ProposedConstraint] = []
        seen: set[str] = set()

        for tool in tools:
            for callee in tool.calls:
                # If tool A calls tool B, B should precede A's main action
                key = f"must_precede:{callee}:{tool.name}"
                if key in seen:
                    continue
                seen.add(key)

                formula = must_precede(callee, tool.name)
                results.append(
                    ProposedConstraint(
                        formula=formula,
                        source=DiscoverySource.AUTO_EXTRACTED,
                        extractor="code_analysis",
                        confidence=0.7,
                        status=ConstraintStatus.PROPOSED,
                        provenance=f"{tool.filepath}:{tool.line}",
                        nl_description=f"{callee} should be called before {tool.name} (inferred from call graph)",
                        evidence={
                            "caller": tool.name,
                            "callee": callee,
                            "file": tool.filepath,
                            "line": tool.line,
                        },
                    )
                )

        return results

    # -----------------------------------------------------------------
    # AST helpers
    # -----------------------------------------------------------------

    # -----------------------------------------------------------------
    # LLM-based inference (Stage 2)
    # -----------------------------------------------------------------

    def _llm_inference(
        self,
        tools: list[ToolInfo],
        sources: list[str],
        existing: list[ProposedConstraint] | None = None,
    ) -> list[ProposedConstraint]:
        """Run LLM inference on the tool inventory for deeper constraint mining.

        Uses ``UnifiedExtractor.extract_from_code()`` with the Atom-aware
        prompt to infer constraints across all 16 det patterns and 6 sto
        categories.

        Args:
            tools: Tool inventory from AST analysis.
            sources: Source file contents for context.
            existing: Rule-based results to include as context so the
                LLM focuses on discovering new constraints.

        Returns:
            List of LLM-inferred ProposedConstraint objects.
        """
        try:
            from sponsio.generation.llm_extraction import UnifiedExtractor
        except ImportError:
            logger.warning("llm_extraction not available, skipping LLM pass")
            return []

        try:
            extractor = UnifiedExtractor(
                model=self._llm_model,
                api_key=self._api_key,
                client=self._client,
                provider=self._provider,
                use_structured_ir=self._use_ir,
            )
        except ImportError:
            logger.warning("openai not installed, skipping LLM pass")
            return []

        # Build tool inventory for the extractor
        tool_inventory = []
        for t in tools:
            entry = {"name": t.name}
            if t.docstring:
                entry["docstring"] = t.docstring
            if t.params:
                entry["params"] = t.params
            if t.source:
                entry["source"] = t.source
            tool_inventory.append(entry)

        # Tell LLM what rule-based already found
        already_found = ""
        if existing:
            lines = ["# Already discovered by static analysis (do NOT repeat):"]
            for r in existing:
                if r.formula:
                    lines.append(f"- {r.formula.pattern_name}: {r.formula.desc}")
            already_found = "\n".join(lines)

        # Select most relevant source files within token budget
        relevant_sources = self._select_sources(sources, tools, max_chars=80_000)

        results = extractor.extract_from_code(
            tool_inventory=tool_inventory,
            source_files=relevant_sources,
            source_snippet=already_found,
            min_confidence=self._min_confidence,
        )

        # Merge LLM-discovered tools into our tool list
        for t in extractor.last_discovered_tools:
            name = t.get("name", "")
            if name and not any(existing.name == name for existing in tools):
                tools.append(
                    ToolInfo(
                        name=name,
                        filepath="(llm-discovered)",
                        line=0,
                        docstring=t.get("description", ""),
                    )
                )

        # Convert ExtractionResults to ProposedConstraints
        proposals: list[ProposedConstraint] = []
        for r in results:
            if not r.ok:
                logger.info(
                    "LLM constraint skipped: %s — %s", r.nl_description, r.error
                )
                continue

            proposal = ProposedConstraint(
                source=DiscoverySource.AUTO_EXTRACTED,
                extractor="code_analysis_llm",
                confidence=r.confidence,
                status=ConstraintStatus.PROPOSED,
                provenance="LLM inference from tool inventory",
                nl_description=r.nl_description,
                evidence={
                    "pattern": r.pattern_name,
                    "args": r.args,
                    "source_quote": r.source_quote,
                    "llm_model": self._llm_model,
                },
            )

            if r.constraint_type == "det":
                proposal.formula = r.compiled
                if r.compiled_assumption:
                    proposal.assumption = r.compiled_assumption
            else:
                proposal.sto = r.compiled

            proposals.append(proposal)

        return proposals

    # -----------------------------------------------------------------
    # Tool inventory export (for sponsio init --scan)
    # -----------------------------------------------------------------

    def get_tool_inventory(
        self, source_paths: list[str | Path]
    ) -> list[dict[str, Any]]:
        """Extract tool inventory without generating constraints.

        Useful for ``sponsio init --scan`` to show discovered tools
        before running LLM inference.

        Args:
            source_paths: Python source files or directories.

        Returns:
            List of tool info dicts with name, filepath, docstring, params.
        """
        all_tools: list[ToolInfo] = []

        for path_str in source_paths:
            path = Path(path_str)
            if path.is_dir():
                for py_file in path.rglob("*.py"):
                    all_tools.extend(self._analyze_file(py_file))
            elif path.is_file() and path.suffix == ".py":
                all_tools.extend(self._analyze_file(path))

        return [
            {
                "name": t.name,
                "filepath": t.filepath,
                "line": t.line,
                "docstring": t.docstring,
                "params": t.params,
                "calls": t.calls,
            }
            for t in all_tools
        ]

    # -----------------------------------------------------------------
    # YAML generation (for sponsio init --scan)
    # -----------------------------------------------------------------

    def generate_yaml(
        self,
        source_paths: list[str | Path],
        agent_id: str = "agent",
        policy_paths: list[str] | None = None,
        tool_inventory: list[dict] | None = None,
    ) -> str:
        """Scan code (and optionally policy docs) to generate sponsio.yaml.

        Args:
            source_paths: Python source files or directories.
            agent_id: Agent identifier for the YAML config.
            policy_paths: Optional policy documents (.md/.txt) to extract
                constraints from using the tool inventory as context.
            tool_inventory: Optional pre-computed tool inventory. If None,
                extracted from source_paths automatically.

        Returns:
            YAML string ready to write to ``sponsio.yaml``.
        """
        # Code scan proposals
        proposals = self.extract(source_paths)

        # Build tool inventory if not provided
        if tool_inventory is None:
            tool_inventory = self.get_tool_inventory(source_paths)

        # Policy document proposals (requires LLM)
        if policy_paths:
            policy_proposals = self._extract_from_policies(policy_paths, tool_inventory)
            proposals.extend(policy_proposals)

        # --- Build YAML ---
        lines = [
            "# Generated by: sponsio scan",
            "# Review each constraint and remove or adjust as needed.",
            "",
            'version: "1"',
        ]

        # Tools section
        if tool_inventory:
            lines.append("")
            lines.append("tools:")
            for t in tool_inventory:
                lines.append(f"  - name: {t['name']}")
                if t.get("docstring"):
                    desc = t["docstring"].split("\n")[0][:80]
                    lines.append(f'    description: "{desc}"')
                if t.get("params"):
                    lines.append(f'    params: "{t["params"]}"')

        # Agents section — emit `contracts:` with `E:` short-keys (YAML
        # schema). Each proposal becomes one unconditional contract entry.
        lines.append("")
        lines.append("agents:")
        lines.append(f"  {agent_id}:")

        if not proposals:
            # Emit an explicit empty list so the loader still sees a list
            # (comments alone would parse the field as None and fail).
            lines.append("    contracts: []")
            lines.append("    # No constraints inferred — add your own:")
            lines.append('    # - E: "tool `check_policy` must precede `issue_refund`"')
            lines.append("    # - E:")
            lines.append("    #     pattern: must_precede")
            lines.append("    #     args: [check_policy, issue_refund]")
        else:
            lines.append("    contracts:")
            for p in sorted(proposals, key=lambda x: -x.confidence):
                confidence_tag = ""
                if p.confidence < 0.9:
                    confidence_tag = f"  # confidence: {p.confidence:.2f}"
                if p.confidence < 0.5:
                    confidence_tag += " — review recommended"

                src_label = ""
                if p.extractor:
                    src_label = "scan" if "code" in p.extractor else "policy"

                if p.formula:
                    pattern = p.formula.pattern_name
                    lines.append(f"      - E:{confidence_tag}")
                    lines.append(f"          pattern: {pattern}")
                    # Reconstruct args
                    if p.evidence and "args" in p.evidence:
                        args = p.evidence["args"]
                        args_str = ", ".join(str(a) for a in args)
                        lines.append(f"          args: [{args_str}]")
                    elif p.evidence:
                        caller = p.evidence.get("caller", "")
                        callee = p.evidence.get("callee", "")
                        if caller and callee:
                            lines.append(f"          args: [{callee}, {caller}]")
                    if src_label:
                        lines.append(f"          source: {src_label}")
                elif p.sto:
                    lines.append(f'      - E: "{p.nl_description}"{confidence_tag}')

        lines.append("")
        return "\n".join(lines)

    def _extract_from_policies(
        self,
        policy_paths: list[str],
        tool_inventory: list[dict],
    ) -> list[ProposedConstraint]:
        """Extract constraints from policy documents using LLM + tool context."""
        try:
            from sponsio.discovery.extractors.document import DocumentExtractor
        except ImportError:
            logger.warning("Document extractor not available")
            return []

        extractor = DocumentExtractor(
            model=self._llm_model or "gemini-2.0-flash",
            api_key=self._api_key,
        )

        proposals: list[ProposedConstraint] = []
        for path_str in policy_paths:
            path = Path(path_str)
            if path.is_file():
                try:
                    content = path.read_text()
                    results = extractor.extract(
                        content,
                        tool_inventory=tool_inventory,
                    )
                    proposals.extend(results)
                except Exception as e:
                    logger.warning("Policy extraction failed for %s: %s", path, e)

        return proposals

    # -----------------------------------------------------------------
    # AST helpers
    # -----------------------------------------------------------------

    @staticmethod
    def _extract_params(node: ast.FunctionDef) -> str:
        """Extract parameter names and annotations from a function def."""
        params = []
        for arg in node.args.args:
            if arg.arg == "self":
                continue
            annotation = ""
            if arg.annotation:
                try:
                    annotation = f": {ast.unparse(arg.annotation)}"
                except Exception:
                    pass
            params.append(f"{arg.arg}{annotation}")
        return ", ".join(params)

    @staticmethod
    def _get_decorator_name(node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Call):
            return CodeAnalyzer._get_call_name(node)
        if isinstance(node, ast.Attribute):
            return node.attr
        return ""

    @staticmethod
    def _get_call_name(node: ast.Call) -> str:
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
        return ""

    @staticmethod
    def _extract_name(node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return ""
