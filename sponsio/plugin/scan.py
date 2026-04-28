"""Plugin-manifest scanner for ``sponsio plugin scan``.

Reads a Claude Code plugin directory (``<dir>/.claude-plugin/plugin.json``,
``<dir>/.mcp.json``, ``<dir>/skills/``) and produces a starter
contract library for the user to drop into
``~/.sponsio/plugins/<plugin-id>/sponsio.yaml``.

Today this is **manifest parsing + name-heuristic rule generation
only** — no live MCP server introspection. Tool names come from the
``--tools`` CLI flag (caller knows them) or from future MCP
``tools/list`` introspection. Without one of those, the scanner can
only produce a baseline library that includes ``sponsio:core/runaway``
to catch token / loop / delegation runaways for the whole plugin.

Mode-A scope: this module *only* writes YAML libraries. It does not
ask MCP servers anything, doesn't talk to the registry, doesn't
auto-apply. The CLI thin-wraps it in ``plugin scan`` and decides
whether to print or write based on ``--apply``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from sponsio.discovery._types import ProposedConstraint
from sponsio.discovery.starter_pack import _per_tool_rules


# ---------------------------------------------------------------------------
# Manifest parsing
# ---------------------------------------------------------------------------


@dataclass
class PluginManifest:
    """What we extracted from ``<dir>/.claude-plugin/plugin.json`` and friends."""

    plugin_id: str
    """Slug used as the contract-library directory name."""
    name: str = ""
    version: str = ""
    description: str = ""
    mcp_servers: list[str] = field(default_factory=list)
    """Names of MCP servers the plugin declares in ``.mcp.json``.
    Just the keys — we don't introspect their tool inventory yet."""
    skill_names: list[str] = field(default_factory=list)
    """Slash-command names from ``skills/<name>/SKILL.md``."""


class ManifestError(ValueError):
    """Raised when a plugin directory doesn't look like a Claude Code plugin."""


