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
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from sponsio.discovery._types import (
    ConstraintStatus,
    DiscoverySource,
    ProposedConstraint,
)
from sponsio.patterns.library import (
    arg_blacklist,
    arg_length_limit,
    idempotent,
    mutual_exclusion,
    must_precede,
    no_data_leak,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Heuristic patterns (reused / adapted from sponsio.scoring.scorer)
# ---------------------------------------------------------------------------

# Antonym verb pairs.  Each tuple is (verb_a, verb_b); we then look for
# tools whose names share the same suffix after the verb (e.g.
# ``approve_refund`` / ``reject_refund``) and propose mutual exclusion.
_ANTONYM_PAIRS: tuple[tuple[str, str], ...] = (
    ("approve", "reject"),
    ("approve", "deny"),
    ("accept", "reject"),
    ("enable", "disable"),
    ("grant", "revoke"),
    ("allow", "block"),
    ("open", "close"),
    ("start", "stop"),
    ("activate", "deactivate"),
    ("publish", "unpublish"),
    ("mount", "unmount"),
)

# Tools whose names suggest irreversible / destructive actions.  Triggers
# the "confirm must precede destructive" heuristic and idempotency
# suggestion for financial verbs.
_DESTRUCTIVE_RE = re.compile(
    r"\b(?:delete|drop|remove|destroy|wipe|purge|truncate|"
    r"transfer|deploy|release|migrate|rollback|revoke|terminate|kill)\b",
    re.IGNORECASE,
)
_FINANCIAL_RE = re.compile(
    r"\b(?:pay|transfer|charge|refund|invoice|debit|credit|"
    r"withdraw|deposit|bill|settle|chargeback)\b",
    re.IGNORECASE,
)
_CONFIRM_RE = re.compile(
    r"\b(?:confirm|approve|authorize|consent|review|verify_(?:user|action))\b",
    re.IGNORECASE,
)
_EXTERNAL_RE = re.compile(
    r"\b(?:email|slack|sms|webhook|notify|message|publish|"
    r"send|post_to|http_post|broadcast|tweet)\b",
    re.IGNORECASE,
)

# Subset of external sends that are **broadcast** — i.e. the target is
# *not* tied to the data subject we just read.  no_data_leak only fires
# on broadcast sinks so we don't flag the entire support-agent
# workflow ("look up customer → send email *to that same customer*").
#
# Two ways a sink qualifies as broadcast:
#   1. Its NAME contains a broadcast verb (post_to, publish, broadcast,
#      tweet, webhook).  These are channel/topic-shaped by convention.
#   2. Its PARAMS contain a routing key that's not a per-user address
#      (channel, topic, webhook_url, room).  An ``email(to=...)`` tool
#      is point-to-point; a ``slack(channel=...)`` tool is broadcast.
_BROADCAST_NAME_RE = re.compile(
    r"\b(?:post_to|publish|broadcast|tweet|webhook|notify_channel)\b",
    re.IGNORECASE,
)
_BROADCAST_PARAM_NAMES = frozenset(
    {
        "channel",
        "topic",
        "room",
        "webhook",
        "webhook_url",
        "feed",
        "audience",
        "broadcast",
        "subscribers",
    }
)
_SENSITIVE_READ_RE = re.compile(
    r"(?:user|customer|patient|account|payment|employee|profile|credential|"
    r"secret|password|api_?key|ssn|salary|medical|dob|address)",
    re.IGNORECASE,
)
_DATA_SOURCE_RE = re.compile(
    r"\b(?:get|fetch|read|query|list|describe|select|lookup|search|load|export)\b",
    re.IGNORECASE,
)


_CAMEL_SPLIT_RE = re.compile(
    # Insert a space between:
    #   * a lowercase/digit and an uppercase letter (``deleteUser``)
    #   * an uppercase run and a single uppercase + lowercase
    #     (``HTTPRequest`` → ``HTTP Request``)
    r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])"
)


def _tokenize_name(name: str) -> str:
    """Split a snake_case / camelCase / PascalCase identifier into words.

    Examples:
        ``issue_refund``       → ``"issue refund"``
        ``deleteUser``         → ``"delete User"``
        ``HTTPRequestParser``  → ``"HTTP Request Parser"``
        ``post_to_Slack``      → ``"post to Slack"``

    The tokenized form is used **alongside** the raw name in
    :func:`_tool_text` so regexes anchored on ``\\b`` actually match
    individual word components.  Without this, neither
    ``\\brefund\\b`` matches ``issue_refund`` (``_`` is a word char) nor
    ``\\bdelete\\b`` matches ``deleteUser`` (no boundary between ``e``
    and ``U`` inside the same word).
    """
    return _CAMEL_SPLIT_RE.sub(" ", name).replace("_", " ")


def _tool_text(tool: "ToolInfo") -> str:
    """Combine name + docstring + params for keyword matching.

    The tool name appears **twice**: raw plus a tokenized form (see
    :func:`_tokenize_name`) so name-based heuristics work uniformly
    across snake_case (``delete_user``), camelCase (``deleteUser``)
    and PascalCase (``DeleteUser``) identifiers.  This single
    normalization is what unlocks ~30-60% more contracts on real
    agents whose docstrings are terse or missing.
    """
    return f"{tool.name} {_tokenize_name(tool.name)} {tool.docstring} {tool.params}"


def _is_destructive(tool: "ToolInfo") -> bool:
    return bool(_DESTRUCTIVE_RE.search(_tool_text(tool)))


def _is_financial(tool: "ToolInfo") -> bool:
    return bool(_FINANCIAL_RE.search(_tool_text(tool)))


def _is_confirm(tool: "ToolInfo") -> bool:
    return bool(_CONFIRM_RE.search(_tool_text(tool)))


