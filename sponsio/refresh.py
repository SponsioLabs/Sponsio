"""``sponsio refresh`` — re-mine contracts from recent traces and
surgically merge into an existing ``sponsio.yaml``.

Design goals (see chat transcript for the design discussion):

* **Preserve user tuning**:  ``overrides:``, ``include:``, ``runtime:``,
  ``judge:``, ``workspace:``, ``tool_rename:``, and every contract
  without a ``source: trace`` tag are left untouched.
* **Only touch what we own**: MVP updates exclusively ``source: trace``
  contracts.  ``source: scan`` (from code) and ``source: policy`` are
  treated as immutable, since a trace-only refresh has no signal about
  whether they should stay or go.
* **Dry-run by default**: nothing is written until ``--apply``.  Even
  with ``--apply`` we backup to ``.sponsio.bak`` first.
* **Two modes**:

  * ``add-only`` — add new contracts, never remove or drift.  Safe for
    small trace windows.
  * ``replace-trace`` (default with ``--apply``) — recent traces are
    authoritative for the ``source: trace`` subset.  Entries that no
    longer show up in the fresh mining run are dropped.

Identity (for dedup + drift detection) is
``(pattern_name, tuple_of_non_numeric_args)``.  A numeric threshold
drift (e.g. ``rate_limit(send_email, 5)`` → ``(send_email, 12)``) is
surfaced as a "drifted" bucket rather than add+remove, because the
user usually wants to see it as a single "threshold moved" line.

Comments and blank-line structure are NOT preserved through
``--apply`` because PyYAML's safe_dump doesn't round-trip them.  The
backup file exists precisely so users can retrieve any prose
annotations they'd inlined.  We warn about this on stderr.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "RefreshReport",
    "compute_refresh",
    "render_report",
    "apply_refresh",
    "DEFAULT_SESSION_GLOB",
]


# Trace-source tag values that refresh considers "owned".  Keep this
# narrow: the MVP signal (trace mining) can only speak authoritatively
# about ``trace``-sourced contracts.
_REFRESHABLE_SOURCES = frozenset({"trace"})

# User-facing glob for the default session log location.  The ``{agent}``
# token is substituted by the CLI wrapper based on ``--agent``.
DEFAULT_SESSION_GLOB = "~/.sponsio/sessions/{agent}/*.jsonl"


# ---------------------------------------------------------------------------
# Identity / args normalization
# ---------------------------------------------------------------------------


def _is_numeric(v: Any) -> bool:
    """Return True for ``int`` / ``float`` but NOT bool (since bool is
    an int subclass and we want rules keyed on booleans to keep the
    boolean in identity)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _normalize_arg(a: Any) -> Any:
    """Canonical form of a single arg for identity/dedup purposes.

    Lists are recursed; everything else is stringified (so that the
    YAML's ``[a, b, c]`` and the in-memory ``["a","b","c"]`` collapse
    together)."""
    if isinstance(a, list):
        return tuple(_normalize_arg(x) for x in a)
    if isinstance(a, bool):
        return a
    if _is_numeric(a):
        return a  # kept in `value_key` only; stripped from `identity_key`
    return str(a)


def identity_key(
    pattern: str | None,
    args: list | tuple | None,
    nl: str | None,
) -> tuple:
    """Stable dedup key — used to decide whether two contracts refer to
    the same rule.

    For structured contracts: ``(pattern, *non_numeric_args)``.  Numeric
    args are stripped so threshold drift shows up as "drift", not
    "add+remove".

    For pure-NL contracts (no ``pattern:``): ``("__nl__", normalized_nl)``
    — we can't do semantic dedup without a parser round-trip, so use
    the string itself.  Case-folded + whitespace-collapsed so tiny
    edits don't spuriously double-count.
    """
    if pattern:
        if args is None:
            args = []
        non_num = tuple(_normalize_arg(a) for a in args if not _is_numeric(a))
        return (str(pattern), non_num)
    if nl:
        collapsed = re.sub(r"\s+", " ", nl.strip().lower())
        return ("__nl__", collapsed)
    return ("__unknown__",)