def parse_plugin_manifest(plugin_dir: Path) -> PluginManifest:
    """Parse the Claude Code plugin layout in ``plugin_dir``.

    Required: ``.claude-plugin/plugin.json`` with at minimum a ``name``.
    Optional: ``.mcp.json``, ``skills/<name>/SKILL.md``.

    Raises :class:`ManifestError` if the layout doesn't match.
    """
    plugin_dir = Path(plugin_dir).expanduser().resolve()
    if not plugin_dir.is_dir():
        raise ManifestError(f"{plugin_dir} is not a directory")

    manifest_path = plugin_dir / ".claude-plugin" / "plugin.json"
    if not manifest_path.exists():
        raise ManifestError(
            f"No .claude-plugin/plugin.json under {plugin_dir} — "
            f"this doesn't look like a Claude Code plugin."
        )

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ManifestError(f"Invalid plugin.json: {e}") from e

    plugin_id = manifest.get("name")
    if not plugin_id or not isinstance(plugin_id, str):
        raise ManifestError(
            f"plugin.json must declare a non-empty `name` field (got {plugin_id!r})"
        )

    mcp_servers: list[str] = []
    mcp_path = plugin_dir / ".mcp.json"
    if mcp_path.exists():
        try:
            mcp_data = json.loads(mcp_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # Don't fail the whole scan because .mcp.json is malformed —
            # operator probably wants to see what the rest looks like.
            mcp_data = {}
        servers = mcp_data.get("mcpServers") or mcp_data.get("servers") or {}
        if isinstance(servers, dict):
            mcp_servers = list(servers.keys())

    skill_names: list[str] = []
    skills_dir = plugin_dir / "skills"
    if skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            if child.is_dir() and (child / "SKILL.md").exists():
                skill_names.append(child.name)

    return PluginManifest(
        plugin_id=plugin_id,
        name=plugin_id,
        version=str(manifest.get("version", "")),
        description=str(manifest.get("description", "")),
        mcp_servers=mcp_servers,
        skill_names=skill_names,
    )


# ---------------------------------------------------------------------------
# Library generation
# ---------------------------------------------------------------------------


@dataclass
class LibraryGroup:
    """A single per-plugin-id library to be written under ``<root>/<plugin_id>/``."""

    plugin_id: str
    """Routing key — what ``guard_stdin.derive_plugin_id`` would return for
    every tool grouped here."""
    tools: list[str]
    """Tools whose names route to this plugin_id."""
    proposed: list[ProposedConstraint]
    """One ``ProposedConstraint`` per heuristic rule."""
    library_yaml: str
    """Rendered yaml ready to drop into ``<root>/<plugin_id>/sponsio.yaml``."""


@dataclass
class ScanResult:
    """End-to-end scan output: manifest + groups partitioned by routed id."""

    manifest: PluginManifest
    declared_tools: list[str]
    groups: list[LibraryGroup]
    """One group per distinct routed plugin_id; ``--apply`` writes one
    file per group."""


def synthesize_manifest(plugin_id: str, *, name: str = "", description: str = "") -> PluginManifest:
    """Build a minimal :class:`PluginManifest` for plugin-less targets.

    Used when scanning a bare MCP server (no Claude-Code wrapping
    plugin directory) — the operator passes ``--introspect`` and a
    ``plugin-id`` and we synthesize the rest.  Mirrors what
    :func:`parse_plugin_manifest` would have read off ``plugin.json``.
    """
    return PluginManifest(
        plugin_id=plugin_id,
        name=name or plugin_id,
        version="",
        description=description,
        mcp_servers=[],
        skill_names=[],
    )


def scan_plugin(
    plugin_dir: Path | None,
    declared_tools: list[str] | None = None,
    *,
    include_runaway: bool = True,
    manifest: PluginManifest | None = None,
) -> ScanResult:
    """End-to-end scan: manifest → tools partitioned by routed plugin_id
    → one rendered yaml per group.

    Why partitioning matters: the runtime hook in
    :mod:`sponsio.guard_stdin` derives ``plugin_id`` from each
    incoming ``tool_name`` (``mcp__github__X`` → ``github``;
    ``Bash`` → ``_host``; ``acme:fetch`` → ``acme``) and only loads
    ``<root>/<plugin_id>/sponsio.yaml``. A Claude Code plugin can
    bundle multiple MCP servers, each surfacing its own tool
    namespace; the scan output therefore has to be partitioned the
    same way the runtime routes, otherwise rules for
    ``mcp__github__*`` written under ``<root>/sponsio-claude-code/``
    are never loaded.

    Args:
        plugin_dir: Path to the Claude Code plugin root (the directory
            containing ``.claude-plugin/``).
        declared_tools: Tool names to apply heuristics to. The scanner
            cannot infer these from the manifest alone — pass them in
            from the CLI ``--tools`` flag, or via a future MCP
            ``tools/list`` introspection. If empty, only the baseline
            ``_host`` / plugin-id group is emitted.
        include_runaway: Whether to include ``sponsio:core/runaway``
            in every group's ``include:`` list. Default on — every
            plugin benefits from token / loop / delegation caps.
    """
    # Lazy import to avoid a runtime import cycle at module load time.
    from sponsio.guard_stdin import derive_plugin_id

    if manifest is None:
        if plugin_dir is None:
            raise ManifestError(
                "scan_plugin: pass either a plugin_dir or an explicit "
                "synthesised manifest (use synthesize_manifest)."
            )
        manifest = parse_plugin_manifest(plugin_dir)
    tools = list(declared_tools or [])

    # Partition tools by routed plugin_id. ``manifest.plugin_id`` is
    # the fallback key for groups with no tools — captures the
    # "baseline-only" case (every plugin gets at least one yaml so
    # ``plugin init``-style discovery still works).
    by_id: dict[str, list[str]] = {}
    for t in tools:
        pid = derive_plugin_id(t)
        by_id.setdefault(pid, []).append(t)
    if not by_id:
        by_id[manifest.plugin_id] = []

    groups: list[LibraryGroup] = []
    for plugin_id in sorted(by_id):
        group_tools = by_id[plugin_id]
        proposed: list[ProposedConstraint] = []
        for t in group_tools:
            proposed.extend(_per_tool_rules(t))
        yaml_text = _render_library_yaml(
            manifest=manifest,
            agent_id=plugin_id,
            tools=group_tools,
            proposed=proposed,
            include_runaway=include_runaway,
        )
        groups.append(
            LibraryGroup(
                plugin_id=plugin_id,
                tools=group_tools,
                proposed=proposed,
                library_yaml=yaml_text,
            )
        )

    return ScanResult(manifest=manifest, declared_tools=tools, groups=groups)


def _render_library_yaml(
    *,
    manifest: PluginManifest,
    agent_id: str,
    tools: list[str],
    proposed: list[ProposedConstraint],
    include_runaway: bool,
) -> str:
    """Render one ``LibraryGroup`` to yaml.

    Output shape matches what ``plugin init`` already writes for
    ``_host`` — top-level ``agents:<agent_id>`` with optional
    ``include:`` and a list of ``contracts:``. Each contract gets a
    ``source: plugin-scan`` tag so future ``sponsio refresh`` runs
    can distinguish heuristic contracts from user-written ones.
    """
    contracts: list[dict] = []
    for p in proposed:
        det = p.formula
        if det is None:
            continue
        if not det.args or not det.pattern_name:
            continue
        contracts.append(
            {
                "desc": p.nl_description or det.desc,
                "E": {
                    "pattern": det.pattern_name,
                    "args": _normalise_args(det.args),
                    "source": "plugin-scan",
                },
            }
        )

    agent_block: dict = {}
    if include_runaway:
        agent_block["include"] = ["sponsio:core/runaway"]
    if contracts:
        agent_block["contracts"] = contracts
    elif "include" not in agent_block:
        agent_block["contracts"] = []

    doc = {"version": "1", "agents": {agent_id: agent_block}}
    header = _yaml_header(manifest=manifest, agent_id=agent_id, tools=tools)
    body = yaml.safe_dump(doc, sort_keys=False)
    return header + body


def _yaml_header(*, manifest: PluginManifest, agent_id: str, tools: list[str]) -> str:
    lines = [
        f"# Generated by `sponsio plugin scan` for plugin "
        f"'{manifest.plugin_id}' (agent: {agent_id}).",
        "#",
        f"# Drop into ~/.sponsio/plugins/{agent_id}/sponsio.yaml",
        "# (or wherever $SPONSIO_PLUGIN_ROOT points).",
        "#",
        "# Every contract here was proposed by name-heuristic — review",
        "# and tighten before relying on it.",
    ]
    if tools:
        lines.append("#")
        lines.append("# Tools covered: " + ", ".join(tools))
    if manifest.version:
        lines.append("#")
        lines.append(f"# Source plugin version: {manifest.version}")
    if manifest.mcp_servers:
        lines.append("#")
        lines.append(
            "# MCP servers in source plugin: " + ", ".join(manifest.mcp_servers)
        )
    lines.append(
        "# ====================================================================="
    )
    lines.append("")
    return "\n".join(lines) + "\n"


def _normalise_args(args: tuple) -> list:
    """Convert pattern args (mix of tuples / lists) into a yaml-friendly list."""

    def _conv(x):
        if isinstance(x, tuple):
            return [_conv(item) for item in x]
        if isinstance(x, list):
            return [_conv(item) for item in x]
        return x

    return [_conv(a) for a in args]