def _is_external_send(tool: "ToolInfo") -> bool:
    return bool(_EXTERNAL_RE.search(_tool_text(tool)))


def _is_broadcast_sink(tool: "ToolInfo") -> bool:
    """True iff the sink fans out to an audience not tied to the data subject.

    This is the precision-tightener for ``no_data_leak``: a generic
    point-to-point send (``send_email(to, body)``) IS the workflow for
    most support / notification agents, so flagging it produces noise.
    A broadcast sink (``post_to_slack(channel, message)``,
    ``publish(topic, payload)``, ``send_webhook(url, ...)``) is a real
    leak vector — its target lives outside the user/customer scope
    we just read from.
    """
    if _BROADCAST_NAME_RE.search(_tool_text(tool)):
        return True
    for name, _ann in _parse_params(tool.params):
        if name.lower() in _BROADCAST_PARAM_NAMES:
            return True
    return False


def _is_sensitive_read(tool: "ToolInfo") -> bool:
    text = _tool_text(tool)
    return bool(_DATA_SOURCE_RE.search(text) and _SENSITIVE_READ_RE.search(text))


# ---------------------------------------------------------------------------
# Param-shape heuristics — generic safety contracts that apply to any agent
# whose tools take user-controlled inputs.  These reuse existing patterns
# (arg_length_limit / arg_blacklist) so no new atom is required.
# ---------------------------------------------------------------------------

# Param-name → semantic role.  Aliases collapse onto a canonical role so
# "filepath", "filename", "dir", "directory", "folder" all map to "path".
_PARAM_ROLE_ALIASES: dict[str, str] = {
    # Free-text inputs (prompt-injection attack surface)
    "text": "text",
    "query": "text",
    "prompt": "text",
    "input": "text",
    "message": "text",
    "content": "text",
    "instruction": "text",
    "body": "text",
    "description": "text",
    # Shell-style commands
    "command": "command",
    "cmd": "command",
    "shell": "command",
    "exec": "command",
    "script": "command",
    # Filesystem paths
    "path": "path",
    "filepath": "path",
    "filename": "path",
    "file": "path",
    "dir": "path",
    "directory": "path",
    "folder": "path",
    # URLs / network endpoints (SSRF surface)
    "url": "url",
    "endpoint": "url",
    "host": "url",
    "uri": "url",
    "address": "url",
    # SQL-ish queries (only fires on tools whose name suggests DB)
    "sql": "sql",
    "query_string": "sql",
    "statement": "sql",
}

# Default blacklists per role.  Tuned to be high-signal; users review and
# extend.  Patterns are regexes evaluated by `arg_field_has`.
_PARAM_BLACKLISTS: dict[str, list[str]] = {
    "command": [
        r"rm\s+-rf",  # classic "delete everything" footgun
        r"\bsudo\b",  # privilege escalation
        r"curl[^|]*\|\s*sh",  # remote shell exec
        r"wget[^|]*\|\s*sh",
        r"chmod\s+-?R?\s*777",
        r":\(\)\s*\{",  # fork-bomb prefix
    ],
    "path": [
        r"\.\./",  # path traversal
        r"~/\.ssh/",  # SSH keys
        r"^/etc/(?:passwd|shadow|sudoers)",
        r"^/proc/",
        r"^/sys/",
        r"\.aws/credentials",
    ],
    "url": [
        r"^file://",  # local-file scheme
        r"^gopher://",
        r"\blocalhost\b",  # SSRF — internal services
        r"\b127\.0\.0\.1\b",
        r"\b0\.0\.0\.0\b",
        r"\b169\.254\.\d+\.\d+\b",  # cloud metadata
        r"\b10\.\d+\.\d+\.\d+\b",  # private network
        r"\b192\.168\.\d+\.\d+\b",
    ],
    "sql": [
        r"(?i)\bDROP\s+TABLE\b",
        r"(?i)\bTRUNCATE\b",
        r"(?i)\bDELETE\s+FROM\s+\w+\s*(?:;|$)",  # DELETE without WHERE
        r"(?i)\bUPDATE\s+\w+\s+SET\b(?!.*\bWHERE\b)",  # naive: no WHERE
    ],
}

# Per-role character caps for the free-text length-limit heuristic.
# Each entry is ``param_name → (cap, rationale)``.  Caps are tuned to
# *exceed legitimate use by a wide margin* on real platforms, while
# still bounded enough to catch unbounded growth (DoS, prompt-stuffing).
#
# Why per-role rather than one magic number:
#   * ``query`` / ``search`` — real search inputs almost never exceed a
#     few hundred chars; 10K let attackers stuff a whole prompt in.
#   * ``body`` / ``message`` — legitimate emails (HTML signatures,
#     formatted markdown) routinely exceed 10K; capping there causes
#     false positives that train users to ignore the contract.
#   * ``prompt`` / ``instruction`` — LLM-context-shaped fields can be
#     long but still bounded.
#
# Adding a name here makes the heuristic propose ``arg_length_limit``
# for any string param of that name.  Cap is the *default*; users
# routinely tune it.
_TEXT_PARAM_LIMITS: dict[str, int] = {
    # Search / query — short by nature
    "query": 1_000,
    "search": 1_000,
    # Free-form prompts / instructions — LLM-shaped
    "prompt": 50_000,
    "instruction": 50_000,
    # User-authored bodies — emails, posts, comments
    "body": 100_000,
    "message": 100_000,
    "content": 100_000,
    # Descriptions / summaries — human-written, short
    "description": 5_000,
    "summary": 5_000,
    # Generic catch-alls
    "text": 10_000,
    "input": 10_000,
}