def value_key(args: list | tuple | None) -> tuple:
    """Full-args tuple — used for drift detection.  Two contracts with
    the same ``identity_key`` but different ``value_key`` are drifted,
    not duplicates."""
    if args is None:
        return ()
    return tuple(_normalize_arg(a) for a in args)


# ---------------------------------------------------------------------------
# Contract shape normalization
# ---------------------------------------------------------------------------


@dataclass
class _NormalizedContract:
    """YAML-dict form flattened enough for diffing.

    We keep the ORIGINAL dict around as ``raw`` so that ``apply_refresh``
    can round-trip exactly what the user wrote — we only touch the
    ``identity_key``-matching entries."""

    raw: dict[str, Any]
    source: str | None
    pattern: str | None
    args: list | None
    assumption: str | None  # raw A: text (str form only, for MVP)
    nl: str | None  # the E: text if it's a string, for NL-only entries

    def identity(self) -> tuple:
        # For contracts with an A:, include its text in identity so
        # ``must_precede(X, Y)`` conditional-on-A is distinct from the
        # unconditional version.
        base = identity_key(self.pattern, self.args, self.nl)
        if self.assumption:
            a = re.sub(r"\s+", " ", self.assumption.strip().lower())
            return base + ("A:" + a,)
        return base

    def values(self) -> tuple:
        return value_key(self.args)


def _text_of(field_value: Any) -> str | None:
    """Flatten an A: / E: field to a single string when possible.

    The schema accepts either a scalar NL string OR a structured dict
    ``{pattern, args, source}``.  This helper returns the string form
    (or ``None`` when it's a structured dict)."""
    if isinstance(field_value, str):
        return field_value
    if isinstance(field_value, list):
        # AND of strings — join with " and " so identity still sees them.
        parts = [str(x) for x in field_value if isinstance(x, str)]
        return " and ".join(parts) if parts else None
    return None


def _normalize_contract_entry(entry: Any) -> _NormalizedContract | None:
    """Collapse an ``agents.<id>.contracts[*]`` entry into the shape we
    need for diffing.  Returns ``None`` for entries we don't understand
    (very malformed) — they'll be passed through untouched on apply."""
    if not isinstance(entry, dict):
        return None

    # Extract A / assumption (accept both long and short keys).
    a_raw = entry.get("A", entry.get("assumption"))
    assumption = _text_of(a_raw)

    # Extract E / enforcement.
    e_raw = entry.get("E", entry.get("enforcement"))
    pattern = None
    args: list | None = None
    source: str | None = None
    nl: str | None = None
    if isinstance(e_raw, dict):
        pattern = e_raw.get("pattern")
        a = e_raw.get("args")
        args = list(a) if isinstance(a, (list, tuple)) else None
        source = e_raw.get("source")
    elif isinstance(e_raw, str):
        nl = e_raw
        source = entry.get("source")  # sometimes attached at entry level
    elif isinstance(e_raw, list):
        nl = _text_of(e_raw)
        source = entry.get("source")
    else:
        return None

    return _NormalizedContract(
        raw=entry,
        source=source,
        pattern=pattern,
        args=args,
        assumption=assumption,
        nl=nl,
    )


# ---------------------------------------------------------------------------
# Diff structure
# ---------------------------------------------------------------------------


@dataclass
class RefreshReport:
    """Per-agent diff summary produced by ``compute_refresh``.

    All lists hold ``_NormalizedContract`` instances (with ``.raw``
    pointing at the original dict).  The "Drifted" bucket carries both
    sides as a pair so the renderer can show old→new.
    """

    agent: str
    added: list[_NormalizedContract] = field(default_factory=list)
    drifted: list[tuple[_NormalizedContract, _NormalizedContract]] = field(
        default_factory=list
    )
    stale: list[_NormalizedContract] = field(default_factory=list)
    unchanged_refreshable: list[_NormalizedContract] = field(default_factory=list)
    untouched_immutable: list[_NormalizedContract] = field(default_factory=list)

    # Raw counts for programmatic access (e.g. tests).
    @property
    def net_change(self) -> int:
        return len(self.added) - len(self.stale)

    @property
    def is_noop(self) -> bool:
        return not self.added and not self.drifted and not self.stale


