"""Terminal reporter for runtime contract enforcement.

Registers as a RuntimeMonitor callback to print real-time contract
checks to stderr during a user's agent run. Output is Rich-rendered
on a TTY, plain text when stderr is piped or ``NO_COLOR`` is set.

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
    2 — everything: every enforcement pass line too.
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

from rich.console import Console

from sponsio.render.monitor import (
    build_label_map,
    render_banner,
    render_event,
)

if TYPE_CHECKING:
    from sponsio.runtime.monitor import MonitorEvent


def _make_stderr_console(colorize: bool | None) -> Console:
    """Build a Rich Console pinned to stderr.

    Honors ``NO_COLOR``, ``--no-color``-style ``colorize=False``, and
    auto-detects TTY when ``colorize is None``.
    """
    if colorize is False or os.environ.get("NO_COLOR"):
        color_system = None
        force = False
    elif colorize is True:
        color_system = "truecolor"
        force = True
    else:
        # Auto: let Rich detect.
        color_system = "auto"
        force = sys.stderr.isatty() or None
    return Console(
        file=sys.stderr,
        color_system=color_system,
        force_terminal=force,
        highlight=False,
        soft_wrap=True,
    )


class TerminalReporter:
    """Pretty-print enforcement events to the terminal.

    Args:
        verbosity: 0=violations only, 1=default (violations + activation
            events), 2=all checks incl. passes.
        colorize: Auto-detected from stderr TTY when ``None``.
        contracts: List of :class:`~sponsio.models.contract.Contract`
            objects — used to resolve assumption→contract-label so
            activation lines can name the contract that just went live.
    """

    def __init__(
        self,
        verbosity: int = 1,
        colorize: bool | None = None,
        contracts: list | None = None,
    ) -> None:
        self.verbosity = verbosity
        self.colorize = colorize
        self._contracts = contracts or []
        self._console = _make_stderr_console(colorize)
        # Eager build — `base.py` constructs the reporter at guard init,
        # then sets ``_header_printed = True`` because it printed the
        # banner separately. We must not depend on first-call to populate
        # the label map.
        self._assumption_to_label: dict[str, str] = build_label_map(self._contracts)
        self._seen_satisfied: set[str] = set()
        # Retained for backwards compatibility with `base.py`, which sets
        # this to True after printing the banner separately. The reporter
        # itself never prints a banner (that's `print_banner`'s job).
        self._header_printed = False

    def __call__(self, event: "MonitorEvent") -> None:
        lines = render_event(
            event,
            verbosity=self.verbosity,
            contract_label_map=self._assumption_to_label,
            seen_satisfied=self._seen_satisfied,
        )
        for line in lines:
            self._console.print(line)

    def _build_label_map(self) -> None:
        """Back-compat shim. ``base.py`` calls this after constructing
        the reporter; the new code does the work in ``__init__`` so this
        is a no-op idempotent re-builder."""
        self._assumption_to_label = build_label_map(self._contracts)


def print_banner(contracts: list, colorize: bool | None = None) -> None:
    """Print the activation banner to stderr.

    Called at :func:`sponsio.Sponsio` time regardless of ``verbose=`` so
    operators can always see which rules are loaded — without this,
    ``verbose=False`` is visually indistinguishable from "no Sponsio at
    all".

    Args:
        contracts: List of :class:`~sponsio.models.contract.Contract`
            objects.
        colorize: Force colour on/off. Auto-detected from stderr TTY
            if ``None``.
    """
    if not contracts:
        return
    console = _make_stderr_console(colorize)
    render_banner(contracts, console=console)