# Backwards-compat alias — old code paths / tests reference the set of
# names directly without caring about the cap.
_TEXT_PARAM_NAMES = frozenset(_TEXT_PARAM_LIMITS)

# Tools whose name suggests they execute SQL — only then does the SQL
# blacklist apply (otherwise a generic ``query: str`` param would always
# match and add noise).
_SQL_TOOL_NAME_RE = re.compile(
    r"\b(?:sql|database|db|table|postgres|mysql|sqlite|bigquery|snowflake|redshift)\b",
    re.IGNORECASE,
)


def _parse_params(params_str: str) -> list[tuple[str, str]]:
    """Parse a ToolInfo.params string into ``[(name, annotation), ...]``.

    ``tool.params`` is the result of ``_extract_params`` — a flat string
    like ``"text: str, max_chars: int = 100"``.  This helper splits on
    top-level commas (respecting nested brackets so types like
    ``Dict[str, int]`` aren't broken) and pulls out the param name and
    annotation independently.  Returns an empty list when ``params_str``
    is empty (e.g. tools registered via string literal in
    ``Agent(tools=[...])``).
    """
    if not params_str:
        return []
    parts: list[tuple[str, str]] = []
    depth = 0
    buf: list[str] = []
    pieces: list[str] = []
    for ch in params_str + ",":
        if ch in "([{":
            depth += 1
            buf.append(ch)
        elif ch in ")]}":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == "," and depth == 0:
            seg = "".join(buf).strip()
            if seg:
                pieces.append(seg)
            buf = []
        else:
            buf.append(ch)
    for seg in pieces:
        # Strip a leading * or ** (varargs) for our purposes
        seg = seg.lstrip("*").strip()
        if ":" in seg:
            name, rest = seg.split(":", 1)
            annotation = rest.split("=", 1)[0].strip()
        else:
            name = seg.split("=", 1)[0]
            annotation = ""
        name = name.strip()
        if name and name not in {"self", "cls"}:
            parts.append((name, annotation))
    return parts