# ---------------------------------------------------------------------------
# Core diff
# ---------------------------------------------------------------------------


def compute_refresh(
    existing_contracts: list[Any],
    fresh_contracts: list[Any],
    agent: str,
) -> RefreshReport:
    """Return a diff between the ``source: trace`` subset of
    ``existing_contracts`` and the newly-mined ``fresh_contracts``.

    Entries whose source is NOT in ``_REFRESHABLE_SOURCES`` are bucketed
    as ``untouched_immutable`` — they flow through any ``apply_refresh``
    call unchanged.  This preserves user-written contracts, ``source:
    scan`` (from code), ``source: policy``, and anything the user
    hand-edited without a source tag.
    """
    report = RefreshReport(agent=agent)

    refreshable_existing: list[_NormalizedContract] = []
    for e in existing_contracts:
        nc = _normalize_contract_entry(e)
        if nc is None:
            # Keep unknowns as-is — we can't diff them but we must
            # not drop them.  Stash as immutable (use a placeholder
            # with raw=e so the writer can round-trip).
            report.untouched_immutable.append(
                _NormalizedContract(
                    raw=e if isinstance(e, dict) else {"_raw": e},
                    source=None,
                    pattern=None,
                    args=None,
                    assumption=None,
                    nl=None,
                )
            )
            continue
        if nc.source in _REFRESHABLE_SOURCES:
            refreshable_existing.append(nc)
        else:
            report.untouched_immutable.append(nc)

    fresh_normalized: list[_NormalizedContract] = []
    for e in fresh_contracts:
        nc = _normalize_contract_entry(e)
        if nc is None:
            continue
        fresh_normalized.append(nc)

    ex_idx: dict[tuple, _NormalizedContract] = {}
    for nc in refreshable_existing:
        ex_idx.setdefault(nc.identity(), nc)  # first wins on accidental dup
    new_idx: dict[tuple, _NormalizedContract] = {}
    for nc in fresh_normalized:
        new_idx.setdefault(nc.identity(), nc)

    ex_keys = set(ex_idx)
    new_keys = set(new_idx)

    for k in sorted(new_keys - ex_keys, key=lambda t: str(t)):
        report.added.append(new_idx[k])
    for k in sorted(ex_keys - new_keys, key=lambda t: str(t)):
        report.stale.append(ex_idx[k])
    for k in sorted(ex_keys & new_keys, key=lambda t: str(t)):
        old = ex_idx[k]
        new = new_idx[k]
        if old.values() != new.values():
            report.drifted.append((old, new))
        else:
            report.unchanged_refreshable.append(old)

    return report


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _fmt_contract(nc: _NormalizedContract) -> str:
    """Short, stable one-line rendering for the diff output."""
    if nc.pattern:
        args_str = ""
        if nc.args:
            parts = [a if isinstance(a, str) else repr(a) for a in (nc.args or [])]
            args_str = "(" + ", ".join(parts) + ")"
        prefix = f"A:{nc.assumption!r} ⇒ " if nc.assumption else ""
        return f"{prefix}{nc.pattern}{args_str}"
    if nc.nl:
        preview = (nc.nl[:80] + "…") if len(nc.nl) > 80 else nc.nl
        prefix = f"A:{nc.assumption!r} ⇒ " if nc.assumption else ""
        return f"{prefix}NL: {preview}"
    return "<unparseable entry>"


