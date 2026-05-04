"""Lightweight ANSI spinner for long stderr-emit gaps.

Used by the CLI progress sink to indicate that a multi-second wait
(LLM inference, doctor probes, trace replay) is alive rather than
hung.  Pure stdlib — no yaspin / halo / rich dependency — so onboard
stays light.

Usage::

    from sponsio.runtime.spinner import Spinner

    spinner = Spinner()
    spinner.start("· Running LLM inference (...)…")
    do_long_work()
    spinner.stop("· LLM inference done in 15.7s")

Skips silently when stderr isn't a TTY (CI / pipe / docker entrypoint),
falling back to a single immediate print of the label so structured
output stays uncorrupted.

The braille frames match yaspin / ora / spinning_wheels visually so
users coming from those tools get the expected effect.  Cadence is
10 Hz — the same default — fast enough to feel alive, slow enough
that piping into a slow terminal doesn't choke.
"""

from __future__ import annotations

import sys
import threading
from typing import Optional


_BRAILLE_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# 10 Hz refresh — matches yaspin's default and is the standard
# "feels alive but not jittery" cadence.  Tune carefully: too fast
# (>20 Hz) starts to flicker on slow terminals; too slow (<5 Hz)
# reads as "stuck" again.
_FRAME_INTERVAL = 0.1

# ANSI escape: carriage return + erase-to-end-of-line.  Used both
# while spinning (overwrite the previous frame in place) and on stop
# (wipe the spinner so the next print doesn't land on top of a stale
# frame).  Bare ``\r`` would only reset the cursor — terminals that
# render via wide glyphs leave residue if we don't also clear.
_LINE_RESET = "\r\x1b[2K"


class Spinner:
    """Single-instance spinner driver — start one, stop it, no nesting.

    Caller invariant: pause other stderr emits while a spinner is
    running.  The CLI's progress sink enforces this by calling
    :meth:`stop` before printing any subsequent line.

    Not thread-safe to use from multiple writer threads; designed for
    a single CLI command's progress callback.
    """

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._label = ""

    @staticmethod
    def stderr_is_tty() -> bool:
        """Return True when stderr is an interactive terminal.

        Robust against unusual embedding contexts where ``sys.stderr``
        is a custom object — we treat any error as "not a TTY" so the
        spinner is silent rather than crashing the host command.
        """
        try:
            return sys.stderr.isatty()
        except Exception:
            return False

    def start(self, label: str) -> None:
        """Begin spinning with ``label`` after the spinner glyph.

        No-op when stderr isn't a TTY (label is printed once instead,
        so the user still sees the "Running …" line in CI logs).
        Calling :meth:`start` twice without an intervening :meth:`stop`
        is a no-op — we don't try to swap labels mid-spin since that
        usually indicates a missing stop call upstream.

        Replaces a trailing ``…`` (the CLI's spinner-trigger sentinel)
        with three spaced dots ``. . .`` — the spaced form reads as
        deliberate progress hint instead of typographic afterthought,
        and pairs with the rotating braille glyph at the front of
        the line for a balanced "spinning + waiting" indicator.
        """
        # The trailing Unicode ellipsis is the convention upstream
        # uses to flag "long-running, start a spinner".  Replace it
        # with the spaced ``. . .`` form once we're rendering — the
        # braille glyph is the primary "in progress" signal; the
        # dots are secondary breathing room.
        label = label.rstrip()
        if label.endswith("…"):
            label = label[:-1].rstrip() + " . . ."

        if not self.stderr_is_tty():
            print(label, file=sys.stderr, flush=True)
            return
        if self._thread is not None:
            return
        self._label = label
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, final: Optional[str] = None) -> None:
        """Stop the spinner and optionally print ``final`` in its place.

        Idempotent: safe to call when no spinner is running (this is
        the common case from the CLI's progress sink, which stops
        unconditionally before each emit).  ``final`` is printed only
        when supplied; passing ``None`` just clears the spinner line
        (the caller will print whatever should follow).
        """
        if self._thread is not None:
            self._stop_event.set()
            # Generous join — the worker checks the event every
            # ``_FRAME_INTERVAL`` so 5x is plenty of headroom.
            self._thread.join(timeout=5 * _FRAME_INTERVAL)
            self._thread = None
            sys.stderr.write(_LINE_RESET)
            sys.stderr.flush()
        if final is not None:
            print(final, file=sys.stderr, flush=True)

    def _run(self) -> None:
        i = 0
        while not self._stop_event.is_set():
            frame = _BRAILLE_FRAMES[i % len(_BRAILLE_FRAMES)]
            # ``_LINE_RESET`` then frame + label — single fwrite so
            # the redraw is atomic from the terminal's POV (no
            # half-rendered glyph during a refresh).
            sys.stderr.write(f"{_LINE_RESET}{frame} {self._label}")
            sys.stderr.flush()
            i += 1
            # ``Event.wait`` returns early when set, so a stop()
            # mid-frame is responsive (≤ _FRAME_INTERVAL seconds).
            self._stop_event.wait(_FRAME_INTERVAL)