def _annotation_is_str(annotation: str) -> bool:
    """Loose check: does the annotation look like it accepts a string?

    Conservative — we want to skip obvious non-strings (int, float, bool,
    list, dict) and apply the heuristic to anything that mentions ``str``
    or has no annotation at all.
    """
    if not annotation:
        return True  # untyped — assume could be str
    a = annotation.lower()
    if "str" in a:  # str, Optional[str], List[str], etc.
        return True
    # Explicit non-string scalars
    if a in {"int", "float", "bool", "bytes"}:
        return False
    if a.startswith(("list[", "dict[", "set[", "tuple[", "int", "float", "bool")):
        return False
    return True  # unknown → permissive


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
        progress: Callable[[str], None] | bool | None = None,
        base_url: str | None = None,
    ) -> None:
        self._use_llm = use_llm
        self._llm_model = llm_model
        self._api_key = api_key
        self._client = client
        self._provider = provider
        self._base_url = base_url
        self._min_confidence = min_confidence
        self._use_ir = use_structured_ir
        # `progress` may be:
        #   None / False  -> silent (default; library-friendly)
        #   True          -> print to stderr
        #   callable      -> caller-supplied sink (e.g. click.echo wrapper)
        if progress is True:
            self._progress: Callable[[str], None] | None = lambda msg: print(
                msg, file=sys.stderr, flush=True
            )
        elif callable(progress):
            self._progress = progress
        else:
            self._progress = None

    def _emit(self, msg: str) -> None:
        if self._progress is not None:
            try:
                self._progress(msg)
            except Exception:
                pass

    def extract(self, source_paths: list[str | Path]) -> list[ProposedConstraint]:
        """Analyze source files and extract constraint candidates.

        Runs the AST pass on all files, then optionally runs the LLM pass
        with the discovered tool inventory as context.

        Args:
            source_paths: Python source files or directories to analyze.

        Returns:
            List of proposed constraints.
        """
        # Lazy import: ``tool_inventory`` imports ``ToolInfo`` from this
        # module, so a top-level import would be circular.
        from sponsio.discovery.extractors.tool_inventory import load_tool_inventory

        all_tools: list[ToolInfo] = []
        all_sources: list[str] = []
        # Per-source counters used by the post-scan summary so users can
        # see which format(s) actually contributed tools.
        py_tool_count = 0
        inv_tool_count = 0
        inv_files_with_tools = 0

        # Scan all relevant files — .py for AST, JSON/YAML for both LLM
        # context AND cross-framework tool inventory parsing.
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
        _INVENTORY_EXTENSIONS = {".json", ".yaml", ".yml"}

        def _consume(file: Path) -> None:
            nonlocal py_tool_count, inv_tool_count, inv_files_with_tools
            suffix = file.suffix.lower()
            if suffix == ".py":
                py_tools = self._analyze_file(file)
                py_tool_count += len(py_tools)
                all_tools.extend(py_tools)
            if suffix in _INVENTORY_EXTENSIONS:
                # Speculative: returns [] for files that aren't recognized
                # tool inventories (settings, lockfiles, snapshots, …).
                inv_tools = load_tool_inventory(file)
                if inv_tools:
                    inv_tool_count += len(inv_tools)
                    inv_files_with_tools += 1
                    all_tools.extend(inv_tools)
            try:
                all_sources.append(f"# File: {file.name}\n{file.read_text()}")
            except Exception:
                pass

        from sponsio.discovery.loaders import _is_excluded

        for path_str in source_paths:
            path = Path(path_str)
            if path.is_dir():
                for file in path.rglob("*"):
                    if file.suffix not in _SCAN_EXTENSIONS:
                        continue
                    if _is_excluded(file):
                        continue
                    _consume(file)
            elif path.is_file():
                if path.suffix in _SCAN_EXTENSIONS:
                    _consume(path)

        # Stage 1: AST-based constraints (deterministic)
        results = self._tools_to_constraints(all_tools)
        ast_count = len(results)
        # Build a source breakdown only when there's something interesting
        # to say (i.e. an inventory file actually contributed tools).
        if inv_tool_count and py_tool_count:
            breakdown = (
                f" [{py_tool_count} from Python AST, "
                f"{inv_tool_count} from {inv_files_with_tools} inventory file(s)]"
            )
        elif inv_tool_count:
            breakdown = (
                f" [from {inv_files_with_tools} inventory file(s) — "
                "OpenAPI / OpenAI / Anthropic / MCP]"
            )
        else:
            breakdown = ""
        self._emit(
            f"AST scan: {len(all_tools)} tool(s){breakdown}, "
            f"{ast_count} contract(s) inferred (call graph + heuristics)"
        )

        # Stage 2: LLM-based inference (optional)
        # Runs even when AST found no tools — LLM can discover tools from source
        if self._use_llm and all_sources:
            llm_results = self._llm_inference(all_tools, all_sources, existing=results)
            existing_keys = {self._dedup_key(r) for r in results}
            added = 0
            for r in llm_results:
                key = self._dedup_key(r)
                if key not in existing_keys:
                    results.append(r)
                    existing_keys.add(key)
                    added += 1
            self._emit(f"LLM inference: +{added} new contract(s) after dedup")
        elif self._use_llm and not all_sources:
            self._emit("LLM inference skipped: no readable source files found")

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
        """Key for deduplicating proposals across AST and LLM results.

        Includes the assumption so two contracts that share the same
        guarantee but differ in their precondition (e.g. one is
        unconditional, another guards on ``called(modify_order)``) are
        kept as distinct proposals.
        """
        a_key = ""
        if r.assumption is not None:
            a_key = str(getattr(r.assumption, "formula", r.assumption))
        if r.formula:
            return ("det", str(r.formula.formula), a_key)
        elif r.sto:
            return ("sto", r.sto.category, r.nl_description, a_key)
        return ("unknown", r.nl_description, a_key)

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
        tools.extend(self._find_bare_tool_lists(tree, source, filename, tools))
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

    # Module-level assignment names that strongly suggest a bare
    # tool registry (MCP / OpenAI function-calling / custom dispatchers
    # all use this convention even without a framework decorator).
    _BARE_TOOL_LIST_NAMES: tuple[str, ...] = (
        "TOOLS",
        "tools",
        "ALL_TOOLS",
        "all_tools",
        "FUNCTIONS",
        "functions",
        "TOOL_FUNCTIONS",
        "TOOL_REGISTRY",
        "tool_registry",
        "TOOL_LIST",
    )

    def _find_bare_tool_lists(
        self,
        tree: ast.AST,
        source: str,
        filename: str,
        already_seen: list[ToolInfo],
    ) -> list[ToolInfo]:
        """Recognise the framework-agnostic ``TOOLS = [fn1, fn2, ...]``
        module-level pattern.

        Many projects never use ``@tool`` / ``@function_tool`` — they
        hand a list of plain functions to an MCP server, an
        OpenAI function-calling loop, or a hand-rolled dispatcher.
        Previously these tools were invisible to ``sponsio scan`` even
        though they're the most common shape in production code.

        Only matches module-level or class-body assignments; only
        accepts items that are ``Name`` references to functions
        defined in this same file (guards against treating arbitrary
        data arrays as tools).
        """
        source_lines = source.split("\n") if source else []

        # 1. Build the set of function names defined in this file.
        local_funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                local_funcs.setdefault(node.name, node)

        # 2. Avoid double-adding tools already captured via decorator
        # or Agent(tools=...) scans.
        seen_names = {t.name for t in already_seen}

        tools: list[ToolInfo] = []

        def _handle_list(value: ast.expr, lineno: int) -> None:
            if not isinstance(value, (ast.List, ast.Tuple, ast.Set)):
                return
            # Require every item to be a plain Name that resolves to a
            # locally-defined function — a strict filter keeps arrays
            # of constants / data / config dicts from being promoted
            # to "tools".
            if not value.elts:
                return
            if not all(isinstance(e, ast.Name) for e in value.elts):
                return
            matched: list[str] = []
            for elt in value.elts:
                if not isinstance(elt, ast.Name):
                    return
                if elt.id not in local_funcs:
                    return
                matched.append(elt.id)
            for fn_name in matched:
                if fn_name in seen_names:
                    continue
                fn_node = local_funcs[fn_name]
                info = ToolInfo(
                    name=fn_name,
                    filepath=filename,
                    line=fn_node.lineno,
                )
                info.docstring = ast.get_docstring(fn_node) or ""
                info.params = self._extract_params(fn_node)
                if source_lines and hasattr(fn_node, "end_lineno"):
                    start = fn_node.lineno - 1
                    end = min(fn_node.end_lineno or start + 30, start + 30)
                    info.source = "\n".join(source_lines[start:end])
                tools.append(info)
                seen_names.add(fn_name)

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign):
                if not any(
                    isinstance(t, ast.Name) and t.id in self._BARE_TOOL_LIST_NAMES
                    for t in node.targets
                ):
                    continue
                _handle_list(node.value, node.lineno)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id not in self._BARE_TOOL_LIST_NAMES:
                    continue
                if node.value is not None:
                    _handle_list(node.value, node.lineno)

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

    # The constraint-synthesis pipeline.  Each entry is the *method name*
    # of a generator that takes ``list[ToolInfo]`` and returns
    # ``list[ProposedConstraint]``.  All generators run at the same level
    # and their output is merged + deduped — no artificial "stage 1 vs
    # stage 1.5" distinction.  Add a new heuristic by writing a method
    # and listing it here.
    _GENERATORS: tuple[str, ...] = (
        "_gen_call_graph",  # direct A→B calls in source
        "_gen_antonym_pair_mutex",  # approve_X / reject_X
        "_gen_confirm_precedes_destructive",  # confirm + delete-style verbs
        "_gen_sensitive_to_external",  # PII read → external send
        "_gen_financial_idempotent",  # pay/refund/transfer → idempotent
        "_gen_destructive_idempotent",  # delete/drop/wipe → idempotent
        "_gen_text_input_length_limit",  # text: str → arg_length_limit
        "_gen_command_arg_blacklist",  # command/shell → arg_blacklist
        "_gen_path_arg_blacklist",  # path/file → arg_blacklist
        "_gen_url_arg_blacklist",  # url/host → SSRF blacklist
        "_gen_sql_arg_blacklist",  # sql/query on DB tools
    )

    def _tools_to_constraints(self, tools: list[ToolInfo]) -> list[ProposedConstraint]:
        """Run all AST-level constraint generators and dedup the union.

        Each generator is independent and reports its own ``extractor``
        tag so provenance survives the merge.  Dedup uses
        :py:meth:`_dedup_key` which considers the full assumption +
        guarantee, so two generators emitting the same contract produce
        one entry, while the same pattern with different assumptions or
        args coexists.
        """
        results: list[ProposedConstraint] = []
        seen: set[tuple] = set()
        for gen_name in self._GENERATORS:
            gen = getattr(self, gen_name)
            for proposal in gen(tools):
                key = self._dedup_key(proposal)
                if key in seen:
                    continue
                seen.add(key)
                results.append(proposal)
        return results

    # -----------------------------------------------------------------
    # Generator: direct call graph (highest signal)
    # -----------------------------------------------------------------

    def _gen_call_graph(self, tools: list[ToolInfo]) -> list[ProposedConstraint]:
        results: list[ProposedConstraint] = []
        emitted: set[tuple[str, str]] = set()
        for tool in tools:
            for callee in tool.calls:
                pair = (callee, tool.name)
                if pair in emitted:
                    continue
                emitted.add(pair)
                results.append(
                    ProposedConstraint(
                        formula=must_precede(callee, tool.name),
                        source=DiscoverySource.AUTO_EXTRACTED,
                        extractor="code_analysis",
                        confidence=0.7,
                        status=ConstraintStatus.PROPOSED,
                        provenance=f"{tool.filepath}:{tool.line}",
                        nl_description=(
                            f"{callee} should be called before {tool.name} "
                            "(inferred from call graph)"
                        ),
                        evidence={
                            "caller": tool.name,
                            "callee": callee,
                            "args": [callee, tool.name],
                            "file": tool.filepath,
                            "line": tool.line,
                        },
                    )
                )
        return results

    # -----------------------------------------------------------------
    # Generators: name/role-based heuristics (cross-framework)
    # -----------------------------------------------------------------

    def _gen_antonym_pair_mutex(
        self, tools: list[ToolInfo]
    ) -> list[ProposedConstraint]:
        results: list[ProposedConstraint] = []
        if not tools:
            return results
        by_name = {t.name: t for t in tools}
        names = list(by_name.keys())
        for verb_a, verb_b in _ANTONYM_PAIRS:
            for name_a in names:
                lname_a = name_a.lower()
                if not (lname_a.startswith(verb_a + "_") or lname_a == verb_a):
                    continue
                suffix_a = lname_a[len(verb_a) :].lstrip("_")
                for name_b in names:
                    if name_b == name_a:
                        continue
                    lname_b = name_b.lower()
                    if not (lname_b.startswith(verb_b + "_") or lname_b == verb_b):
                        continue
                    suffix_b = lname_b[len(verb_b) :].lstrip("_")
                    if suffix_a != suffix_b:
                        continue
                    a, b = sorted((name_a, name_b))
                    results.append(
                        ProposedConstraint(
                            formula=mutual_exclusion(a, b),
                            source=DiscoverySource.AUTO_EXTRACTED,
                            extractor="code_analysis_heuristic",
                            confidence=0.6,
                            status=ConstraintStatus.PROPOSED,
                            provenance=f"{by_name[a].filepath}:{by_name[a].line}",
                            nl_description=(
                                f"{a} and {b} are antonyms — likely mutually exclusive"
                            ),
                            evidence={
                                "args": [a, b],
                                "heuristic": "antonym_pair",
                                "verbs": [verb_a, verb_b],
                            },
                        )
                    )
        return results

    def _gen_confirm_precedes_destructive(
        self, tools: list[ToolInfo]
    ) -> list[ProposedConstraint]:
        confirms = [t for t in tools if _is_confirm(t)]
        destructives = [t for t in tools if _is_destructive(t)]
        if not confirms or not destructives:
            return []
        # Pick the shortest-named confirm tool as the canonical gate.
        confirm = min(confirms, key=lambda t: len(t.name))
        results: list[ProposedConstraint] = []
        for d in destructives:
            if d.name == confirm.name:
                continue
            results.append(
                ProposedConstraint(
                    formula=must_precede(confirm.name, d.name),
                    source=DiscoverySource.AUTO_EXTRACTED,
                    extractor="code_analysis_heuristic",
                    confidence=0.55,
                    status=ConstraintStatus.PROPOSED,
                    provenance=f"{d.filepath}:{d.line}",
                    nl_description=(
                        f"{confirm.name} should precede destructive action "
                        f"{d.name} (heuristic: confirm + destructive verb)"
                    ),
                    evidence={
                        "args": [confirm.name, d.name],
                        "heuristic": "confirm_precedes_destructive",
                    },
                )
            )
        return results

    def _gen_sensitive_to_external(
        self, tools: list[ToolInfo]
    ) -> list[ProposedConstraint]:
        """Propose ``no_data_leak`` for sensitive-read → broadcast-sink pairs.

        Why broadcast-only: a generic point-to-point send
        (``send_email(to, body)``) is exactly the workflow for support /
        CRM / notification agents — looking up the customer and
        emailing *that same customer* is the job, not a leak.  Flagging
        it generates noise users learn to ignore.

        A broadcast sink (``post_to_slack(channel, ...)``,
        ``publish(topic, ...)``, ``send_webhook(url, ...)``) routes to
        an audience that's *not* tied to the data subject we read.
        That's where real exfiltration happens — patient data into the
        #ops channel, credentials into a public webhook, etc.
        """
        readers = [t for t in tools if _is_sensitive_read(t)]
        sinks = [t for t in tools if _is_broadcast_sink(t)]
        results: list[ProposedConstraint] = []
        for r in readers:
            for s in sinks:
                if r.name == s.name:
                    continue
                results.append(
                    ProposedConstraint(
                        formula=no_data_leak(r.name, s.name),
                        source=DiscoverySource.AUTO_EXTRACTED,
                        extractor="code_analysis_heuristic",
                        confidence=0.45,
                        status=ConstraintStatus.PROPOSED,
                        provenance=f"{r.filepath}:{r.line}",
                        nl_description=(
                            f"data from {r.name} (sensitive read) must not "
                            f"flow to {s.name} (broadcast sink) without review"
                        ),
                        evidence={
                            "args": [r.name, s.name],
                            "heuristic": "sensitive_to_broadcast",
                        },
                    )
                )
        return results

    def _gen_financial_idempotent(
        self, tools: list[ToolInfo]
    ) -> list[ProposedConstraint]:
        results: list[ProposedConstraint] = []
        for tool in tools:
            if not _is_financial(tool):
                continue
            results.append(
                ProposedConstraint(
                    formula=idempotent(tool.name),
                    source=DiscoverySource.AUTO_EXTRACTED,
                    extractor="code_analysis_heuristic",
                    confidence=0.5,
                    status=ConstraintStatus.PROPOSED,
                    provenance=f"{tool.filepath}:{tool.line}",
                    nl_description=(
                        f"{tool.name} looks financial — should be idempotent "
                        "to avoid double-charges on retries"
                    ),
                    evidence={
                        "args": [tool.name],
                        "heuristic": "financial_idempotent",
                    },
                )
            )
        return results

    def _gen_destructive_idempotent(
        self, tools: list[ToolInfo]
    ) -> list[ProposedConstraint]:
        """Suggest ``idempotent`` for destructive / irreversible tools.

        ``_gen_financial_idempotent`` handles the money-moving subset;
        this one covers the structural-destruction subset — ``delete``,
        ``drop``, ``wipe``, ``terminate``, ``revoke``, etc.  These
        operations are by definition unreturnable, so a second call
        triggered by a retry loop or a confused planner causes lasting
        damage.  The ``idempotent`` pattern compiles to
        ``G(count(tool) <= 1)``, which catches any double-invocation
        inside a single session.

        We deliberately skip tools that are *also* financial — the
        dedup in ``_tools_to_constraints`` would collapse the two
        contracts, but skipping here keeps provenance clean so the
        user sees ``heuristic: financial_idempotent`` (more specific
        label) when both match.
        """
        results: list[ProposedConstraint] = []
        for tool in tools:
            if not _is_destructive(tool):
                continue
            if _is_financial(tool):
                # Let ``_gen_financial_idempotent`` own the contract for
                # verbs like ``transfer`` / ``chargeback`` that sit in
                # both sets — its nl_description is more informative.
                continue
            results.append(
                ProposedConstraint(
                    formula=idempotent(tool.name),
                    source=DiscoverySource.AUTO_EXTRACTED,
                    extractor="code_analysis_heuristic",
                    confidence=0.55,
                    status=ConstraintStatus.PROPOSED,
                    provenance=f"{tool.filepath}:{tool.line}",
                    nl_description=(
                        f"{tool.name} looks destructive — should be idempotent "
                        "to avoid repeating an irreversible action on retries"
                    ),
                    evidence={
                        "args": [tool.name],
                        "heuristic": "destructive_idempotent",
                    },
                )
            )
        return results

    # -----------------------------------------------------------------
    # Generators: param-shape-based generic safety contracts
    #
    # These reuse the existing ``arg_length_limit`` and ``arg_blacklist``
    # patterns and apply to *any* agent whose tools accept user-controlled
    # inputs.  They require Python decorator-style param introspection;
    # tools registered as bare strings (e.g. ``Agent(tools=["x"])``) get
    # only the name-based heuristics above.
    # -----------------------------------------------------------------

    def _gen_text_input_length_limit(
        self, tools: list[ToolInfo]
    ) -> list[ProposedConstraint]:
        """Suggest ``arg_length_limit`` for free-text str params.

        Defends against prompt injection / runaway inputs where the agent
        inlines an entire payload into a tool argument.
        """
        results: list[ProposedConstraint] = []
        for tool in tools:
            for name, ann in _parse_params(tool.params):
                cap = _TEXT_PARAM_LIMITS.get(name.lower())
                if cap is None:
                    continue
                if not _annotation_is_str(ann):
                    continue
                results.append(
                    ProposedConstraint(
                        formula=arg_length_limit(tool.name, name, cap),
                        source=DiscoverySource.AUTO_EXTRACTED,
                        extractor="code_analysis_heuristic",
                        confidence=0.45,
                        status=ConstraintStatus.PROPOSED,
                        provenance=f"{tool.filepath}:{tool.line}",
                        nl_description=(
                            f"{tool.name}.{name} is a free-text input — cap "
                            f"length at {cap:,} chars to blunt "
                            "prompt-injection / runaway-payload attacks"
                        ),
                        evidence={
                            "args": [tool.name, name, cap],
                            "heuristic": "text_input_length",
                            "role": name.lower(),
                        },
                    )
                )
        return results

    def _gen_command_arg_blacklist(
        self, tools: list[ToolInfo]
    ) -> list[ProposedConstraint]:
        return self._param_role_blacklist(tools, role="command")

    def _gen_path_arg_blacklist(
        self, tools: list[ToolInfo]
    ) -> list[ProposedConstraint]:
        return self._param_role_blacklist(tools, role="path")

    def _gen_url_arg_blacklist(self, tools: list[ToolInfo]) -> list[ProposedConstraint]:
        return self._param_role_blacklist(tools, role="url")

    def _gen_sql_arg_blacklist(self, tools: list[ToolInfo]) -> list[ProposedConstraint]:
        # Only fires on tools whose name suggests SQL execution — a
        # generic ``query: str`` on a search tool would otherwise produce
        # noisy false positives.
        results: list[ProposedConstraint] = []
        for tool in tools:
            if not _SQL_TOOL_NAME_RE.search(_tool_text(tool)):
                continue
            for name, ann in _parse_params(tool.params):
                role = _PARAM_ROLE_ALIASES.get(name.lower())
                if role != "sql":
                    continue
                if not _annotation_is_str(ann):
                    continue
                patterns = _PARAM_BLACKLISTS["sql"]
                results.append(
                    self._make_blacklist_proposal(
                        tool, name, "sql", patterns, confidence=0.5
                    )
                )
        return results

    def _param_role_blacklist(
        self, tools: list[ToolInfo], *, role: str
    ) -> list[ProposedConstraint]:
        """Shared helper for command/path/url role-based blacklists."""
        patterns = _PARAM_BLACKLISTS.get(role)
        if not patterns:
            return []
        results: list[ProposedConstraint] = []
        for tool in tools:
            for name, ann in _parse_params(tool.params):
                if _PARAM_ROLE_ALIASES.get(name.lower()) != role:
                    continue
                if not _annotation_is_str(ann):
                    continue
                conf = 0.5 if role in {"command", "path"} else 0.45
                results.append(
                    self._make_blacklist_proposal(
                        tool, name, role, patterns, confidence=conf
                    )
                )
        return results

    @staticmethod
    def _make_blacklist_proposal(
        tool: "ToolInfo",
        param: str,
        role: str,
        patterns: list[str],
        confidence: float,
    ) -> ProposedConstraint:
        formula = arg_blacklist(tool.name, param, patterns)
        # Render the patterns list as a YAML inline list so it round-trips
        # correctly when ``generate_yaml`` writes ``args: [tool, param, [...]]``.
        return ProposedConstraint(
            formula=formula,
            source=DiscoverySource.AUTO_EXTRACTED,
            extractor="code_analysis_heuristic",
            confidence=confidence,
            status=ConstraintStatus.PROPOSED,
            provenance=f"{tool.filepath}:{tool.line}",
            nl_description=(
                f"{tool.name}.{param} looks like a {role} input — block "
                f"high-risk {role} patterns (e.g. {patterns[0]!r})"
            ),
            evidence={
                "args": [tool.name, param, patterns],
                "heuristic": f"{role}_blacklist",
            },
        )

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
            self._emit("LLM inference skipped: llm_extraction module not available")
            return []

        try:
            extractor = UnifiedExtractor(
                model=self._llm_model,
                api_key=self._api_key,
                client=self._client,
                provider=self._provider,
                base_url=self._base_url,
                use_structured_ir=self._use_ir,
            )
        except ImportError:
            logger.warning("openai not installed, skipping LLM pass")
            self._emit(
                "LLM inference skipped: openai/google-genai client not installed"
            )
            return []

        self._emit(
            f"Running LLM inference (model={self._llm_model or 'auto'}, "
            f"{len(tools)} tool(s), {len(sources)} source file(s))…"
        )
        _t0 = time.perf_counter()

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

            evidence: dict = {
                "pattern": r.pattern_name,
                "args": r.args,
                "source_quote": r.source_quote,
                "llm_model": self._llm_model,
            }
            # Preserve the raw assumption string so generate_yaml can emit
            # a round-trippable `A:` field alongside `E:`.
            assumption_raw = getattr(r, "assumption_raw", "") or ""
            if assumption_raw:
                evidence["assumption_raw"] = assumption_raw

            proposal = ProposedConstraint(
                source=DiscoverySource.AUTO_EXTRACTED,
                extractor="code_analysis_llm",
                confidence=r.confidence,
                status=ConstraintStatus.PROPOSED,
                provenance="LLM inference from tool inventory",
                nl_description=r.nl_description,
                evidence=evidence,
            )

            if r.constraint_type == "det":
                proposal.formula = r.compiled
                if r.compiled_assumption:
                    proposal.assumption = r.compiled_assumption
            else:
                proposal.sto = r.compiled

            proposals.append(proposal)

        elapsed = time.perf_counter() - _t0
        self._emit(
            f"LLM inference done in {elapsed:.1f}s "
            f"({len(proposals)} candidate contract(s) returned)"
        )
        return proposals

    # -----------------------------------------------------------------
    # Tool inventory export (for `sponsio scan`)
    # -----------------------------------------------------------------

    def get_tool_inventory(
        self, source_paths: list[str | Path]
    ) -> list[dict[str, Any]]:
        """Extract tool inventory without generating constraints.

        Useful for ``sponsio scan`` to show discovered tools
        before running LLM inference.

        Args:
            source_paths: Python source files or directories.

        Returns:
            List of tool info dicts with name, filepath, docstring, params.
        """
        all_tools: list[ToolInfo] = []

        from sponsio.discovery.loaders import iter_python_files

        for path_str in source_paths:
            path = Path(path_str)
            if path.is_dir():
                for py_file in iter_python_files(path):
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
    # YAML generation (for `sponsio scan`)
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
            lines.append("    # No constraints inferred — add your own.")
            lines.append("    # Each entry is an (A, E) pair; A is optional.")
            lines.append("    #")
            lines.append("    # Unconditional invariant (no precondition):")
            lines.append('    # - E: "tool `check_policy` must precede `issue_refund`"')
            lines.append("    # - E:")
            lines.append("    #     pattern: must_precede")
            lines.append("    #     args: [check_policy, issue_refund]")
            lines.append("    #")
            lines.append("    # Conditional rule (only enforced when A holds):")
            lines.append('    # - A: "called `modify_order`"')
            lines.append(
                '    #   E: "tool `get_order_details` must precede `modify_order`"'
            )
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
                    a_text = self._render_assumption(p)
                    if a_text:
                        # `A: ... E: ...` pair — confidence comment goes on
                        # the entry (`- A:`) line.
                        lines.append(f'      - A: "{a_text}"{confidence_tag}')
                        lines.append("        E:")
                        lines.append(f"          pattern: {pattern}")
                    else:
                        lines.append(f"      - E:{confidence_tag}")
                        lines.append(f"          pattern: {pattern}")

                    # Reconstruct args for the enforcement
                    if p.evidence and "args" in p.evidence:
                        args = p.evidence["args"]
                        lines.append(f"          args: {self._emit_yaml_list(args)}")
                    elif p.evidence:
                        caller = p.evidence.get("caller", "")
                        callee = p.evidence.get("callee", "")
                        if caller and callee:
                            lines.append(
                                f"          args: {self._emit_yaml_list([callee, caller])}"
                            )
                    if src_label:
                        lines.append(f"          source: {src_label}")
                elif p.sto:
                    a_text = self._render_assumption(p)
                    if a_text:
                        lines.append(f'      - A: "{a_text}"{confidence_tag}')
                        lines.append(f'        E: "{p.nl_description}"')
                    else:
                        lines.append(f'      - E: "{p.nl_description}"{confidence_tag}')

        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _emit_yaml_scalar(value: Any) -> str:
        """Render a single value as a YAML-safe scalar.

        Numbers / bools / None pass through.  Strings are bare-emitted
        when they're a plain identifier-like token; otherwise they're
        double-quoted with backslashes and quotes escaped so a regex
        like ``rm\\s+-rf`` survives a YAML round-trip intact.
        """
        if isinstance(value, bool) or value is None:
            return "true" if value is True else "false" if value is False else "null"
        if isinstance(value, (int, float)):
            return str(value)
        s = str(value)
        # Bare scalar safe?  Identifiers, dotted/underscored names, simple
        # numbers — anything without YAML-significant chars.
        if s and re.match(r"^[A-Za-z_][A-Za-z0-9_.\-]*$", s):
            return s
        # Quote and escape \ and "
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    @classmethod
    def _emit_yaml_list(cls, values: list[Any]) -> str:
        """Render a (possibly nested) list as a YAML flow sequence.

        Nested lists are recursed; each leaf goes through
        :py:meth:`_emit_yaml_scalar` so regex patterns survive.
        """
        parts: list[str] = []
        for v in values:
            if isinstance(v, list):
                parts.append(cls._emit_yaml_list(v))
            else:
                parts.append(cls._emit_yaml_scalar(v))
        return "[" + ", ".join(parts) + "]"

    @staticmethod
    def _render_assumption(p: ProposedConstraint) -> str:
        """Return a YAML-safe assumption string, or "" when none.

        Preference order:

        1. Raw assumption text preserved by the LLM extractor in
           ``evidence["assumption_raw"]`` (round-trippable LTL/NL).
        2. ``assumption.desc`` with the ``"assumes: "`` prefix stripped
           (human-readable fallback for extractors that didn't preserve
           the raw form).

        Quotes inside the string are escaped so the output stays valid
        YAML when wrapped in double quotes by the caller.
        """
        if p.assumption is None:
            return ""
        raw = ""
        if p.evidence and isinstance(p.evidence, dict):
            raw = (p.evidence.get("assumption_raw") or "").strip()
        if not raw:
            desc = getattr(p.assumption, "desc", "") or ""
            raw = desc.removeprefix("assumes: ").strip()
        if not raw:
            return ""
        return raw.replace("\\", "\\\\").replace('"', '\\"')

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
            self._emit("Policy extraction skipped: DocumentExtractor not available")
            return []

        if not self._use_llm:
            # Policy extraction is LLM-only; surface this clearly instead of
            # silently returning empty results.
            self._emit(
                f"Policy extraction skipped: --llm flag not set "
                f"({len(policy_paths)} policy file(s) ignored)"
            )
            return []

        extractor = DocumentExtractor(
            model=self._llm_model,
            api_key=self._api_key,
            provider=self._provider,
            base_url=self._base_url,
        )

        self._emit(
            f"Extracting from {len(policy_paths)} policy doc(s) "
            f"using {self._llm_model or 'auto'}…"
        )
        _t0 = time.perf_counter()
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
                    self._emit(f"Policy extraction failed for {path}: {e}")

        elapsed = time.perf_counter() - _t0
        self._emit(
            f"Policy extraction done in {elapsed:.1f}s "
            f"({len(proposals)} candidate contract(s) returned)"
        )
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
