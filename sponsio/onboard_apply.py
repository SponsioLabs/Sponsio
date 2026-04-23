"""Auto-patch helper for ``sponsio onboard --apply``.

Inserts the framework-specific Sponsio wrap into the user's agent
entry file with these guarantees:

* **Backup first.**  The original file is copied to ``<file>.sponsio.bak``
  before any byte is written.  If anything goes wrong the user has a
  trivially-restorable copy.
* **Validate after.**  After the edit, the result is re-parsed with
  :mod:`ast`.  Any ``SyntaxError`` triggers a rollback from the .bak
  so the user never sees a half-patched file.
* **Idempotent.**  The patch is a no-op when an existing
  ``from sponsio.<framework> import Sponsio`` is detected — re-running
  ``--apply`` after a successful one prints "already patched" rather
  than appending a duplicate.
* **Conservative.**  Only frameworks with an obvious wrap shape
  (``langgraph``, ``langchain``) are patched today.  Other frameworks
  fall back to printing the snippet — the manual step is two-line so
  not patching is always a safe non-action.

Why a separate module from ``onboard.py``?  AST patching has its own
edge cases (multiple call sites, kwargs vs positional, chained
calls) and benefits from being unit-testable against a focused
fixture set without dragging in framework / provider detection.
"""

from __future__ import annotations

import ast
import shutil
from dataclasses import dataclass
from pathlib import Path

# Frameworks where we can do a structural patch today.  Everything
# else defers to the printed snippet.
_AUTO_APPLIABLE_FRAMEWORKS = frozenset({"langgraph", "langchain"})

# The single call we look for inside the user's source.  When extending
# this list, also extend ``_FACTORY_BY_FRAMEWORK`` so the inserted
# import matches the call we're wrapping.
_FRAMEWORK_TARGET_CALLS: dict[str, tuple[str, ...]] = {
    "langgraph": ("create_react_agent",),
    # LangChain users typically wrap a tool list before passing it to
    # ``initialize_agent`` or a custom AgentExecutor — neither is a
    # universal call site, so we match the most popular two.
    "langchain": ("create_react_agent", "initialize_agent"),
}

_FACTORY_BY_FRAMEWORK: dict[str, str] = {
    "langgraph": "sponsio.langgraph",
    "langchain": "sponsio.langgraph",
}


@dataclass
class ApplyResult:
    """Outcome of :func:`apply_patch`.

    Always returned — never raised.  ``applied=False`` plus a
    ``reason`` is the normal "we didn't patch but didn't fail either"
    case (already-patched files, unknown framework, no target call
    site).  ``applied=False`` plus ``error`` indicates an actual
    failure that the CLI should surface to the user.
    """

    applied: bool = False
    file: Path | None = None
    backup: Path | None = None
    reason: str = ""
    error: str = ""
    diff: str = ""

    @property
    def ok(self) -> bool:
        """True iff the call returned without raising AND wasn't a hard error."""
        return not self.error


