"""Stub for the sto atom catalog (LLM-judge evaluators).

The real catalog (``no_pii`` / ``tone_polite`` / ``injection_free`` /
``hallucination_free`` / `harmful` / …) is a Sponsio Cloud feature. The
OSS engine has no built-in sto evaluators; contracts that include sto
atoms get logged-and-skipped by :func:`sponsio.runtime.monitor._warn_sto_skipped`
at evaluation time.

This stub keeps lazy ``from sponsio.patterns.sto_catalog import …``
imports across the codebase from breaking at module load.
"""

from __future__ import annotations

import contextlib
from typing import Any

# Empty catalog — no built-in evaluators in OSS.
_SOFT_CATALOG: dict[str, Any] = {}


def _cloud_only_evaluator(*args, **kwargs):
    """Stub evaluator for the registered atom names. Raises if called.

    Lazy ``from sponsio.patterns.sto_catalog import …_evaluator`` calls
    around the codebase resolve to this single helper so OSS imports
    don't break; actually invoking the evaluator surfaces the
    cloud-feature notice.
    """
    raise ImportError(
        "Sto evaluators (no_pii / tone / pii / relevance / length / format / "
        "llm_judge / content_prohibition) are Sponsio Cloud features. "
        "Install via `pip install sponsio[cloud]`."
    )


# Aliases the cloud catalog exposes — kept here as no-op stubs so the
# OSS package can be imported without ``ImportError`` from sites that
# do ``from sponsio.patterns.sto_catalog import pii_evaluator``.
pii_evaluator = _cloud_only_evaluator
length_evaluator = _cloud_only_evaluator
format_evaluator = _cloud_only_evaluator
content_prohibition_evaluator = _cloud_only_evaluator
tone_evaluator = _cloud_only_evaluator
relevance_evaluator = _cloud_only_evaluator
llm_judge_evaluator = _cloud_only_evaluator


def set_default_judge(judge: Any) -> None:
    """No-op — there's no judge harness in the OSS build."""


@contextlib.contextmanager
def _use_judge(judge: Any):
    """No-op context manager — sto evaluation is cloud-only in OSS."""
    yield


def _require_judge() -> Any:
    """Raise if any sto evaluator demands a judge — unreachable in OSS
    because no sto evaluators are registered in the first place."""
    raise ImportError(
        "No sto judge available — Sponsio Cloud is required for the "
        "sto pipeline. Install via `pip install sponsio[cloud]`."
    )
