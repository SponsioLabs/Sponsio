"""Terminal reporter for runtime contract enforcement.

Registers as a RuntimeMonitor callback to provide real-time
CLI feedback during agent execution. Zero external dependencies.

Usage::

    from sponsio.langgraph import Sponsio

    # Auto-enabled (verbose=True is the default)
    guard = Sponsio(contracts=[...])

    # Or explicitly with verbosity control
    guard = Sponsio(contracts=[...], verbose=True, verbosity=2)

Verbosity levels:
    0 — violations only
    1 — default: violations + assumption first-satisfied + contract
        activation events. Enforcement *pass* lines are suppressed
        (one per tool call per constraint gets noisy fast).
    2 — everything: every enforcement pass line too, plus span
        trees on violations.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sponsio.models.contract import Contract
    from sponsio.runtime.monitor import MonitorEvent

# ANSI codes
_GREEN = "32"
_RED = "31;1"
_YELLOW = "33"
_BLUE = "34"
_BOLD = "1"
_DIM = "2"
_RESET = "0"


class TerminalReporter:
    """Pretty-print enforcement events to the terminal.

    Args:
        verbosity: 0=violations only, 1=default (violations + activation
            events), 2=all checks incl. passes + span trees on violations.
        colorize: Auto-detected from TTY, or set explicitly.
        contracts: List of :class:`~sponsio.models.contract.Contract`
            objects to display at startup and to resolve assumption→
            contract-label for activation lines.
    """

    def __init__(
        self,
        verbosity: int = 1,
        colorize: bool | None = None,
        contracts: list | None = None,
    ) -> None:
        self.verbosity = verbosity
        self.colorize = colorize if colorize is not None else sys.stderr.isatty()
        self._contracts = contracts or []  # list of Contract objects
        self._header_printed = False
        # Assumption desc → short contract label (resolved from contracts
        # on first print). Used to name the contract that just went live.
        self._assumption_to_label: dict[str, str] = {}
        # Assumption desc → has been shown as satisfied exactly once.
        self._seen_satisfied: set[str] = set()

    def __call__(self, event: MonitorEvent) -> None:
        """Handle a MonitorEvent from the RuntimeMonitor."""
        if not self._header_printed:
            self._print_header()
            self._header_printed = True

        extra = self._format(event)
        if extra is None:
            return
        for line in extra if isinstance(extra, list) else [extra]:
            print(line, file=sys.stderr)

        # Verbosity 2: print span tree on violations
        if self.verbosity >= 2 and event.result.action in (
            "blocked",
            "escalated",
            "retrying",
        ):
            self._print_span_tree(event)

    def _print_header(self) -> None:
        """Delegate to the module-level :func:`print_banner` so it can be
        called without having a reporter instance (e.g. when
        ``verbose=False`` but we still want the loaded-contracts banner)."""
        print_banner(self._contracts, colorize=self.colorize)
        # Still build the label map — the reporter uses it at event time.
        self._build_label_map()

    def _format(self, event: MonitorEvent):
        """Return a string (or list of strings) to print, or ``None`` to skip."""
        action_str = event.result.action
        # ``observed`` is the observe/shadow-mode counterpart of
        # ``blocked`` — the enforcement failed, we just chose not to
        # stop the agent. Treat it as a violation for display so
        # shadow runs get the same visibility as enforce runs.
        is_violation = action_str in (
            "blocked",
            "escalated",
            "retrying",
            "observed",
        )

        # Verbosity 0: violations only
        if self.verbosity == 0 and not is_violation:
            return None

        if event.pipeline == "det":
            return self._format_hard(event, is_violation)
        elif event.pipeline == "sto":
            return self._format_soft(event, is_violation)
        return None

    def _format_hard(self, event: MonitorEvent, is_violation: bool):
        name = event.constraint_name
        is_assumption = name.startswith("assumption: ")
        desc = name[len("assumption: ") :] if is_assumption else name
        is_observed = event.result.action == "observed"

        if is_assumption:
            # An unsatisfied assumption (escalated) at step N just means the
            # contract is dormant, not a real violation. Skip at v<2.
            if is_violation:
                if self.verbosity < 2:
                    return None
                icon = self._ansi(_YELLOW, "▸")
                kind = self._ansi(_YELLOW, "assume ")
                desc_fmt = self._ansi(_DIM, desc)
                verdict = self._ansi(_DIM, "not yet satisfied")
                return f"  {icon} {kind} {desc_fmt} — {verdict}"

            # Assumption satisfied: only announce the first time. Then
            # also emit the "contract X is now active" line.
            if desc in self._seen_satisfied:
                return None
            self._seen_satisfied.add(desc)
            icon = self._ansi(_YELLOW, "▸")
            kind = self._ansi(_YELLOW, "assume ")
            desc_fmt = self._ansi(_DIM, desc)
            verdict = self._ansi(_YELLOW, "satisfied")
            lines = [f"  {icon} {kind} {desc_fmt} — {verdict}"]
            label = self._assumption_to_label.get(desc)
            if label:
                bolt = self._ansi(_GREEN, "⚡")
                msg = self._ansi(_GREEN, f"contract '{label}' is now active")
                lines.append(f"  {bolt} {msg}")
            return lines

        # Enforcement events.
        if is_violation:
            if is_observed:
                # Observe / shadow mode: the contract would have
                # blocked but didn't. Use warning yellow so the user
                # can still tell this is not a real block.
                icon = self._ansi(_YELLOW, "⚠")
                kind = self._ansi(_YELLOW, "enforce")
                desc_fmt = self._ansi(_DIM, desc)
                verdict = self._ansi(_YELLOW, "WOULD-BLOCK (observe)")
                return f"  {icon} {kind} {desc_fmt} — {verdict}"
            icon = self._ansi(_RED, "✗")
            kind = self._ansi(_RED, "enforce")
            desc_fmt = self._ansi(_DIM, desc)
            verdict = self._ansi(_RED, f"VIOLATED → {event.result.action}")
            return f"  {icon} {kind} {desc_fmt} — {verdict}"

        # Enforcement PASS — suppress at verbosity<2, they're noisy
        # (one per tool call per constraint).
        if self.verbosity < 2:
            return None
        icon = self._ansi(_GREEN, "✓")
        kind = self._ansi(_GREEN, "enforce")
        desc_fmt = self._ansi(_DIM, desc)
        verdict = self._ansi(_GREEN, "pass")
        return f"  {icon} {kind} {desc_fmt} — {verdict}"

    def _format_soft(self, event: MonitorEvent, is_violation: bool) -> str:
        score_str = ""
        if event.sto_result is not None:
            score_str = f" score {event.sto_result.score:.2f}"

        if is_violation:
            icon = self._ansi(_YELLOW, "⚠")
            status = self._ansi(_YELLOW, f"{score_str} → retrying with feedback")
        else:
            icon = self._ansi(_GREEN, "✓")
            status = self._ansi(_GREEN, f"{score_str} passed")

        action = self._ansi(_DIM, f'"{event.action}"')
        constraint = self._ansi(_DIM, f'"{event.constraint_name}"')
        return f"  {icon} [sto] action {action} — {constraint}{status}"

    def _print_span_tree(self, event: MonitorEvent) -> None:
        """Print the span tree for the current turn (verbosity >= 2).

        Best-effort: prints nothing if span data isn't available.
        """

    # ---------------------------------------------------------------
    # Helpers for contract labelling
    # ---------------------------------------------------------------

    def _build_label_map(self) -> None:
        """Populate ``_assumption_to_label`` from ``self._contracts``."""
        for c in self._contracts:
            for a in getattr(c, "assumptions", []) or []:
                assume_desc = getattr(a, "desc", str(a))
                label = self._contract_label(c)
                if assume_desc and label:
                    self._assumption_to_label[assume_desc] = label

    @staticmethod
    def _contract_label(c) -> str:
        """Return a short display label for a contract.

        Prefers ``Contract.desc`` if the author set one; otherwise uses
        the first enforcement's description as a reasonable stand-in.
        """
        desc = getattr(c, "desc", None)
        if desc:
            return str(desc)
        enforcements = getattr(c, "enforcements", []) or []
        if enforcements:
            return str(getattr(enforcements[0], "desc", "") or "")
        return getattr(getattr(c, "agent", None), "id", "") or ""

    def _ansi(self, code: str, text: str) -> str:
        if not self.colorize:
            return text
        return f"\033[{code}m{text}\033[0m"


def _is_bare(c: Contract | object) -> bool:
    """True if the contract has no assumption (active from start)."""
    assumptions = getattr(c, "assumptions", []) or []
    return not assumptions


def _ansi_code(code: str, text: str, colorize: bool) -> str:
    if not colorize:
        return text
    return f"\033[{code}m{text}\033[0m"


def print_banner(contracts: list, colorize: bool | None = None) -> None:
    """Print the A/G contract banner to stderr.

    Called at :func:`sponsio.Sponsio` time regardless of ``verbose=`` so
    operators can always see which rules are loaded — without this,
    ``verbose=False`` is visually indistinguishable from "no Sponsio at all".

    Args:
        contracts: List of :class:`~sponsio.models.contract.Contract` objects.
        colorize: Emit ANSI colour codes. Auto-detected from TTY if ``None``.
    """
    if not contracts:
        return
    if colorize is None:
        colorize = sys.stderr.isatty()

    top = _ansi_code(_DIM, "━━━ ◒◓ sponsio ", colorize)
    top += _ansi_code(_DIM, "━" * 37, colorize)
    bottom = _ansi_code(_DIM, "━" * 50, colorize)
    print(f"\n  {top}", file=sys.stderr)

    for i, c in enumerate(contracts):
        if hasattr(c, "to_str"):
            for line in c.to_str(colorize=colorize).splitlines():
                print(f"  {line}", file=sys.stderr)
        else:
            print(f"  {c}", file=sys.stderr)
        if i < len(contracts) - 1:
            print("", file=sys.stderr)

    print(f"  {bottom}", file=sys.stderr)

    # Name the unconditional contracts so the viewer sees which rules
    # are live at step 0.
    bare = [TerminalReporter._contract_label(c) for c in contracts if _is_bare(c)]
    bare = [b for b in bare if b]
    if bare:
        # One contract per line — when 20+ unconditional contracts are
        # active (typical with auto-included packs like core/universal +
        # core/runaway) the old comma-joined single line wraps to a
        # ~1500-char paragraph that's unreadable.  Bullet form keeps
        # every contract on its own line, easy to skim or grep.
        active = _ansi_code(_GREEN, "⚡ active from start:", colorize)
        print(f"  {active}", file=sys.stderr)
        for label in bare:
            bullet = _ansi_code(_DIM, "  · ", colorize)
            print(f"   {bullet}{label}", file=sys.stderr)
    print("", file=sys.stderr)
