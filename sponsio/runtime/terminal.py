"""Terminal reporter for runtime contract enforcement.

Registers as a RuntimeMonitor callback to provide real-time
CLI feedback during agent execution. Zero external dependencies.

Usage::

    from sponsio.integrations.langgraph import LangGraphGuard

    # Auto-enabled (verbose=True is the default)
    guard = LangGraphGuard(contracts=[...])

    # Or explicitly with verbosity control
    guard = LangGraphGuard(contracts=[...], verbose=True, verbosity=2)
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
        verbosity: 0=violations only, 1=all checks, 2=checks+span tree.
        colorize: Auto-detected from TTY, or set explicitly.
        contracts: List of contract NL strings to display at startup.
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

    def __call__(self, event: MonitorEvent) -> None:
        """Handle a MonitorEvent from the RuntimeMonitor."""
        if not self._header_printed:
            self._print_header()
            self._header_printed = True

        line = self._format(event)
        if line is None:
            return
        print(line, file=sys.stderr)

        # Verbosity 2: print span tree on violations
        if self.verbosity >= 2 and event.result.action in (
            "blocked",
            "escalated",
            "retrying",
        ):
            self._print_span_tree(event)

    def _print_header(self) -> None:
        """Print contracts with A/G structure on first event."""
        if not self._contracts:
            return
        top = self._ansi(_DIM, "\u2501\u2501\u2501 \u25e1\u25e0 sponsio ")
        top += self._ansi(_DIM, "\u2501" * 37)
        bottom = self._ansi(_DIM, "\u2501" * 50)
        print(f"\n  {top}", file=sys.stderr)

        for i, c in enumerate(self._contracts):
            if hasattr(c, "to_str"):
                for line in c.to_str(colorize=self.colorize).splitlines():
                    print(f"  {line}", file=sys.stderr)
            else:
                print(f"  {c}", file=sys.stderr)

            if i < len(self._contracts) - 1:
                print("", file=sys.stderr)

        print(f"  {bottom}\n", file=sys.stderr)

    def _format(self, event: MonitorEvent) -> str | None:
        """Format a MonitorEvent into a one-line summary."""
        action_str = event.result.action
        is_violation = action_str in ("blocked", "escalated", "retrying")

        # Verbosity 0: violations only
        if self.verbosity == 0 and not is_violation:
            return None

        if event.pipeline == "det":
            return self._format_hard(event, is_violation)
        elif event.pipeline == "sto":
            return self._format_soft(event, is_violation)
        return None

    def _format_hard(self, event: MonitorEvent, is_violation: bool) -> str:
        name = event.constraint_name
        is_assumption = name.startswith("assumption: ")

        if is_assumption:
            desc = name[len("assumption: ") :]
            kind = self._ansi(_YELLOW, "assume ")
        else:
            desc = name
            kind = self._ansi(_GREEN, "enforce")

        if is_violation:
            icon = self._ansi(_RED, "\u2717")
            verdict = self._ansi(_RED, f"VIOLATED \u2192 {event.result.action}")
        else:
            icon = self._ansi(_GREEN, "\u2713")
            verdict = self._ansi(_GREEN, "pass")

        desc_fmt = self._ansi(_DIM, desc)
        return f"  {icon} {kind} {desc_fmt} \u2014 {verdict}"

    def _format_soft(self, event: MonitorEvent, is_violation: bool) -> str:
        score_str = ""
        if event.sto_result is not None:
            score_str = f" score {event.sto_result.score:.2f}"

        if is_violation:
            icon = self._ansi(_YELLOW, "\u26a0")
            status = self._ansi(_YELLOW, f"{score_str} \u2192 retrying with feedback")
        else:
            icon = self._ansi(_GREEN, "\u2713")
            status = self._ansi(_GREEN, f"{score_str} passed")

        action = self._ansi(_DIM, f'"{event.action}"')
        constraint = self._ansi(_DIM, f'"{event.constraint_name}"')
        return f"  {icon} [sto] action {action} \u2014 {constraint}{status}"

    def _print_span_tree(self, event: MonitorEvent) -> None:
        """Print the span tree for the current turn (verbosity >= 2).

        Best-effort: prints nothing if span data isn't available.
        """

    def _ansi(self, code: str, text: str) -> str:
        if not self.colorize:
            return text
        return f"\033[{code}m{text}\033[0m"