def apply_patch(
    entry_file: Path,
    *,
    framework: str,
    agent_id: str,
    config_relpath: str = "sponsio.yaml",
) -> ApplyResult:
    """Patch ``entry_file`` to wrap the agent's tools with Sponsio.

    Args:
        entry_file: The framework-specific entry file (typically the
            one returned by :func:`sponsio.onboard.detect_framework`).
        framework: One of the canonical framework ids from
            ``_FRAMEWORK_FACTORY``.  Frameworks not in
            :data:`_AUTO_APPLIABLE_FRAMEWORKS` cause a soft skip with
            ``reason`` populated — never an error.
        agent_id: Agent identifier baked into the inserted
            ``Sponsio(agent_id=...)`` call.  Should match the id
            written into ``sponsio.yaml``.
        config_relpath: Path to the sponsio.yaml passed to
            ``Sponsio(config=...)``.  Caller may resolve to an
            absolute path; we use it verbatim.

    Returns:
        :class:`ApplyResult` describing what (if anything) changed.
    """
    if framework not in _AUTO_APPLIABLE_FRAMEWORKS:
        return ApplyResult(
            applied=False,
            file=entry_file,
            reason=(
                f"auto-apply not yet implemented for framework "
                f"{framework!r}; the wrap snippet is two lines — apply "
                f"it manually for now."
            ),
        )

    if entry_file is None or not entry_file.is_file():
        return ApplyResult(
            applied=False,
            file=entry_file,
            reason=(
                "no entry file was detected — re-run with the source "
                "you want patched, or apply the snippet manually."
            ),
        )

    factory = _FACTORY_BY_FRAMEWORK[framework]

    try:
        original = entry_file.read_text(encoding="utf-8")
    except OSError as e:
        return ApplyResult(
            applied=False,
            file=entry_file,
            error=f"could not read {entry_file}: {e}",
        )

    # Idempotency: already patched?
    if f"from {factory} import Sponsio" in original:
        return ApplyResult(
            applied=False,
            file=entry_file,
            reason="already patched (Sponsio import present)",
        )

    try:
        tree = ast.parse(original)
    except SyntaxError as e:
        return ApplyResult(
            applied=False,
            file=entry_file,
            error=(
                f"{entry_file.name} has a SyntaxError ({e.msg} at "
                f"line {e.lineno}); refuse to patch a file we can't "
                "verify."
            ),
        )

    # Find the line number after the last import statement.  This is
    # where we'll insert ``from sponsio.* import Sponsio`` and the
    # ``_sponsio_guard = Sponsio(...)`` line.  Falls back to line 1
    # for files with no imports at all (rare but valid).
    last_import_line = 0
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            # ast nodes are 1-indexed; pick the END of the statement
            # so multi-line imports (``from x import (\n  a,\n  b\n)``)
            # don't get truncated.
            end = getattr(node, "end_lineno", node.lineno)
            if end > last_import_line:
                last_import_line = end

    # Find target call sites we can wrap.  We want each call whose
    # function name matches one of the framework's target calls — and
    # whose second positional arg is the tools expression.
    targets = _FRAMEWORK_TARGET_CALLS[framework]
    call_sites: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn_name = _call_func_name(node)
        if fn_name in targets:
            call_sites.append(node)

    if not call_sites:
        return ApplyResult(
            applied=False,
            file=entry_file,
            reason=(
                f"didn't find a {' / '.join(targets)} call in "
                f"{entry_file.name}; apply the snippet manually."
            ),
        )

    # Build the new file text by line-level edits.  Operating on lines
    # rather than via ast.unparse preserves the user's formatting,
    # comments, and blank-line hygiene of unrelated code.
    lines = original.splitlines(keepends=True)

    # 1. Wrap each target call's tools argument.
    edits: list[
        tuple[int, int, int, str]
    ] = []  # (lineno, col_offset, end_col_offset, replacement)
    wrapped_count = 0
    for call in call_sites:
        tools_arg = _resolve_tools_arg(call)
        if tools_arg is None:
            continue
        if isinstance(tools_arg, ast.Call) and _call_func_name(tools_arg) == "wrap":
            # Already wrapped (e.g. user partially applied earlier).
            continue
        line_idx = tools_arg.lineno - 1
        col_start = tools_arg.col_offset
        col_end = getattr(tools_arg, "end_col_offset", None)
        end_lineno = getattr(tools_arg, "end_lineno", tools_arg.lineno)
        if col_end is None or end_lineno != tools_arg.lineno:
            # Multi-line tools expression — too risky to slice
            # textually.  Skip this site (apply it manually).
            continue
        existing = lines[line_idx][col_start:col_end]
        replacement = f"_sponsio_guard.wrap({existing})"
        edits.append((tools_arg.lineno, col_start, col_end, replacement))
        wrapped_count += 1

    if wrapped_count == 0:
        return ApplyResult(
            applied=False,
            file=entry_file,
            reason=(
                f"found {len(call_sites)} candidate call(s) but their "
                "tools argument shape is too complex to patch safely — "
                "apply the snippet manually."
            ),
        )

    # Apply edits in reverse so earlier offsets stay valid as we mutate.
    edits.sort(key=lambda e: (e[0], e[1]), reverse=True)
    for lineno, col_start, col_end, replacement in edits:
        line = lines[lineno - 1]
        lines[lineno - 1] = line[:col_start] + replacement + line[col_end:]

    # 2. Insert import + guard init after the last import (or at top).
    #
    # Spacing strategy: consume any blank lines immediately after
    # ``insert_at`` and re-emit normalized PEP-8 spacing.  Without
    # this the user's existing 1-2 blanks stack on top of our
    # ``\n\n`` and the patched file ends up with 3-4 blank lines
    # between the guard line and the next decorator — cosmetic but
    # ugly, and noisy in the diff banner the CLI prints back.
    insert_at = last_import_line  # 0 → top of file; otherwise after the last import
    consume_idx = insert_at
    while consume_idx < len(lines) and lines[consume_idx].strip() == "":
        consume_idx += 1
    del lines[insert_at:consume_idx]

    has_following_code = insert_at < len(lines)
    # One blank above (separator from imports), guard block, then two
    # blanks below to satisfy PEP-8's module-level separator before
    # the next def / class / decorator.  When there's no following
    # code we keep just one trailing newline so the file doesn't end
    # in extra blank lines.
    trailing = "\n\n" if has_following_code else ""
    insert_block = (
        "\n"
        f"# Added by `sponsio onboard --apply`\n"
        f"from {factory} import Sponsio as _SponsioGuardFactory\n"
        f'_sponsio_guard = _SponsioGuardFactory(config="{config_relpath}", '
        f'agent_id="{agent_id}")\n'
        f"{trailing}"
    )
    lines.insert(insert_at, insert_block)

    new_text = "".join(lines)

    # Verify the result still parses.  This is the line of defence
    # against AST-locator mistakes (e.g. a call we matched whose
    # column offsets don't actually slice cleanly).
    try:
        ast.parse(new_text)
    except SyntaxError as e:
        return ApplyResult(
            applied=False,
            file=entry_file,
            error=(
                f"patch produced invalid Python ({e.msg} at line "
                f"{e.lineno}); aborting before write."
            ),
        )

    # Backup, write, return diff.
    backup = entry_file.with_suffix(entry_file.suffix + ".sponsio.bak")
    try:
        shutil.copyfile(entry_file, backup)
        entry_file.write_text(new_text, encoding="utf-8")
    except OSError as e:
        return ApplyResult(
            applied=False,
            file=entry_file,
            error=f"failed to write patched file: {e}",
        )

    diff = _summarize_diff(original, new_text)
    return ApplyResult(
        applied=True,
        file=entry_file,
        backup=backup,
        diff=diff,
        reason=f"patched {wrapped_count} call site(s) in {entry_file.name}",
    )


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _call_func_name(call: ast.Call) -> str:
    """Return the textual function name of an ``ast.Call`` (last attr)."""
    fn = call.func
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return ""


