"""Loader for ``.sponsiorc`` — sponsio environment / runtime config.

``.sponsiorc`` is the dotfile written by ``sponsio onboard`` (see
:mod:`sponsio.onboard_setup`) holding the user's framework + LLM
provider / model / api_key_env choices.  This module is the single
read-side counterpart — every sponsio command that wants to honour
those choices imports from here.

Lookup precedence the rest of the codebase agrees on (highest →
lowest):

  1. Explicit CLI flag (``--provider``, ``--model``, ``--framework``).
  2. ``.sponsiorc`` in the project directory or any parent up to the
     git root.
  3. Environment variables (``GOOGLE_API_KEY``, ``ANTHROPIC_API_KEY``,
     ``OPENAI_API_KEY``, ...).
  4. Built-in defaults baked into the codebase.

Steps 2 and 3 swapped vs. the pre-Step-2 behaviour: ``.sponsiorc`` now
beats env vars, so a user who picked ``provider: anthropic`` in their
project but happens to have a stale ``GOOGLE_API_KEY`` exported gets
the project choice rather than the surprise-Gemini path.

Failure modes are deliberately silent — a missing or malformed
``.sponsiorc`` returns an empty :class:`SponsioRcConfig` with
``found = False``.  We don't want an unparseable rcfile to crash
``sponsio scan`` mid-pipeline; the empty config falls through to env
+ defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


_FILENAME = ".sponsiorc"

# Maximum number of parent dirs we walk before giving up — prevents
# scanning the full filesystem when an .sponsiorc isn't anywhere.
# Eight is plenty: monorepos are typically 3-5 levels deep.
_SEARCH_LIMIT = 8


@dataclass
class SponsioRcConfig:
    """Parsed ``.sponsiorc`` content, plus where it came from.

    All fields default to ``None`` so the caller can distinguish
    "user set this" from "user left this for sponsio to figure out".
    Sample call site shape::

        rc = load_sponsiorc(Path.cwd())
        if rc.extractor_provider:
            provider = rc.extractor_provider
        else:
            provider = ...env-var fallback...
    """

    framework: Optional[str] = None
    extractor_provider: Optional[str] = None
    extractor_model: Optional[str] = None
    extractor_api_key_env: Optional[str] = None
    judge_provider: Optional[str] = None
    judge_model: Optional[str] = None
    judge_api_key_env: Optional[str] = None
    judge_fallback_mode: Optional[str] = None

    # Path to the ``.sponsiorc`` we read (or None when no file was
    # found / the file was malformed).  Useful in error messages —
    # "from .sponsiorc at /Users/.../foo/.sponsiorc" beats the
    # hand-wavy "from project config".
    source_path: Optional[Path] = None

    @property
    def found(self) -> bool:
        """True iff a .sponsiorc file was actually located + parsed."""
        return self.source_path is not None


def find_sponsiorc(start: Path) -> Path | None:
    """Walk up from ``start`` looking for a ``.sponsiorc``.

    Stops on the first hit, at filesystem root, or after
    :data:`_SEARCH_LIMIT` ascents.  Also stops once we've passed the
    git root (presence of a sibling ``.git/``) — beyond the repo we
    have no business reading config.

    Args:
        start: Starting directory or file path.  When ``start`` is a
            file (e.g. user passed ``sponsio onboard agent.py``) we
            search from its parent.

    Returns:
        Path to the discovered ``.sponsiorc`` or ``None``.
    """
    if not start.exists():
        return None
    current = start.resolve() if start.is_dir() else start.resolve().parent

    for _ in range(_SEARCH_LIMIT):
        candidate = current / _FILENAME
        if candidate.is_file():
            return candidate

        # Past a git boundary — don't leak into a parent repo / home dir.
        if (current / ".git").exists():
            return None

        parent = current.parent
        if parent == current:  # filesystem root
            return None
        current = parent

    return None


def load_sponsiorc(start: Path | None = None) -> SponsioRcConfig:
    """Locate and parse ``.sponsiorc`` starting from ``start``.

    Args:
        start: Directory (or file) to begin the upward search.
            Defaults to ``Path.cwd()``.

    Returns:
        ``SponsioRcConfig`` — empty (all fields None) when no file
        was located or parsing failed.  Callers should branch on
        :attr:`SponsioRcConfig.found` rather than checking individual
        fields, so a partially-filled rcfile reads consistently.
    """
    if start is None:
        start = Path.cwd()

    path = find_sponsiorc(start)
    if path is None:
        return SponsioRcConfig()

    # ``yaml`` is an optional dep at the package level (sponsio[config]).
    # If it's missing we silently skip the rcfile — sponsio.yaml loading
    # already requires PyYAML, so this branch only fires in heavily
    # stripped-down installs.
    try:
        import yaml
    except ImportError:
        return SponsioRcConfig()

    try:
        raw = path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
    except Exception:
        # Malformed rcfile — better to ignore than to abort the
        # whole command.  We deliberately don't log here because
        # the same rcfile could be read 3-4 times per command and
        # we'd spam stderr.
        return SponsioRcConfig()

    if not isinstance(data, dict):
        return SponsioRcConfig()

    return _parse_dict(data, source_path=path)


def _parse_dict(data: dict, *, source_path: Path) -> SponsioRcConfig:
    """Pull the recognised fields out of a yaml-loaded dict.

    Unknown keys are ignored — forward-compat with future rcfile
    additions and tolerant of users sticking comments / scratchpad
    keys in there.  Sub-dicts that aren't actually dicts (user typo'd
    ``extractor: gemini`` instead of ``extractor:\\n  provider:
    gemini``) get silently coerced to empty so we don't crash.
    """
    extractor = data.get("extractor")
    judge = data.get("judge")
    extractor = extractor if isinstance(extractor, dict) else {}
    judge = judge if isinstance(judge, dict) else {}

    def _str_or_none(v: Any) -> Optional[str]:
        if v is None:
            return None
        return str(v).strip() or None

    return SponsioRcConfig(
        framework=_str_or_none(data.get("framework")),
        extractor_provider=_str_or_none(extractor.get("provider")),
        extractor_model=_str_or_none(extractor.get("model")),
        extractor_api_key_env=_str_or_none(extractor.get("api_key_env")),
        judge_provider=_str_or_none(judge.get("provider")),
        judge_model=_str_or_none(judge.get("model")),
        judge_api_key_env=_str_or_none(judge.get("api_key_env")),
        judge_fallback_mode=_str_or_none(judge.get("fallback_mode")),
        source_path=source_path,
    )
