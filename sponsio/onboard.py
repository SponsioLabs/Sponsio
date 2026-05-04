"""``sponsio onboard`` — one-shot project wire-up.

Composes the existing building blocks (``init`` wizard, ``scan`` via
:class:`CodeAnalyzer`, starter-pack, ``doctor``) into a single command
that takes a repo path and leaves the user with:

1. A valid ``sponsio.yaml`` in ``mode: observe``.
2. A populated ``agents.<id>.contracts:`` block — either LLM-inferred
   (when a provider key is available or Ollama is running locally) or
   name-heuristic from :mod:`sponsio.discovery.starter_pack` (pure AST
   fallback).
3. A printed 2-line patch showing exactly where to insert the
   framework-specific ``Sponsio(...)`` factory into the user's agent
   entry point.

Why a separate module?  ``cli.py`` is already ~2600 lines and each of
the three detections (framework, provider, reachable Ollama) has its
own failure mode that's cleaner to unit-test against a pure function
than against a Click ``CliRunner`` harness.  The CLI wrapper stays
~30 lines and forwards flags into :func:`run_onboard`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Framework detection
# ---------------------------------------------------------------------------

# Order matters — the first match wins.  More specific frameworks
# (e.g. ``claude_agent`` which uses the Anthropic SDK) must come
# before their generic parents (``anthropic``/``openai``).
_FRAMEWORK_IMPORT_SIGNATURES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # framework_id, (python import prefixes that imply the framework)
    #
    # Each prefix list has two flavours:
    #   1. The framework's own import path (``langgraph`` / ``crewai`` /
    #      ``claude_agent_sdk`` / ...) — strongest evidence, this is
    #      how greenfield projects declare a framework.
    #   2. The matching ``sponsio.<adapter>`` import — the wrap snippet
    #      onboard prints lands here, so once the user has pasted it
    #      we can re-detect the framework even if their app doesn't
    #      directly import the underlying SDK (common in scripted
    #      demos that mock out the framework client and rely solely
    #      on Sponsio's adapter for the agent surface).
    ("langgraph", ("langgraph", "sponsio.langgraph")),
    # ``langchain_core`` is the modern split (langchain ≥ 0.1):
    # ``from langchain_core.tools import tool`` is now the canonical
    # import path and the bare ``langchain.`` prefix doesn't match it.
    ("langchain", ("langchain.", "langchain_core")),
    ("claude_agent", ("claude_agent_sdk", "anthropic.agents", "sponsio.claude_agent")),
    ("google_adk", ("google.adk", "sponsio.google_adk")),
    ("crewai", ("crewai", "sponsio.crewai")),
    (
        "openai_agents",
        (
            "openai_agents",
            "agents.",
            "sponsio.agents",
        ),
    ),
    ("openai", ("openai", "sponsio.openai")),
    # placeholder; JS-only, rarely reachable from .py
    ("vercel_ai", ("ai ", "sponsio.vercel_ai")),
    ("mcp", ("mcp.", "sponsio.mcp")),
)

# Pyproject / requirements package-name hints.  Used when import grep
# finds nothing (e.g. monorepos where the agent is in a submodule we
# didn't point at).
_FRAMEWORK_DEPENDENCY_HINTS: dict[str, tuple[str, ...]] = {
    "langgraph": ("langgraph",),
    "langchain": ("langchain",),
    "claude_agent": ("claude-agent-sdk", "claude_agent_sdk"),
    "google_adk": ("google-adk", "google_adk"),
    "crewai": ("crewai",),
    "openai_agents": ("openai-agents", "openai_agents"),
    "openai": ("openai",),
    "mcp": ("mcp", "modelcontextprotocol"),
}

# TypeScript / JS dependency hints — same shape, scanned out of
# ``package.json``'s ``dependencies`` / ``devDependencies`` blocks.
# Order matches :data:`_FRAMEWORK_IMPORT_SIGNATURES` so when both
# package.json AND import-grep find a hit, ranking stays stable.
# ``vercel_ai`` is TS-only on the Sponsio side today (no Python
# adapter), but listed here because the runtime is real.
_FRAMEWORK_TS_DEPENDENCY_HINTS: dict[str, tuple[str, ...]] = {
    "langgraph": ("@langchain/langgraph", "langgraph"),
    "langchain": ("@langchain/core", "@langchain/community", "langchain"),
    "vercel_ai": ("ai", "@ai-sdk/anthropic", "@ai-sdk/openai", "@ai-sdk/google"),
    "claude_agent": ("@anthropic-ai/claude-agent-sdk", "@anthropic-ai/sdk"),
    "openai_agents": ("@openai/agents",),
    "openai": ("openai",),
    "mcp": ("@modelcontextprotocol/sdk",),
}

# Mapping from detected framework → the Sponsio factory the user
# should import.  This is the only place that knows which
# subpackage name each adapter lives in.
_FRAMEWORK_FACTORY: dict[str, str] = {
    "langgraph": "sponsio.langgraph",
    "langchain": "sponsio.langgraph",  # LC tools plug into the same adapter
    "claude_agent": "sponsio.claude_agent",
    "google_adk": "sponsio.google_adk",
    "crewai": "sponsio.crewai",
    "openai_agents": "sponsio.agents",
    "openai": "sponsio.openai",
    "vercel_ai": "sponsio.vercel_ai",
    "mcp": "sponsio.mcp",
    "none": "sponsio",  # generic — guard_before/guard_after loop
}

# Files we scan for import signatures.  Capped at a few thousand
# lines total so onboarding stays instant on mono-repos.
_IMPORT_SCAN_MAX_FILES = 200
_IMPORT_SCAN_MAX_BYTES = 2_000_000


@dataclass
class FrameworkHint:
    """Outcome of :func:`detect_framework`.

    Attributes:
        framework: Canonical id (``langgraph`` / ``openai`` / ...) or
            ``"none"`` if nothing matched.
        factory: Dotted import path for the Sponsio adapter to use.
        evidence: Human-readable reason we picked this — printed in
            the onboard banner and included in ``--json`` output.
        entry_file: First file we saw evidence in, if any.  Used as
            the default location to print the wrap patch against.
    """

    framework: str = "none"
    factory: str = "sponsio"
    evidence: str = "no framework imports detected"
    entry_file: Path | None = None


def _iter_py_files(root: Path) -> list[Path]:
    """List .py files under ``root``, skipping the usual dependency dirs.

    Delegates the skip-set to :func:`sponsio.discovery.loaders.iter_python_files`
    so framework detection and the AST tool extractor walk the same
    files.  Previously each had its own filter — onboard's was the
    smaller of the two — and a monorepo with ~250 unrelated ``.py``
    files at the root could exhaust :data:`_IMPORT_SCAN_MAX_FILES`
    before the agent file in a deep subdir was reached, leaving
    ``framework: none`` even though ``tool_inventory`` had already
    found ``@tool`` functions in that exact file.

    Bounded by :data:`_IMPORT_SCAN_MAX_FILES` so a 100k-file
    monorepo doesn't turn ``sponsio onboard`` into a 20-second
    operation — we only need representative imports, not every one.
    Callers that already know which files contain agent code (after
    running tool inventory) should pass them via
    ``detect_framework(..., prioritize_files=...)`` so they're
    scanned before the cap kicks in.
    """
    if root.is_file():
        return [root] if root.suffix == ".py" else []

    from sponsio.discovery.loaders import iter_python_files

    return iter_python_files(root)[:_IMPORT_SCAN_MAX_FILES]


def detect_framework(
    root: Path,
    *,
    prioritize_files: list[Path] | None = None,
) -> FrameworkHint:
    """Identify the agent framework used in the project.

    Two-stage detection:

    1. **Import grep.**  Scan a bounded set of ``.py`` files under
       ``root`` for ``import <framework>`` / ``from <framework>``
       lines.  This is the highest-signal check because framework
       imports actually imply framework usage.
    2. **Dependency declaration fallback.**  When import grep finds
       nothing (rare but happens on generated code / stubbed monorepos),
       parse ``pyproject.toml`` / ``requirements.txt`` / ``Pipfile``
       for declared dependencies and map those onto frameworks.

    Returns ``FrameworkHint(framework="none", ...)`` when both stages
    fail.  ``none`` is a valid choice — the generic
    ``sponsio.Sponsio(...)`` + ``guard.guard_before/after`` pattern
    works for custom function-calling loops.
    """
    bounded = _iter_py_files(root)
    if prioritize_files:
        # Walk priority files first so the cap can never starve us of
        # the file that actually carries the framework import.  Dedup
        # by resolved path (priority + bounded scan often overlap on
        # small projects).
        seen: set[Path] = set()
        files: list[Path] = []
        for p in list(prioritize_files) + bounded:
            try:
                key = p.resolve()
            except OSError:
                key = p
            if key in seen:
                continue
            seen.add(key)
            files.append(p)
        files = files[:_IMPORT_SCAN_MAX_FILES]
    else:
        files = bounded

    total_bytes = 0
    per_framework_hits: dict[str, tuple[int, Path]] = {}
    import_re = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][\w.]*)")

    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        total_bytes += len(text)
        if total_bytes > _IMPORT_SCAN_MAX_BYTES:
            break
        for line in text.splitlines():
            m = import_re.match(line)
            if not m:
                continue
            imp = m.group(1) + " "  # sentinel space so prefix match is anchored
            for fw_id, prefixes in _FRAMEWORK_IMPORT_SIGNATURES:
                for prefix in prefixes:
                    if imp.startswith(prefix):
                        prev_count, _prev_file = per_framework_hits.get(fw_id, (0, f))
                        per_framework_hits[fw_id] = (prev_count + 1, f)
                        break

    if per_framework_hits:
        # Priority = signature declaration order.  If LangGraph and
        # LangChain both appear, LangGraph wins (listed first) — it's
        # the more specific adapter.
        for fw_id, _prefixes in _FRAMEWORK_IMPORT_SIGNATURES:
            if fw_id in per_framework_hits:
                count, hit_file = per_framework_hits[fw_id]
                return FrameworkHint(
                    framework=fw_id,
                    factory=_FRAMEWORK_FACTORY[fw_id],
                    evidence=(
                        f"found {count} `{fw_id}` import(s) (first: {hit_file.name})"
                    ),
                    entry_file=hit_file,
                )

    # Stage 2 — dependency declarations
    dep_hit = _detect_from_dependencies(root)
    if dep_hit is not None:
        return dep_hit

    return FrameworkHint(
        framework="none",
        factory=_FRAMEWORK_FACTORY["none"],
        evidence="no framework imports or dependency hints found",
        entry_file=None,
    )


def _detect_from_dependencies(root: Path) -> FrameworkHint | None:
    """Read ``pyproject.toml`` / ``requirements.txt`` / ``package.json``
    for framework deps.

    Text-only grep — we don't TOML / JSON parse because (a) cheaper
    and (b) a malformed manifest that the package manager still
    accepts shouldn't make onboarding crash.

    Python and TypeScript hints are kept in two parallel tables so
    each ecosystem's package names live in one place.  Iteration
    order is identical (declared via :data:`_FRAMEWORK_IMPORT_SIGNATURES`)
    so when a polyglot repo has both a ``pyproject.toml`` AND a
    ``package.json`` claiming the same framework, the more specific
    one (langgraph > langchain etc.) still wins.
    """
    py_candidates = [
        root / "pyproject.toml",
        root / "requirements.txt",
        root / "requirements-dev.txt",
        root / "Pipfile",
    ]
    ts_candidates = [
        root / "package.json",
    ]

    # Python first — preserves the historical priority that a Python
    # project with both py + ts manifests should resolve to its py
    # framework (the ts one is usually frontend tooling, not the
    # agent itself).
    for path in py_candidates:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            continue
        for fw_id, _prefixes in _FRAMEWORK_IMPORT_SIGNATURES:
            hints = _FRAMEWORK_DEPENDENCY_HINTS.get(fw_id, ())
            for h in hints:
                if h.lower() in text:
                    return FrameworkHint(
                        framework=fw_id,
                        factory=_FRAMEWORK_FACTORY[fw_id],
                        evidence=f"found `{h}` in {path.name}",
                        entry_file=None,
                    )

    for path in ts_candidates:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            continue
        for fw_id, _prefixes in _FRAMEWORK_IMPORT_SIGNATURES:
            hints = _FRAMEWORK_TS_DEPENDENCY_HINTS.get(fw_id, ())
            for h in hints:
                # Quote-anchor the substring search — looking for
                # ``"@ai-sdk/anthropic"`` rather than the bare name
                # avoids accidental matches on substrings of unrelated
                # values (e.g. "ai" matching "tail" or "main").
                quoted = f'"{h.lower()}"'
                if quoted in text:
                    return FrameworkHint(
                        framework=fw_id,
                        factory=_FRAMEWORK_FACTORY[fw_id],
                        evidence=f"found `{h}` in {path.name}",
                        entry_file=None,
                    )
    return None


# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------


@dataclass
class ProviderHint:
    """Outcome of :func:`detect_provider`.

    ``provider`` is one of ``openai`` / ``anthropic`` / ``gemini`` /
    ``bedrock`` / ``ollama`` / ``none`` — matches
    ``_PROVIDER_DEFAULTS`` in ``init_wizard`` plus ``"ollama"`` which
    routes through the ``base_url`` mechanism.
    """

    provider: str = "none"
    env_var: str | None = None
    base_url: str | None = None
    model: str | None = None
    evidence: str = "no provider credentials detected"


_OLLAMA_URL_DEFAULT = "http://localhost:11434"


def detect_provider(
    *,
    probe_ollama: bool = True,
    ollama_url: str = _OLLAMA_URL_DEFAULT,
    probe_timeout_s: float = 0.5,
) -> ProviderHint:
    """Find the cheapest usable LLM provider for the user.

    Priority (highest → lowest):

    1. ``GOOGLE_API_KEY`` / ``GEMINI_API_KEY`` — Gemini has a 1500
       req/day free tier, so this beats paid keys when both are set.
    2. ``ANTHROPIC_API_KEY`` — Claude.
    3. ``OPENAI_API_KEY`` — GPT.
    4. ``OPENAI_BASE_URL`` — assume a user-configured OpenAI-compatible
       endpoint (OpenRouter / Azure / DeepSeek / Together / vLLM / ...).
    5. Local Ollama on :11434 — free, private, and often already
       running on developer laptops.  Skipped when ``probe_ollama`` is
       False (unit tests shouldn't hit the network).
    6. ``none`` — caller falls back to the starter pack.

    The probe is cheap (sub-second, single GET) but still guarded by
    ``probe_timeout_s`` so a mis-routed DNS entry doesn't stall
    onboarding.
    """
    if os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"):
        env = "GOOGLE_API_KEY" if os.environ.get("GOOGLE_API_KEY") else "GEMINI_API_KEY"
        # ``SPONSIO_EXTRACTOR_MODEL`` lets per-project workflows pin
        # a specific Gemini model.  Default is ``gemini-2.5-flash`` —
        # the lighter ``flash-lite`` consistently missed numeric-
        # threshold rules (the ``arg_numeric(wire_transfer, amount)
        # <= 50000`` case in the wire-transfer demo was the canonical
        # repro).  Trading ~13s of extra latency on the one-off
        # onboard call for stable contract coverage is the right
        # call; users who specifically need lite's ~3s latency (CI
        # smoke tests, "I'll re-run with flash later") set the env
        # var to override.
        model = os.environ.get("SPONSIO_EXTRACTOR_MODEL", "gemini-2.5-flash")
        return ProviderHint(
            provider="gemini",
            env_var=env,
            model=model,
            evidence=f"{env} set (1500 req/day free tier)",
        )

    if os.environ.get("ANTHROPIC_API_KEY"):
        return ProviderHint(
            provider="anthropic",
            env_var="ANTHROPIC_API_KEY",
            model="claude-3-5-sonnet-20241022",
            evidence="ANTHROPIC_API_KEY set",
        )

    if os.environ.get("OPENAI_API_KEY"):
        return ProviderHint(
            provider="openai",
            env_var="OPENAI_API_KEY",
            model="gpt-4o-mini",
            evidence="OPENAI_API_KEY set",
        )

    base_url = os.environ.get("OPENAI_BASE_URL")
    if base_url:
        return ProviderHint(
            provider="openai",
            env_var="OPENAI_API_KEY",
            base_url=base_url,
            model="gpt-4o-mini",
            evidence=f"OPENAI_BASE_URL={base_url} (custom endpoint)",
        )

    if probe_ollama and _ollama_reachable(ollama_url, probe_timeout_s):
        model = _ollama_pick_model(ollama_url, probe_timeout_s)
        return ProviderHint(
            provider="ollama",
            env_var=None,
            base_url=f"{ollama_url.rstrip('/')}/v1",
            model=model or "llama3.1",
            evidence=(
                f"Ollama reachable on {ollama_url} (model: {model or 'llama3.1'})"
            ),
        )

    return ProviderHint()


def _ollama_reachable(url: str, timeout_s: float) -> bool:
    """Best-effort liveness probe — never raises, never blocks long."""
    try:
        import httpx
    except ImportError:
        return False
    try:
        r = httpx.get(
            f"{url.rstrip('/')}/api/tags",
            timeout=timeout_s,
        )
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


def _ollama_pick_model(url: str, timeout_s: float) -> str | None:
    """Pick a sensible model from the Ollama installation.

    Prefers llama3.* (widely available, strong enough for extraction),
    then qwen2.5, then whatever's first in the list.  Returns None on
    any network error — caller defaults to ``llama3.1`` so `sponsio
    scan` tries the standard tag and surfaces a clean "pull this
    first" error if the user doesn't actually have it yet.
    """
    try:
        import httpx
    except ImportError:
        return None
    try:
        r = httpx.get(f"{url.rstrip('/')}/api/tags", timeout=timeout_s)
        if r.status_code != 200:
            return None
        models = [m.get("name") for m in r.json().get("models", []) if m.get("name")]
    except Exception:  # noqa: BLE001
        return None

    if not models:
        return None

    preferred_prefixes = ("llama3.1", "llama3", "qwen2.5", "qwen2", "mistral")
    for prefix in preferred_prefixes:
        for m in models:
            if m.startswith(prefix):
                return m
    return models[0]


# ---------------------------------------------------------------------------
# Pack auto-selection
# ---------------------------------------------------------------------------


# Tool-name → pack tool slot.  Each pack ships rules keyed on a
# specific generic name (``exec`` for the shell pack, ``read`` /
# ``write`` / ``edit`` / ``apply_patch`` for the filesystem pack).
# If a host project has a tool whose lowercased name matches one of
# these aliases but isn't the canonical pack name, we emit a
# ``tool_rename:`` so the pack rules apply to the host's actual tool.
#
# The aliases are deliberately conservative — only names that are
# *unambiguously* the same capability.  ``run`` could mean shell or
# could mean "kick off a workflow"; we don't include it.
_SHELL_TOOL_ALIASES: dict[str, frozenset[str]] = {
    "exec": frozenset(
        {
            "bash",
            "shell",
            "exec",
            "execute",
            "execute_command",
            "run_command",
            "run_shell",
            "run_bash",
            "terminal",
            "subprocess",
        }
    ),
}

_FS_TOOL_ALIASES: dict[str, frozenset[str]] = {
    "read": frozenset(
        {"read", "read_file", "file_read", "open_file", "cat", "view_file", "load_file"}
    ),
    "write": frozenset(
        {"write", "write_file", "file_write", "save_file", "create_file"}
    ),
    "edit": frozenset({"edit", "edit_file", "modify_file", "file_edit", "update_file"}),
    "apply_patch": frozenset(
        {"apply_patch", "apply_patch_file", "patch_file", "apply_diff", "patch"}
    ),
}


@dataclass
class PackSelection:
    """Outcome of :func:`select_packs`.

    Attributes:
        packs: ordered include specs (e.g. ``["sponsio:core/universal",
            "sponsio:capability/shell"]``).  Order matches what the
            user will see in YAML — universal first, capability packs
            after.
        tool_rename: mapping from pack-canonical name (``exec``,
            ``read``, …) to the host's actual tool name.  Empty when
            the host happens to use the same names the packs use.
        needs_workspace: True iff a pack with ``<workspace>/``
            placeholders is included.  No longer set by the
            auto-included ``capability/filesystem`` pack (workspace
            rules now live in opt-in ``capability/filesystem-strict``).
            Kept for forward-compat with future packs that need
            workspace resolution + for explicit-include callers.
        evidence: list of human-readable reasons, one per pack —
            displayed in the onboard banner so users can see *why*
            each pack was selected.
    """

    packs: list[str] = field(default_factory=list)
    tool_rename: dict[str, str] = field(default_factory=dict)
    needs_workspace: bool = False
    evidence: list[str] = field(default_factory=list)


def _match_alias(tool_name: str, alias_table: dict[str, frozenset[str]]) -> str | None:
    """Return the pack-canonical name (table key) when ``tool_name``
    matches one of the aliases; None otherwise.  Lowercases the
    needle so ``RunCommand`` and ``run_command`` collapse onto the
    same alias."""
    needle = tool_name.lower()
    for canonical, aliases in alias_table.items():
        if needle in aliases:
            return canonical
    return None


def select_packs(
    framework: str,
    tool_inventory: list[dict] | None,
) -> PackSelection:
    """Pick contract packs to auto-include based on framework + tools.

    Heuristics (intentionally conservative — the bias is towards
    "include packs that obviously help, skip everything else"):

    * **``sponsio:core/universal``** — always.  Provides PII /
      injection-free output guards that apply to any LLM agent.
    * **``sponsio:capability/shell``** — when any tool name matches
      a shell-execution alias (``bash`` / ``exec`` / ``run_command``
      / …).
    * **``sponsio:capability/filesystem``** — when any tool name
      matches a filesystem alias (``read_file`` / ``write_file`` /
      ``edit_file`` / ``apply_patch``).

    Skipped on purpose:

    * **``sponsio:incident/openclaw``** — pinned to a specific
      vendor incident; teams should opt in deliberately, not by
      heuristic.

    Tool-rename pre-population: when a capability pack is selected
    but the host's tool name differs from the pack's canonical
    name, populate ``tool_rename:`` so the included rules apply
    out of the box (``{exec: bash}`` etc.).
    """
    sel = PackSelection()
    tool_inventory = tool_inventory or []
    tool_names = [t.get("name", "") for t in tool_inventory if t.get("name")]

    sel.packs.append("sponsio:core/universal")
    sel.evidence.append("universal: applies to every LLM agent")

    # ``sponsio:core/runaway`` used to be auto-included for agentic-
    # loop frameworks (langgraph / crewai / etc.), shipping hard-
    # coded token budgets + loop / delegation caps.  Those defaults
    # were arbitrary (200k tokens, depth 5, ...) and produced noisy
    # contracts every user had to override.  The pack is now empty
    # by design — see ``sponsio/contracts/core/runaway.yaml`` for the
    # rationale and the patterns users should reach for instead.

    # Shell pack
    shell_renames: dict[str, str] = {}
    for name in tool_names:
        canonical = _match_alias(name, _SHELL_TOOL_ALIASES)
        if canonical and canonical not in shell_renames:
            shell_renames[canonical] = name
    if shell_renames:
        sel.packs.append("sponsio:capability/shell")
        matched = ", ".join(f"{k}->{v}" for k, v in shell_renames.items())
        sel.evidence.append(f"shell: tools resemble exec ({matched})")
        for k, v in shell_renames.items():
            if k != v:
                sel.tool_rename[k] = v

    # Filesystem pack
    fs_renames: dict[str, str] = {}
    for name in tool_names:
        canonical = _match_alias(name, _FS_TOOL_ALIASES)
        if canonical and canonical not in fs_renames:
            fs_renames[canonical] = name
    if fs_renames:
        sel.packs.append("sponsio:capability/filesystem")
        matched = ", ".join(f"{k}->{v}" for k, v in fs_renames.items())
        sel.evidence.append(f"filesystem: tools resemble file IO ({matched})")
        # Auto-include is the BASE ``capability/filesystem`` pack only,
        # which carries credential-shape blacklists + ordering rules
        # (no <workspace>/ placeholders).  ``capability/filesystem-strict``
        # — the workspace-bounding scope_limit rules — is opt-in: users
        # who actually want strict workspace bounding add it by hand
        # along with a ``workspace:`` line.  This avoids the false-
        # positive trace noise and load-time error users hit when
        # auto-include pulled in workspace rules without a workspace.
        for k, v in fs_renames.items():
            if k != v:
                sel.tool_rename[k] = v

    return sel


# ---------------------------------------------------------------------------
# YAML composition
# ---------------------------------------------------------------------------


@dataclass
class OnboardReport:
    """Machine-readable summary of what ``sponsio onboard`` did.

    Returned from :func:`run_onboard` (tests assert on this shape) and
    serialized to stdout when ``--json`` is passed.
    """

    out_path: Path
    agent_id: str
    framework: FrameworkHint
    provider: ProviderHint
    mode: str = "observe"
    tools_count: int = 0
    contracts_count: int = 0
    starter_pack_used: bool = False
    wrap_snippet: str = ""
    warnings: list[str] = field(default_factory=list)
    # Populated only when ``run_doctor=True`` (the CLI default).  A
    # list of :class:`sponsio.doctor.CheckResult` plus the doctor
    # exit code.  Tests can assert on the count of failed checks.
    doctor_results: list | None = None
    doctor_exit_code: int | None = None
    # Populated by :func:`select_packs`.  None preserves the older
    # report shape for callers that don't care about pack picks;
    # the field is present whenever ``run_onboard`` ran the
    # selection step (always, currently — kept Optional only for
    # forward compat with `auto_select=False` flag we may add).
    pack_selection: PackSelection | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "out_path": str(self.out_path),
            "agent_id": self.agent_id,
            "framework": {
                "framework": self.framework.framework,
                "factory": self.framework.factory,
                "evidence": self.framework.evidence,
                "entry_file": (
                    str(self.framework.entry_file)
                    if self.framework.entry_file
                    else None
                ),
            },
            "provider": {
                "provider": self.provider.provider,
                "env_var": self.provider.env_var,
                "base_url": self.provider.base_url,
                "model": self.provider.model,
                "evidence": self.provider.evidence,
            },
            "mode": self.mode,
            "tools_count": self.tools_count,
            "contracts_count": self.contracts_count,
            "starter_pack_used": self.starter_pack_used,
            "wrap_snippet": self.wrap_snippet,
            "warnings": list(self.warnings),
        }
        if self.pack_selection is not None:
            d["packs"] = {
                "selected": list(self.pack_selection.packs),
                "tool_rename": dict(self.pack_selection.tool_rename),
                "needs_workspace": self.pack_selection.needs_workspace,
                "evidence": list(self.pack_selection.evidence),
            }
        if self.doctor_results is not None:
            d["doctor"] = {
                "exit_code": self.doctor_exit_code,
                "checks": [
                    {
                        "name": r.name,
                        "status": r.status,
                        "detail": r.detail,
                    }
                    for r in self.doctor_results
                ],
            }
        return d


def _emit_pack_block(
    selection: PackSelection,
    workspace: str | None,
) -> list[str]:
    """Render the ``include:`` / ``workspace:`` / ``tool_rename:`` lines
    that get spliced under the agent block.

    The lines are agent-scoped: indented ``    `` (4 spaces, the
    agent block's body indent in the scan YAML).  Each pack has a
    one-line comment explaining why it was selected so users
    reading the YAML understand the picks without consulting docs.
    Returns an empty list when no packs were selected (only happens
    in edge cases where the universal pack itself fails to register).
    """
    if not selection.packs:
        return []

    out: list[str] = []
    if workspace is not None and selection.needs_workspace:
        out.append("    # Project root — resolves the `<workspace>/` placeholder")
        out.append("    # used by the filesystem pack rules below.  Edit if your")
        out.append("    # agent runs from a different directory at deploy time.")
        out.append(f'    workspace: "{workspace}"')
        out.append("")

    if selection.tool_rename:
        out.append("    # Maps the packs' canonical tool names onto the names your")
        out.append("    # codebase actually uses.  Pre-populated from the scanned")
        out.append("    # tool inventory; remove an entry if it's wrong.")
        out.append("    tool_rename:")
        for canonical, host in selection.tool_rename.items():
            out.append(f"      {canonical}: {host}")
        out.append("")

    out.append("    # Auto-selected contract packs.  Each pack ships a curated set")
    out.append("    # of rules; use `customized:` below to disable individual ones")
    out.append("    # without forking the pack.")
    out.append("    include:")
    for pack, why in zip(selection.packs, selection.evidence):
        out.append(f"      - {pack}  # {why}")
    out.append("")
    return out


def _splice_pack_block_into_agent(
    yaml_text: str,
    agent_id: str,
    pack_lines: list[str],
) -> str:
    """Insert the pack block immediately after the agent's header line.

    The scan YAML shape we splice into is::

        agents:
          <agent_id>:
            contracts:
              - E: ...

    We want the result to be::

        agents:
          <agent_id>:
            include:
              - sponsio:core/universal
            contracts:
              - E: ...

    Splicing right after ``  <agent_id>:`` keeps ``include:`` adjacent
    to ``workspace:`` and ``tool_rename:`` (the same agent-scoped
    settings cluster together — better for scanning).  No-op when
    ``pack_lines`` is empty.
    """
    if not pack_lines:
        return yaml_text
    lines = yaml_text.split("\n")
    agent_header = f"  {agent_id}:"
    for i, ln in enumerate(lines):
        if ln.rstrip() == agent_header:
            lines = lines[: i + 1] + pack_lines + lines[i + 1 :]
            return "\n".join(lines)
    return yaml_text


def _compose_yaml(
    *,
    provider: ProviderHint,
    mode: str,
    agent_id: str,
    scan_yaml: str,
) -> str:
    """Merge an ``init``-style header onto a ``scan``-style body.

    Strategy: we generate the scan YAML first (with its tools+agents
    block populated), then *prepend* an extractor/judge/defaults block
    that matches what ``sponsio init`` would have written.  This means:

    * The file has the same top-level layout as ``init`` + ``scan`` ran
      separately (so users who already know one command know the file).
    * We never double-emit ``version:`` — we strip scan's ``version:``
      line since the init header writes its own canonical form.
    """
    lines: list[str] = ["version: 1", ""]

    if provider.provider == "ollama":
        lines.append("# Parse-time LLM via local Ollama.  Free & private, but the")
        lines.append("# starter-pack rules below remain useful even if the daemon")
        lines.append("# is offline when `sponsio scan --refresh` runs.")
        lines.append("extractor:")
        lines.append("  provider: openai")  # OpenAI-compatible schema
        lines.append(f"  model: {provider.model or 'llama3.1'}")
        lines.append(f"  base_url: {provider.base_url}")
        lines.append("")
    elif provider.provider in {"openai", "anthropic", "gemini"}:
        lines.append("# Parse-time LLM (used by `sponsio scan` to turn code/docs into")
        lines.append(
            "# contracts).  Offline & one-shot — favour accuracy over latency."
        )
        lines.append("extractor:")
        lines.append(f"  provider: {provider.provider}")
        if provider.model:
            lines.append(f"  model: {provider.model}")
        if provider.env_var:
            lines.append(f"  api_key: ${{{provider.env_var}}}")
        if provider.base_url:
            lines.append(f"  base_url: {provider.base_url}")
        lines.append("")
    else:
        # No provider configured — still emit an empty stanza so the
        # user can see where to add one.  `sponsio scan --refresh`
        # picks up a newly-added block without the user having to
        # re-run `onboard`.
        lines.append(
            "# No LLM configured — `sponsio scan` currently runs AST + starter-pack"
        )
        lines.append("# only.  To enable richer inference, uncomment and fill in:")
        lines.append("# extractor:")
        lines.append("#   provider: gemini   # 1500 req/day free tier")
        lines.append("#   api_key: ${GOOGLE_API_KEY}")
        lines.append("")

    lines.append(
        "# Runtime sto-judge (evaluates stochastic atoms like `injection_free`)"
    )
    lines.append("# on the agent's hot path.  Favour cheap+fast model; fault tolerance")
    lines.append("# matters because LLM outages must NOT cascade into agent outages.")
    lines.append("judge:")
    if provider.provider in {"openai", "anthropic", "gemini"} and provider.env_var:
        lines.append(f"  provider: {provider.provider}")
        if provider.model:
            lines.append(f"  model: {provider.model}")
        lines.append(f"  api_key: ${{{provider.env_var}}}")
    lines.append("  fallback_mode: allow  # allow|deny|skip on judge failure")
    lines.append("  circuit_breaker: true")
    lines.append("")

    lines.append("defaults:")
    lines.append(f"  mode: {mode}  # observe|enforce — observe = shadow (safe default)")
    lines.append("")

    # Drop every line from the scan output until we hit ``tools:`` or
    # ``agents:``.  That covers scan's version banner + "Generated by"
    # comments without relying on ordering heuristics — the only
    # top-level keys scan emits are ``version:``, ``tools:`` and
    # ``agents:``, and we keep the latter two.
    body_lines: list[str] = []
    capture = False
    for ln in scan_yaml.splitlines():
        if not capture:
            stripped = ln.lstrip()
            if stripped.startswith(("tools:", "agents:")):
                capture = True
        if capture:
            body_lines.append(ln)

    # Rename scan's default agent id ("agent") to the one chosen by
    # the caller.  Done textually because CodeAnalyzer bakes the id
    # directly into the YAML; parsing + re-emitting would cost more
    # code than it saves.
    body_text = "\n".join(body_lines)
    body_text = re.sub(
        r"^(agents:\s*\n  )agent(:\s*\n)",
        rf"\1{agent_id}\2",
        body_text,
        count=1,
        flags=re.MULTILINE,
    )

    return "\n".join(lines) + "\n" + body_text.lstrip("\n") + "\n"


def _wrap_snippet(framework: str, agent_id: str) -> str:
    """Return the 2-4 line patch the user needs to apply.

    Framework-specific so the user sees the factory name that matches
    their stack.  Kept short — the snippet is meant to be copy-pasted
    or applied by a coding-agent tool call, not read linearly.
    """
    snippets: dict[str, str] = {
        "langgraph": (
            f"from sponsio.langgraph import Sponsio\n"
            f'guard = Sponsio(config="sponsio.yaml", agent_id="{agent_id}")\n'
            f"agent = create_react_agent(model, guard.wrap(tools))"
        ),
        "langchain": (
            f"from sponsio.langgraph import Sponsio\n"
            f'guard = Sponsio(config="sponsio.yaml", agent_id="{agent_id}")\n'
            f"# pass guard.wrap(tools) to your agent constructor:\n"
            f"#   agent = create_react_agent(model, guard.wrap(tools))\n"
            f"#   # or: AgentExecutor(agent=..., tools=guard.wrap(tools))"
        ),
        "claude_agent": (
            f"from sponsio.claude_agent import Sponsio\n"
            f'guard = Sponsio(config="sponsio.yaml", agent_id="{agent_id}")\n'
            f"# add `guard.hooks()` to your ClaudeAgentOptions.hooks"
        ),
        "crewai": (
            f"from sponsio.crewai import Sponsio\n"
            f'guard = Sponsio(config="sponsio.yaml", agent_id="{agent_id}")\n'
            f"crew = guard.wrap(crew)"
        ),
        "google_adk": (
            f"from sponsio.google_adk import Sponsio\n"
            f'guard = Sponsio(config="sponsio.yaml", agent_id="{agent_id}")\n'
            f"# pass guard.wrap(tools) to your agent constructor:\n"
            f"#   agent = Agent(model=..., tools=guard.wrap(tools))"
        ),
        "openai_agents": (
            f"from sponsio.agents import Sponsio\n"
            f'guard = Sponsio(config="sponsio.yaml", agent_id="{agent_id}")\n'
            f"agent = guard.wrap(agent)"
        ),
        "openai": (
            f"from sponsio.openai import Sponsio, patch_openai\n"
            f'guard = Sponsio(config="sponsio.yaml", agent_id="{agent_id}")\n'
            f"patch_openai(client, guard)"
        ),
        "vercel_ai": (
            f"from sponsio.vercel_ai import Sponsio\n"
            f'guard = Sponsio(config="sponsio.yaml", agent_id="{agent_id}")\n'
            f"# add `sponsioMiddleware(guard)` to your generateText middleware"
        ),
        "mcp": (
            f"from sponsio.mcp import MCPContractProxy\n"
            f'proxy = MCPContractProxy(config="sponsio.yaml", '
            f'agent_id="{agent_id}")\n'
            f"# then run the proxy in front of your upstream MCP server:\n"
            f"#   await proxy.serve_stdio()  # or proxy.serve_sse(host=..., port=...)"
        ),
        "none": (
            f"import sponsio\n"
            f'guard = sponsio.Sponsio(config="sponsio.yaml", '
            f'agent_id="{agent_id}")\n'
            f"# before each tool call:\n"
            f"guard.guard_before(tool_name, args)\n"
            f"# after:\n"
            f"guard.guard_after(tool_name, result)"
        ),
    }
    return snippets.get(framework, snippets["none"])


def _refresh_stale_skills() -> list[str]:
    """Silently refresh any drifted SKILL.md copies.

    Walks every Sponsio Agent Skill install slot (``~/.cursor/skills/``,
    ``~/.claude/skills/``, ``~/.codex/skills/``).  For each one whose
    install is in ``drift`` state — i.e. the user previously ran
    ``sponsio skill install`` and the copy now lags behind the packaged
    source — re-copy the source on top of it.

    What this DOESN'T do:
        * Install into slots the user never opted into (status
          ``missing``) — that's a deliberate first-time choice, not a
          drift refresh.
        * Touch link-mode installs — those auto-upgrade with the
          package and never go stale.
        * Surface errors to the caller — onboard runs the doctor pass
          immediately after, which will re-flag the drift if anything
          went wrong here.

    Returns the list of tool labels that were refreshed (for the
    progress emit).  Empty when there's nothing to do, which is the
    common case.
    """
    try:
        from sponsio.cli import (
            _SKILL_TOOL_DIRS,
            _packaged_skill_source,
            _verify_skill_install_target,
        )
    except Exception:  # noqa: BLE001 — cli should always import
        return []

    try:
        src = _packaged_skill_source()
    except FileNotFoundError:
        # Packaged skill missing — doctor will raise this loudly; we
        # have nothing to refresh from.
        return []

    refreshed: list[str] = []
    import shutil

    for tool, parent in _SKILL_TOOL_DIRS.items():
        try:
            probe = _verify_skill_install_target(tool, parent, src)
        except Exception:  # noqa: BLE001
            continue
        if probe.status != "drift":
            continue
        target = parent / "sponsio"
        try:
            if target.is_symlink() or target.is_file():
                target.unlink()
            elif target.exists():
                shutil.rmtree(target)
            shutil.copytree(src, target)
        except OSError:
            continue
        refreshed.append(tool)
    return refreshed


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------


def run_onboard(
    target: Path,
    *,
    agent_id: str = "agent",
    mode: str = "observe",
    force: bool = False,
    scan_paths: list[Path] | None = None,
    probe_ollama: bool = True,
    run_doctor: bool = True,
    progress=None,
) -> OnboardReport:
    """Do the whole thing.

    Args:
        target: Directory in which to write ``sponsio.yaml``.  A
            ``.yaml`` path is accepted and treated as the output file.
        agent_id: Agent identifier stamped into the YAML.  Defaults to
            ``"agent"`` to match ``sponsio scan``'s default so users
            who later run ``sponsio scan --append`` don't end up with
            two siblings agent blocks.
        mode: ``"observe"`` (default, safe) or ``"enforce"``.
        force: Overwrite an existing ``sponsio.yaml``.
        scan_paths: Directories / files to scan for tool definitions.
            Defaults to ``[target]`` — typical when the user ran
            ``sponsio onboard .`` from their project root.
        probe_ollama: Whether to probe ``localhost:11434``.  Disable
            in tests that shouldn't hit the network.
        progress: Optional ``(str) -> None`` callback for banner lines
            (one per stage).  ``None`` silences them; ``print`` wires
            them to stdout.

    Returns:
        :class:`OnboardReport` with paths, counts, and the wrap
        snippet.  The caller (CLI) formats this for humans.
    """
    target = Path(target)
    if target.suffix in {".yaml", ".yml"}:
        out_path = target
        root = target.parent or Path(".")
    else:
        out_path = target / "sponsio.yaml"
        root = target

    if scan_paths is None:
        scan_paths = [root]

    def _emit(msg: str) -> None:
        if progress is not None:
            try:
                progress(msg)
            except Exception:  # noqa: BLE001
                pass

    if out_path.exists() and not force:
        raise FileExistsError(
            f"{out_path} already exists. Pass force=True (CLI: --force) to overwrite."
        )

    # --- Stage 1: provider detection ------------------------------------
    # Provider goes first because the analyzer it feeds is needed by
    # tool inventory, which we now run before framework detection so
    # the latter can prioritize files where tools were actually found.
    provider = detect_provider(probe_ollama=probe_ollama)

    # --- Stage 2: scan + starter-pack -----------------------------------
    # ``▸`` prefix is recognised by the CLI's progress sink as a
    # section header — printed bold-cyan without the ``· `` per-step
    # bullet.  Splits the long scan/LLM/pack stretch from the doctor
    # block visually so a 20s LLM wait doesn't look like one
    # undifferentiated stall.
    _emit("▸ Scanning your code")

    # Lazy import — CodeAnalyzer pulls in optional LLM SDKs on import
    # paths, and ``sponsio --help`` shouldn't pay for that.
    from sponsio.discovery.extractors.code_analysis import CodeAnalyzer
    from sponsio.discovery.starter_pack import starter_contracts

    # Configure the extractor based on the detected provider.  Ollama
    # speaks OpenAI-compatible, so we hand it off with provider="openai"
    # + base_url pointing at the local endpoint.
    analyzer_kwargs: dict = {}
    if provider.provider == "ollama":
        analyzer_kwargs.update(
            use_llm=True,
            provider="openai",
            base_url=provider.base_url,
            llm_model=provider.model,
        )
    elif provider.provider in {"openai", "anthropic", "gemini"}:
        analyzer_kwargs.update(
            use_llm=True,
            provider=provider.provider,
            llm_model=provider.model,
            base_url=provider.base_url,
        )
    else:
        analyzer_kwargs.update(use_llm=False)

    # Pipe our progress callback into the analyzer so its long-running
    # emits — "AST scan: N tools", "Running LLM inference (model=...)…",
    # "LLM inference done in 15.7s" — show up live during the wait.
    # Without this the user sees ~20 silent seconds during the LLM
    # call and reasonably wonders if the command froze.
    if progress is not None:
        analyzer_kwargs["progress"] = progress

    analyzer = CodeAnalyzer(**analyzer_kwargs)

    tool_inventory = analyzer.get_tool_inventory([str(p) for p in scan_paths])

    # --- Stage 3: framework detection (after tool inventory) ------------
    # The tool extractor walks the same project but with a more
    # aggressive skip set + no scan cap, so when both pipelines see
    # the same monorepo the tool extractor still finds the agent file
    # while a naive bounded import grep can miss it.  Feed the
    # extractor's hits in as priorities so the framework detector
    # can never starve on pad files.
    prioritize_files: list[Path] = []
    for t in tool_inventory or []:
        fp = t.get("filepath") if isinstance(t, dict) else None
        if not fp:
            continue
        p = Path(fp)
        if not p.is_absolute():
            p = root / p
        if p.is_file():
            prioritize_files.append(p)
    framework = detect_framework(root, prioritize_files=prioritize_files)

    scan_yaml = analyzer.generate_yaml(
        [str(p) for p in scan_paths],
        agent_id=agent_id,
        tool_inventory=tool_inventory,
    )

    tools_count = len(tool_inventory) if tool_inventory else 0
    warnings: list[str] = []

    # Count contracts in the scan YAML by counting top-level entries
    # inside the `contracts:` list.  Used both for the banner and to
    # decide whether to append starter-pack.
    scan_contract_count = _count_contracts(scan_yaml)

    starter_pack_used = False
    if provider.provider == "none" or scan_contract_count == 0:
        # Either the user has no LLM and the AST pass didn't find
        # enough, OR the LLM run produced nothing (rare but possible
        # on very small tool sets).  Either way the starter pack adds
        # safety net rules that require only the tool names.
        tool_names = [t["name"] for t in tool_inventory] if tool_inventory else []
        # Both globals (token_budget + delegation_depth_limit) default
        # to OFF — their hard-coded thresholds (100k tokens, depth 3)
        # are arbitrary and produced one ``# review`` line per onboard
        # that every user immediately removed.  Users who actually want
        # those caps add them by hand with values calibrated to their
        # workload.
        starter = starter_contracts(tool_names)
        # Dedup against contracts the scan already emitted.  We drop
        # starter rules — never the other way round — because the
        # scan side has full source context (param names, call graph,
        # docstrings) and its argument lists are strictly more
        # precise than the starter-pack's name-only convention.
        starter = _dedup_starter_proposals(starter, scan_yaml)
        if starter:
            starter_pack_used = True
            scan_yaml = _append_proposals_to_yaml(scan_yaml, starter, agent_id=agent_id)
            _emit(
                f"starter-pack: +{len(starter)} contract(s) "
                f"from name-heuristic safety rules"
            )

    if provider.provider == "none":
        warnings.append(
            "No LLM provider configured — set GOOGLE_API_KEY "
            "(1500 req/day free), ANTHROPIC_API_KEY, or OPENAI_API_KEY "
            "and re-run `sponsio onboard --force` for richer inference."
        )

    # --- Stage 3.5: pack auto-selection ---------------------------------
    pack_selection = select_packs(framework.framework, tool_inventory)
    if pack_selection.packs:
        _emit(
            f"packs: +{len(pack_selection.packs)} auto-selected "
            f"({', '.join(p.split(':', 1)[1] for p in pack_selection.packs)})"
        )
    pack_workspace = str(root.resolve()) if pack_selection.needs_workspace else None
    pack_lines = _emit_pack_block(pack_selection, pack_workspace)

    # --- Stage 4: compose + write ---------------------------------------
    final_yaml = _compose_yaml(
        provider=provider,
        mode=mode,
        agent_id=agent_id,
        scan_yaml=scan_yaml,
    )
    final_yaml = _splice_pack_block_into_agent(final_yaml, agent_id, pack_lines)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(final_yaml)
    _emit(f"wrote {out_path}")

    # --- Stage 5: wrap snippet ------------------------------------------
    # Auto-patching the user's agent file used to live behind
    # ``--apply`` but was removed (only langgraph / langchain were
    # supported and a coding agent / manual paste does the same job
    # for any framework with fewer surprises).  The CLI prints the
    # snippet for the user to apply.
    snippet = _wrap_snippet(framework.framework, agent_id)

    # --- Stage 5.5: refresh stale agent skills --------------------------
    # The `Agent Skill` doctor check warns when a previously-installed
    # SKILL.md copy lags behind the packaged source (the classic
    # ``pip install -U sponsio`` foot-gun: Python API moved forward,
    # the agent dispatcher still reads V(n-1)).  Onboard already
    # implies "freshen the local Sponsio install" so silently refresh
    # any drifted copies — a no-op if everything is already up to
    # date, no surprise installs into tools the user hadn't already
    # opted into.
    refreshed = _refresh_stale_skills()
    if refreshed:
        _emit(f"refreshed agent skill copies: {', '.join(refreshed)}")

    # --- Stage 6: doctor ------------------------------------------------
    doctor_results = None
    doctor_exit_code = None
    if run_doctor:
        # Lazy import — doctor pulls in the sto judge stack which has
        # its own optional deps; keep onboard --json --no-doctor
        # independent of those.
        # Announce the stage but NOT the result — the result is shown
        # in the CLI summary block immediately below, with per-check
        # details.  Emitting both here and there double-printed
        # ``doctor: 6/8 ok, 2 warn`` in the user-facing banner.
        _emit("▸ Health checks")
        _emit("running doctor checks…")
        try:
            from sponsio.doctor import run_doctor as _run_doctor

            doctor_results, doctor_exit_code = _run_doctor(root, with_llm=False)
        except Exception as e:  # noqa: BLE001
            warnings.append(f"sponsio doctor failed to run: {e}")

    # Authoritative count: the number of contracts the user's agent
    # will actually have at runtime.  Includes pack-pulled contracts
    # via ``include:``, which the inline-scanning ``_count_contracts``
    # heuristic misses by design.  We fall back to the heuristic only
    # when the load fails (which would itself surface as a warning
    # in the doctor stage above) so the report still has a sensible
    # number to print.
    try:
        from sponsio.config import load_config as _load_for_count

        _loaded = _load_for_count(out_path)
        final_contract_count = len(_loaded.agents[agent_id].contracts)
    except Exception as e:  # noqa: BLE001
        warnings.append(f"could not load emitted yaml to count contracts: {e}")
        final_contract_count = _count_contracts(final_yaml)
    return OnboardReport(
        out_path=out_path,
        agent_id=agent_id,
        framework=framework,
        provider=provider,
        mode=mode,
        tools_count=tools_count,
        contracts_count=final_contract_count,
        starter_pack_used=starter_pack_used,
        wrap_snippet=snippet,
        warnings=warnings,
        doctor_results=doctor_results,
        doctor_exit_code=doctor_exit_code,
        pack_selection=pack_selection,
    )


def _count_contracts(yaml_text: str) -> int:
    """Count top-level contract entries in a generated YAML body.

    Matches ``- E:`` or ``- A:`` at indent 6 — the shape that both
    :class:`CodeAnalyzer.generate_yaml` and :func:`_append_proposals_to_yaml`
    emit.  Comment-only entries and blank lines don't count.
    """
    n = 0
    for line in yaml_text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("- E:") or stripped.startswith("- A:"):
            n += 1
    return n


# Pattern aliases: two pattern names that compile to semantically
# identical LTL formulas.  Used for the starter-pack dedup: an AST-
# emitted ``idempotent(delete_user)`` and a starter-pack
# ``irreversible_once(delete_user)`` are the same contract
# (``G(count(delete_user) <= 1)``) and shipping both wastes review
# time + runtime evaluation.  Map every alias onto its canonical name
# so the dedup key set treats them as one.
_PATTERN_ALIASES: dict[str, str] = {
    "idempotent": "irreversible_once",
}


def _canonical_pattern(name: str) -> str:
    return _PATTERN_ALIASES.get(name, name)


# Match a contract entry's pattern name + first argument as written by
# ``CodeAnalyzer.generate_yaml`` (or our own ``_append_proposals_to_yaml``).
# Anchored on the indentation of a structured entry so freeform NL
# entries (``- E: "..."``) don't accidentally produce keys.
_CONTRACT_KEY_RE = re.compile(
    r"""
    ^[ ]{10}pattern:[ ]+(?P<pattern>[A-Za-z_][\w]*)[ \t]*\n
    (?:^[ ]{10}args:[ ]+\[\s*(?P<first_arg>[^,\]\s][^,\]]*?)\s*[,\]])?
    """,
    re.MULTILINE | re.VERBOSE,
)


def _existing_contract_keys(yaml_text: str) -> set[tuple[str, str | None]]:
    """Extract ``(canonical_pattern, first_arg)`` keys from a scan YAML.

    The first arg of every starter-pack pattern is either the bound
    tool name (``irreversible_once``, ``arg_blacklist``, ``rate_limit``,
    ``loop_detection``) or a sentinel for global rules (``token_budget``,
    ``delegation_depth_limit``, ``tool_allowlist``).  Matching on
    ``(pattern, first_arg)`` is therefore precise enough to dedup
    near-duplicates that ``_dedup_key`` (full LTL string) misses
    because the patterns lists differ slightly between AST and
    starter-pack.
    """
    keys: set[tuple[str, str | None]] = set()
    for m in _CONTRACT_KEY_RE.finditer(yaml_text):
        pat = _canonical_pattern(m.group("pattern"))
        arg = (m.group("first_arg") or "").strip().strip('"').strip("'")
        # Collapse to the global-rule sentinel (``None``) when the
        # first arg isn't a tool name:
        #   * Lists / nested args (``args: [[a, b]]`` → captures ``[a``)
        #     come from ``tool_allowlist``-style globals.
        #   * Numeric leading args (``args: [50000, total]``) come
        #     from ``token_budget`` / ``delegation_depth_limit``
        #     where the per-tool dimension doesn't exist.
        # The proposal-side normalizer in ``_proposal_dedup_key`` does
        # the same collapse, so the two key sets line up.
        if arg.startswith("["):
            arg = ""
        else:
            try:
                float(arg)
                arg = ""
            except ValueError:
                pass
        keys.add((pat, arg or None))
    return keys


def _proposal_dedup_key(p) -> tuple[str, str | None]:
    """Mirror :func:`_existing_contract_keys` for a ``ProposedConstraint``."""
    pat = _canonical_pattern(getattr(p.formula, "pattern_name", "") or "")
    args = []
    if isinstance(p.evidence, dict):
        args = p.evidence.get("args") or []
    first = args[0] if args else None
    if isinstance(first, list):
        # Global rules whose only positional arg is a list (e.g.
        # ``tool_allowlist([...])``) collapse onto pattern-name-only
        # keys — same in :func:`_existing_contract_keys`.
        first = None
    if first is not None and not isinstance(first, str):
        # Numeric leading args (e.g. ``token_budget(100000, "total")``)
        # also collapse — there's no per-tool dimension to dedup on.
        first = None
    return (pat, first)


def _dedup_starter_proposals(
    starter: list,
    scan_yaml: str,
) -> list:
    """Drop starter-pack proposals already covered by the scan YAML.

    Always trusts the scan side: AST + LLM pass produced the entry
    with full source context, so its argument list is more precise
    than the starter-pack's name-only convention.  Starter-pack rules
    are removed when ``(canonical_pattern, primary_tool)`` matches
    anything already emitted — see ``_PATTERN_ALIASES`` for the
    semantic-equivalence table.
    """
    existing = _existing_contract_keys(scan_yaml)
    if not existing:
        return starter
    return [p for p in starter if _proposal_dedup_key(p) not in existing]


def _append_proposals_to_yaml(
    scan_yaml: str,
    proposals: list,
    *,
    agent_id: str,
) -> str:
    """Append a list of :class:`ProposedConstraint` to a scan YAML.

    Uses the same emission shape as
    :meth:`CodeAnalyzer.generate_yaml` (``- E: pattern: ... args:
    [...]  source: scan``) so the appended entries are
    indistinguishable from regularly-scanned ones — the user reviewing
    ``sponsio.yaml`` just sees contracts with a ``source: scan``
    marker, not two parallel formats.

    Handles the ``contracts: []`` sentinel case: when the scan emitted
    an empty list (no AST contracts AND no LLM), we replace the
    sentinel with a proper ``contracts:`` header before appending.
    """
    from sponsio.discovery.extractors.code_analysis import CodeAnalyzer

    lines = scan_yaml.split("\n")

    # Find the contracts: line for our agent.  Agent id already renamed
    # upstream by _compose_yaml, so the expected shape is:
    #   agents:
    #     <agent_id>:
    #       contracts: [] | contracts:
    agent_header = f"  {agent_id}:"
    contracts_idx = -1
    in_agent = False
    for i, ln in enumerate(lines):
        if ln.rstrip() == agent_header:
            in_agent = True
            continue
        if in_agent and ln.lstrip().startswith("contracts:"):
            contracts_idx = i
            break
        # Moved to the next top-level block — not the agent we wanted.
        if in_agent and ln and not ln.startswith(" "):
            break

    if contracts_idx == -1:
        # Scan didn't produce an agent block for our id (extremely
        # rare — generate_yaml always emits one).  Append a full block
        # as a best-effort fallback.
        lines.extend(
            [
                "agents:",
                f"  {agent_id}:",
                "    contracts:",
            ]
        )
        contracts_idx = len(lines) - 1

    # If the line is `contracts: []`, replace with a proper list header
    # so entries can follow.
    if lines[contracts_idx].rstrip().endswith("contracts: []"):
        indent = lines[contracts_idx][
            : len(lines[contracts_idx]) - len(lines[contracts_idx].lstrip())
        ]
        lines[contracts_idx] = f"{indent}contracts:"

    # Emit each proposal in the scan format.  We duplicate the tiny
    # amount of YAML-emission logic here rather than re-invoke
    # ``generate_yaml`` (which re-emits the entire file) because the
    # merge target is specifically "the end of this agent's contracts
    # list".
    appended: list[str] = []
    for p in sorted(proposals, key=lambda x: -x.confidence):
        if p.formula is None:
            continue
        pattern = getattr(p.formula, "pattern_name", "")
        if not pattern:
            continue
        conf = p.confidence
        conf_tag = f"  # confidence: {conf:.2f}" if conf < 0.9 else ""
        appended.append(f"      - E:{conf_tag}")
        appended.append(f"          pattern: {pattern}")
        args = p.evidence.get("args") if isinstance(p.evidence, dict) else None
        if args:
            appended.append(f"          args: {CodeAnalyzer._emit_yaml_list(args)}")
        appended.append("          source: scan")

    # Find the end of this agent's block (next top-level key or EOF).
    end_idx = len(lines)
    for j in range(contracts_idx + 1, len(lines)):
        ln = lines[j]
        # Anything dedented to column 0 (new top-level) ends our block.
        if ln and not ln.startswith(" ") and not ln.startswith("#"):
            end_idx = j
            break

    lines = lines[:end_idx] + appended + lines[end_idx:]
    return "\n".join(lines)