def _resolve_tools_arg(call: ast.Call) -> ast.AST | None:
    """Return the ast node corresponding to the ``tools`` argument.

    Convention: most agent constructors take ``tools`` as the second
    positional or as a keyword.  We check positional first (the
    common ``create_react_agent(model, tools)`` shape) then fall back
    to the keyword.
    """
    if len(call.args) >= 2:
        return call.args[1]
    for kw in call.keywords:
        if kw.arg == "tools":
            return kw.value
    return None


def _summarize_diff(before: str, after: str) -> str:
    """Compact unified diff for the CLI banner.

    We don't pull in ``difflib.unified_diff`` because the patch is
    small (≤ 4 line changes) and a homemade summary keeps the output
    inside one screen even on small terminals.
    """
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    if len(before_lines) == len(after_lines):
        # Pure replacement — show only the differing lines.
        out: list[str] = []
        for i, (a, b) in enumerate(zip(before_lines, after_lines), start=1):
            if a != b:
                out.append(f"  {i:>4} - {a}")
                out.append(f"  {i:>4} + {b}")
        return "\n".join(out) if out else "(no visible diff)"
    # Insertion — show the inserted block by walking until streams
    # diverge, then printing the inserted lines.
    i = 0
    while (
        i < len(before_lines)
        and i < len(after_lines)
        and before_lines[i] == after_lines[i]
    ):
        i += 1
    inserted = len(after_lines) - len(before_lines)
    out = []
    for j in range(i, min(i + inserted + 4, len(after_lines))):
        prefix = "+" if j < i + inserted else " "
        out.append(f"  {j + 1:>4} {prefix} {after_lines[j]}")
    return "\n".join(out)
