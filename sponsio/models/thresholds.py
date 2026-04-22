"""Cost-based threshold helpers for stochastic contracts.

Turns operational costs (``c_FP``, ``c_FN``) or risk profile names into
concrete ``(alpha, beta)`` pairs for :class:`sponsio.models.contract.Contract`.

Three user-facing ways to specify thresholds:

1. **Explicit**: ``alpha=0.7, beta=0.95``
2. **Risk profile**: ``risk_profile="cautious"`` → expands via
   :data:`RISK_PROFILES`
3. **Cost-based**: ``costs={"fp": 1, "fn": 20}`` → ``β = c_fn / (c_fp + c_fn)``;
   α falls back to the per-category default in :data:`ATOM_CATEGORY_ALPHAS`

See ``docs/cost-based-thresholds.md`` for the derivation and audience
considerations (why costs are more natural than raw probabilities).
"""

from __future__ import annotations


def beta_from_costs(c_fp: float, c_fn: float) -> float:
    """Bayes-optimal decision threshold.

    ``β* = c_FN / (c_FP + c_FN)`` — block when expected cost of blocking
    is less than expected cost of allowing (see doc §3).

    Raises:
        ValueError: If either cost is non-positive.
    """
    if c_fp <= 0 or c_fn <= 0:
        raise ValueError(f"costs must be positive, got c_fp={c_fp!r}, c_fn={c_fn!r}")
    return c_fn / (c_fp + c_fn)


def alpha_from_costs(c_missed_trigger: float, c_false_trigger: float) -> float:
    """Bayes-optimal assumption trigger threshold.

    ``α* = c_MT / (c_MT + c_FT)`` — trigger when assumption's posterior
    probability justifies spending enforcement-side compute (see doc §4).

    Raises:
        ValueError: If either cost is non-positive.
    """
    if c_missed_trigger <= 0 or c_false_trigger <= 0:
        raise ValueError(
            f"costs must be positive, got "
            f"c_missed_trigger={c_missed_trigger!r}, c_false_trigger={c_false_trigger!r}"
        )
    return c_missed_trigger / (c_missed_trigger + c_false_trigger)


# From docs/cost-based-thresholds.md §7
RISK_PROFILES: dict[str, dict[str, float]] = {
    "permissive": {"alpha": 0.5, "beta": 0.5},
    "balanced": {"alpha": 0.6, "beta": 0.85},
    "cautious": {"alpha": 0.7, "beta": 0.95},
    "strict_compliance": {"alpha": 0.6, "beta": 0.999},
}


# From docs/cost-based-thresholds.md §5 — defaults for α when cost-based
# β is specified but α is not. Keyed on atom category (roughly matches
# sto atom predicate naming in sto_catalog.py).
ATOM_CATEGORY_ALPHAS: dict[str, float] = {
    "injection": 0.6,
    "jailbreak": 0.6,
    "pii": 0.7,
    "semantic_pii": 0.7,
    "scope_violation": 0.8,
    "authority": 0.8,
    "relevance": 0.5,
    "tone": 0.5,
    "faithfulness": 0.7,
    "hallucination": 0.7,
    "toxic": 0.5,
    "harmful": 0.5,
}


_DEFAULT_CATEGORY_ALPHA = 0.7  # fallback when category unknown


def resolve_thresholds(
    *,
    alpha: float | None = None,
    beta: float | None = None,
    risk_profile: str | None = None,
    costs: dict[str, float] | None = None,
    atom_category: str | None = None,
) -> tuple[float, float]:
    """Resolve ``(alpha, beta)`` from whichever spec the caller provided.

    Exactly one spec may be used:

    * explicit ``alpha`` and/or ``beta`` (unset values default to 1.0)
    * ``risk_profile`` — looked up in :data:`RISK_PROFILES`
    * ``costs={"fp": ..., "fn": ...}`` — β derived via :func:`beta_from_costs`;
      α from :data:`ATOM_CATEGORY_ALPHAS` using ``atom_category``

    Args:
        alpha: Explicit assumption trigger threshold.
        beta: Explicit enforcement satisfaction threshold.
        risk_profile: Name of a preset (``"permissive"``, ``"balanced"``,
            ``"cautious"``, ``"strict_compliance"``).
        costs: Dict with keys ``"fp"`` and ``"fn"``.
        atom_category: Used with ``costs=`` to pick an α default.

    Returns:
        ``(alpha, beta)`` each in [0, 1].

    Raises:
        ValueError: On conflicting specs, unknown profile, malformed costs.
    """
    explicit = alpha is not None or beta is not None
    using_profile = risk_profile is not None
    using_costs = costs is not None

    if sum([explicit, using_profile, using_costs]) > 1:
        raise ValueError(
            "threshold spec is ambiguous: provide exactly one of "
            "(explicit alpha/beta), risk_profile, or costs — "
            f"got alpha={alpha!r}, beta={beta!r}, "
            f"risk_profile={risk_profile!r}, costs={costs!r}"
        )

    if using_profile:
        if risk_profile not in RISK_PROFILES:
            raise ValueError(
                f"unknown risk_profile {risk_profile!r}. "
                f"Available: {sorted(RISK_PROFILES)}"
            )
        p = RISK_PROFILES[risk_profile]
        return p["alpha"], p["beta"]

    if using_costs:
        if "fp" not in costs or "fn" not in costs:
            raise ValueError(f"costs must have 'fp' and 'fn' keys, got {sorted(costs)}")
        resolved_beta = beta_from_costs(costs["fp"], costs["fn"])
        resolved_alpha = ATOM_CATEGORY_ALPHAS.get(
            atom_category or "", _DEFAULT_CATEGORY_ALPHA
        )
        return resolved_alpha, resolved_beta

    # Explicit (or nothing — both default to 1.0)
    return alpha if alpha is not None else 1.0, beta if beta is not None else 1.0
