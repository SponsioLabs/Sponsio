"""Stub for the sto (stochastic / LLM-judge) pipeline.

The real ``StoFormula`` + sto evaluation engine is a Sponsio Cloud
feature, not bundled with the OSS engine. This stub keeps lazy
``from sponsio.patterns.sto import StoFormula`` imports across the
codebase from breaking at module load — instead, the failure surfaces
when sto behavior is actually invoked.

Operators who want the sto pipeline:

    pip install sponsio[cloud]

or contact your Sponsio account team for hosted access.
"""

from __future__ import annotations


class _CloudFeatureError(ImportError):
    """Raised when OSS code paths reach a sto-only operation.

    Subclasses :class:`ImportError` so the standard ``try/except
    ImportError`` guard already used at most lazy-import sites
    catches it as the cloud-feature absence signal.
    """


class StoFormula:
    """Placeholder for the cloud :class:`StoFormula` AST.

    Constructing one in OSS raises :class:`_CloudFeatureError` with a
    pointer to the cloud package. The class itself is importable so
    ``isinstance`` checks and type hints elsewhere don't break.
    """

    def __init__(self, *args, **kwargs) -> None:
        raise _CloudFeatureError(
            "sponsio.patterns.sto.StoFormula is a Sponsio Cloud feature. "
            "Install via `pip install sponsio[cloud]` or contact your "
            "account team."
        )

    def __init_subclass__(cls, **kwargs) -> None:
        # Allow downstream subclassing for type compatibility, but the
        # base ``__init__`` still raises if anyone instantiates the
        # subclass without overriding it.
        super().__init_subclass__(**kwargs)