def render_report(reports: list[RefreshReport], *, color: bool = True) -> str:
    """Turn a list of per-agent reports into the stderr-ready diff
    summary.  Passing ``color=False`` yields a plain string suitable
    for tests and non-TTY environments."""

    def _c(tag: str, text: str) -> str:
        if not color:
            return text
        codes = {
            "+": "\033[32m",
            "-": "\033[33m",
            "~": "\033[36m",
            "=": "\033[90m",
            "!": "\033[31m",
            "reset": "\033[0m",
        }
        return f"{codes.get(tag, '')}{text}{codes['reset']}"

    lines: list[str] = []
    grand = {"added": 0, "drifted": 0, "stale": 0, "unchanged": 0, "immutable": 0}
    for r in reports:
        grand["added"] += len(r.added)
        grand["drifted"] += len(r.drifted)
        grand["stale"] += len(r.stale)
        grand["unchanged"] += len(r.unchanged_refreshable)
        grand["immutable"] += len(r.untouched_immutable)

        lines.append(f"Agent: {r.agent}")
        if r.added:
            for nc in r.added:
                lines.append(_c("+", f"  + new       {_fmt_contract(nc)}"))
        if r.drifted:
            for old, new in r.drifted:
                lines.append(
                    _c(
                        "~",
                        f"  ~ drifted   {_fmt_contract(old)}  "
                        f"→ args {list(new.values())}",
                    )
                )
        if r.stale:
            for nc in r.stale:
                lines.append(
                    _c("-", f"  - stale     {_fmt_contract(nc)}  (not re-observed)")
                )
        if r.unchanged_refreshable:
            lines.append(
                _c(
                    "=",
                    f"  = {len(r.unchanged_refreshable)} unchanged "
                    f"(source: trace, re-observed)",
                )
            )
        if r.untouched_immutable:
            lines.append(
                _c(
                    "=",
                    f"  = {len(r.untouched_immutable)} preserved "
                    f"(user / scan / policy / overrides — not touched)",
                )
            )
        lines.append("")

    lines.append(
        f"Total: +{grand['added']}  ~{grand['drifted']}  -{grand['stale']}  "
        f"={grand['unchanged']} unchanged  ={grand['immutable']} preserved"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def apply_refresh(
    config: dict[str, Any],
    reports: dict[str, RefreshReport],
    fresh_agent_contracts: dict[str, list[Any]],
    *,
    mode: str = "replace-trace",
) -> dict[str, Any]:
    """Return a NEW top-level config dict with each agent's
    ``contracts:`` list rewritten per ``mode``.  Does NOT mutate the
    input.

    * ``add-only``: existing contracts are kept verbatim; only
      genuinely-new ``source: trace`` entries are appended.
    * ``replace-trace``: every existing ``source: trace`` entry is
      dropped, then the full set of freshly-mined contracts is appended
      (so drift / re-observed / genuinely-new all land from the fresh
      side).  Non-refreshable entries (user, scan, policy, overrides)
      pass through untouched.
    """
    if mode not in ("add-only", "replace-trace"):
        raise ValueError(f"mode must be 'add-only' or 'replace-trace', got {mode!r}")

    out = dict(config)
    agents = dict(out.get("agents") or {})

    for agent_id, report in reports.items():
        a_cfg = dict(agents.get(agent_id) or {})
        existing: list = list(a_cfg.get("contracts") or [])
        fresh: list = list(fresh_agent_contracts.get(agent_id) or [])

        if mode == "add-only":
            # Keep everything existing; append only contracts whose
            # identity wasn't seen in the existing refreshable set.
            added = [nc.raw for nc in report.added]
            a_cfg["contracts"] = existing + added
        else:
            # replace-trace: strip source:trace entries from existing,
            # keep the rest in their original order, then append the
            # full fresh set at the bottom.
            kept: list = []
            for e in existing:
                nc = _normalize_contract_entry(e)
                if nc is not None and nc.source in _REFRESHABLE_SOURCES:
                    continue
                kept.append(e)
            a_cfg["contracts"] = kept + fresh

        agents[agent_id] = a_cfg

    out["agents"] = agents
    return out


def backup_then_write(
    target: Path,
    new_yaml_text: str,
    *,
    backup_suffix: str = ".sponsio.bak",
) -> Path | None:
    """Copy ``target`` → ``target.with_suffix(backup_suffix)``, then
    write ``new_yaml_text`` to ``target``.  Returns the backup path
    (or ``None`` if ``target`` didn't exist yet)."""
    backup: Path | None = None
    if target.exists():
        backup = target.with_name(target.name + backup_suffix)
        shutil.copy2(target, backup)
    target.write_text(new_yaml_text)
    return backup
