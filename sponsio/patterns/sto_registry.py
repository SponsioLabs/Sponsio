"""Global registry mapping sto atom predicates to their metadata.

A sto atom's registry entry carries:

* the **evaluator** — ``(Atom, Trace, int) -> StoResult`` — resolved by
  :func:`sponsio.runtime.sto_lifting.eval_sto_confidence` at monitor
  dispatch time;
* a handful of **metadata** fields (NL keywords, required arg count,
  preferred ``context_scope`` / ``output_type``, one-line description)
  that the NL parser and the LLM extractor consume so that adding a new
  atom only requires editing one decorator call, not three files.

Usage
-----

Register an evaluator with the decorator — the evaluator is always
positional; metadata is keyword-only::

    from sponsio.patterns.sto_registry import register_sto_atom
    from sponsio.runtime.evaluators import StoResult

    @register_sto_atom(
        "injection_free",
        nl_keywords=[r"prompt\\s+injection", r"injection\\s+attack"],
        required_args=0,
        default_context_scope="event",
        default_output_type="classify",
        description="free of prompt-injection attempts",
    )
    def _eval_injection(atom, trace, t) -> StoResult:
        # Return score in [0,1] — 1.0 means guarantee holds.
        ...

Build an Atom referencing the registered predicate::

    Atom("injection_free", atom_type="sto",
         output_type="classify", context_scope="event")

At eval time the lifting function resolves via
:func:`resolve_sto_evaluator`. The NL parser and LLM extractor read
the metadata via :func:`list_sto_atom_infos` and
:func:`get_sto_atom_info`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Iterable

if TYPE_CHECKING:
    from sponsio.formulas.formula import Atom
    from sponsio.models.trace import Trace
    from sponsio.runtime.evaluators import StoResult

StoAtomEvaluator = Callable[["Atom", "Trace", int], "StoResult"]


@dataclass(frozen=True)
class StoAtomInfo:
    """One row in the sto atom registry.

    ``evaluator`` is the scoring function; the remaining fields are
    the self-description consumed by :mod:`sponsio.generation` to
    auto-wire NL parsing and LLM prompt generation.

    Fields:
        predicate: The atom predicate name (e.g. ``"injection_free"``).
        evaluator: Scoring function resolved by the lifting engine.
        nl_keywords: Regex patterns the rule-based NL parser matches
            against. Empty tuple disables auto-routing (users construct
            the atom explicitly or pass an ``llm_extractor``).
        required_args: Number of positional args the atom expects.
            ``0`` atoms can be routed from a keyword match directly;
            ``>0`` atoms typically need LLM-level translation to fill
            the args.
        default_context_scope: Preferred ``context_scope`` when the
            parser auto-constructs the atom. Almost always ``"event"``;
            cross-turn checks (``metric_integrity``) prefer
            ``"full_trace"``.
        default_output_type: Default ``output_type`` for auto-routed
            atoms. Usually ``"classify"``.
        description: One-line human-readable description used in the
            LLM extractor prompt and ``sponsio patterns`` output.
    """

    predicate: str
    evaluator: StoAtomEvaluator
    nl_keywords: tuple[str, ...] = ()
    required_args: int = 0
    default_context_scope: str = "event"
    default_output_type: str = "classify"
    description: str = ""


_REGISTRY: dict[str, StoAtomInfo] = {}


def register_sto_atom(
    predicate: str,
    *,
    nl_keywords: Iterable[str] = (),
    required_args: int = 0,
    default_context_scope: str = "event",
    default_output_type: str = "classify",
    description: str = "",
) -> Callable[[StoAtomEvaluator], StoAtomEvaluator]:
    """Register an evaluator + metadata for a sto atom predicate.

    Args:
        predicate: The atom predicate name (e.g. ``"injection_free"``).
        nl_keywords: Regex patterns the rule-based NL parser matches
            against this atom. Leave empty to disable auto-routing.
        required_args: Number of positional args this atom expects.
        default_context_scope: Preferred ``context_scope`` for
            auto-constructed atoms.
        default_output_type: Default ``output_type`` for
            auto-constructed atoms.
        description: One-line human-readable description.

    Raises:
        ValueError: If ``predicate`` is already registered.
    """

    def deco(fn: StoAtomEvaluator) -> StoAtomEvaluator:
        if predicate in _REGISTRY:
            raise ValueError(
                f"sto atom {predicate!r} is already registered by "
                f"{_REGISTRY[predicate].evaluator.__qualname__}"
            )
        _REGISTRY[predicate] = StoAtomInfo(
            predicate=predicate,
            evaluator=fn,
            nl_keywords=tuple(nl_keywords),
            required_args=required_args,
            default_context_scope=default_context_scope,
            default_output_type=default_output_type,
            description=description,
        )
        return fn

    return deco


_bootstrap_attempted = False


def _bootstrap_once() -> None:
    """Lazy one-shot import of ``sponsio.patterns.sto_catalog`` so its
    ``@register_sto_atom`` decorators fire.

    We avoid doing this in ``sponsio.patterns.__init__`` because
    ``sto_catalog`` imports from ``sponsio.runtime``, which in turn
    imports from ``sponsio.models``, which imports from
    ``sponsio.patterns.library`` — making eager import circular. The
    bootstrap triggers on the first access call once all modules are
    settled.
    """
    global _bootstrap_attempted
    if _bootstrap_attempted:
        return
    _bootstrap_attempted = True
    try:
        import sponsio.patterns.sto_catalog  # noqa: F401
    except ImportError:
        pass


def resolve_sto_evaluator(predicate: str) -> StoAtomEvaluator:
    """Look up the evaluator for a sto atom predicate.

    Lazily bootstraps ``sto_catalog`` on the first call so built-in
    atom-registered evaluators (``injection_free``, future additions)
    are available without users needing to import the catalog module.

    Raises:
        KeyError: If no evaluator is registered for ``predicate``.
    """
    if predicate not in _REGISTRY:
        _bootstrap_once()
    if predicate not in _REGISTRY:
        raise KeyError(
            f"no sto evaluator registered for {predicate!r}. "
            f"Available: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[predicate].evaluator


def get_sto_atom_info(predicate: str) -> StoAtomInfo:
    """Return the full registry entry for ``predicate``.

    Unlike :func:`resolve_sto_evaluator` (which returns only the
    evaluator), this also exposes ``nl_keywords`` / ``required_args`` /
    ``default_context_scope`` — used by the NL parser and LLM
    extractor to auto-wire NL → atom routing.
    """
    if predicate not in _REGISTRY:
        _bootstrap_once()
    if predicate not in _REGISTRY:
        raise KeyError(
            f"no sto evaluator registered for {predicate!r}. "
            f"Available: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[predicate]


def list_sto_atoms() -> list[str]:
    """Return the sorted list of registered sto atom predicates."""
    _bootstrap_once()
    return sorted(_REGISTRY)


def list_sto_atom_infos() -> list[StoAtomInfo]:
    """Return every registered atom's metadata, sorted by predicate.

    Intended for the NL parser and LLM extractor: both iterate this
    list to auto-generate keyword rules and prompt content so that
    adding a new atom requires only one decorator call.
    """
    _bootstrap_once()
    return [_REGISTRY[name] for name in sorted(_REGISTRY)]


def _clear_for_test() -> None:
    """Clear the registry — only for test fixtures.

    Tests that register/unregister custom predicates should use this
    in a fixture tear-down to avoid leaking into other tests.

    Also re-runs ``sto_catalog``'s registration decorators (via
    ``importlib.reload``) so built-in atoms like ``injection_free``
    are re-registered for subsequent tests. ``reload`` mutates the
    existing module object in place, preserving external references
    to symbols like :func:`set_default_judge`.
    """
    import sys

    _REGISTRY.clear()
    mod = sys.modules.get("sponsio.patterns.sto_catalog")
    if mod is not None:
        import importlib

        importlib.reload(mod)


# Keep ``field`` imported so future AtomInfo evolutions can add default
# factory fields without re-adding the import.
_ = field
