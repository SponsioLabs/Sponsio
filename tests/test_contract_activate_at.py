"""Tests for the ``activate_at`` field on ``Contract``.

Two semantics are supported and these tests pin both:

* ``activate_at=None`` (default, *global*) — A and E each evaluated
  against the full trace from position 0.  If A becomes true anywhere,
  E must hold at every position from 0.  Suitable for invariants.

* ``activate_at="first_match"`` (*reactive*) — find the first position
  k where A's evidence holds; evaluate E from position k onward.
  Events before k are NOT subject to E.  Suitable for trigger-then-
  enforce safety contracts.

Coverage:

* §1 — semantic difference on the same trace
* §2 — validation: rejected shapes / invalid values
* §3 — vacuity when assumption never activates
* §4 — multi-assumption activation = max(per-assumption first-match)
* §5 — YAML round-trip via ``config_to_guard_kwargs``
"""

from __future__ import annotations

import textwrap

import pytest

from sponsio.formulas.formula import Atom, F, G, Not
from sponsio.integrations.base import BaseGuard
from sponsio.models.agent import Agent
from sponsio.models.contract import Contract
from sponsio.models.trace import Event, Trace
from sponsio.patterns.library import DetFormula
from sponsio.runtime.verifier import TraceVerifier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _det(formula, desc: str) -> DetFormula:
    return DetFormula(formula=formula, desc=desc, pattern_name="custom")


def _trace_of(*tools: str, agent: str = "test") -> Trace:
    """Build a Trace from a sequence of tool-call event names."""
    events = []
    for i, t in enumerate(tools):
        events.append(
            Event(
                ts=i,
                agent=agent,
                event_type="tool_call",
                tool=t,
                args={},
            )
        )
    return Trace(events=events)


def _verdict_for(contract: Contract, trace: Trace):
    """Evaluate a contract against a fixed trace via TraceVerifier."""
    v = TraceVerifier()
    v.sync(trace)
    return v.check_contract(contract)


# ---------------------------------------------------------------------------
# §1 — Semantic difference on the same trace
# ---------------------------------------------------------------------------


def test_global_default_flags_q_before_p_as_violation():
    """``activate_at=None``: F(P) → G(!Q) retroactively flags pre-P Q.

    This is the existing semantic.  Documented behaviour, not a bug,
    but the wrong choice for trigger-then-enforce safety contracts.
    """
    agent = Agent(id="t")
    contract = Contract(
        agent=agent,
        assumption=_det(F(Atom("called", "P")), "F(P)"),
        guarantee=_det(G(Not(Atom("called", "Q"))), "G(!Q)"),
        # activate_at=None  (default)
    )
    # Trace: Q happened before any P.
    trace = _trace_of("Q", "P")
    verdict = _verdict_for(contract, trace)
    assert verdict.assumption_holds is True, "F(P) should hold (P appears at pos 1)"
    # Global semantic: Q at pos 0 violates G(!Q) checked from pos 0.
    assert any(not e.holds for e in verdict.guarantees), (
        "global semantic should flag the pre-P Q as a violation"
    )


def test_reactive_first_match_does_not_flag_pre_activation_q():
    """``activate_at='first_match'``: same trace, Q-before-P does NOT trip.

    The reactive semantic activates at the first P (pos 1) and only
    checks ¬Q from pos 1 onward.  Q at pos 0 is before activation and
    is allowed.
    """
    agent = Agent(id="t")
    contract = Contract(
        agent=agent,
        assumption=_det(F(Atom("called", "P")), "F(P)"),
        guarantee=_det(G(Not(Atom("called", "Q"))), "G(!Q)"),
        activate_at="first_match",
    )
    trace = _trace_of("Q", "P")
    verdict = _verdict_for(contract, trace)
    assert verdict.assumption_holds is True
    assert all(e.holds for e in verdict.guarantees), (
        "reactive should NOT flag pre-activation Q"
    )


def test_reactive_first_match_flags_post_activation_q():
    """``activate_at='first_match'``: Q after P-activation IS a violation."""
    agent = Agent(id="t")
    contract = Contract(
        agent=agent,
        assumption=_det(F(Atom("called", "P")), "F(P)"),
        guarantee=_det(G(Not(Atom("called", "Q"))), "G(!Q)"),
        activate_at="first_match",
    )
    # Trace: P (activates), then Q (post-activation, illegal)
    trace = _trace_of("P", "Q")
    verdict = _verdict_for(contract, trace)
    assert verdict.assumption_holds is True
    assert any(not e.holds for e in verdict.guarantees), (
        "reactive should flag post-activation Q"
    )


def test_reactive_first_match_e2e_through_baseguard():
    """End-to-end via BaseGuard: trace [Q, P, Q] under reactive semantics.

    Expected:
      pos 0 Q: allow (no P yet)
      pos 1 P: allow (no Q after P yet; activates)
      pos 2 Q: DENY (post-activation)
    """
    agent = Agent(id="t")
    contract = Contract(
        agent=agent,
        assumption=_det(F(Atom("called", "P")), "F(P)"),
        guarantee=_det(G(Not(Atom("called", "Q"))), "G(!Q)"),
        activate_at="first_match",
    )
    g = BaseGuard(
        agent_id="t",
        contracts=[contract],
        verbose=False,
        verbosity=0,
        mode="enforce",
    )
    r1 = g.guard_before(tool_name="Q", args={})
    r2 = g.guard_before(tool_name="P", args={})
    r3 = g.guard_before(tool_name="Q", args={})
    assert r1.allowed is True
    assert r2.allowed is True
    assert r3.allowed is False


# ---------------------------------------------------------------------------
# §2 — Validation: invalid values, rejected shapes
# ---------------------------------------------------------------------------


def test_invalid_activate_at_value_rejected():
    agent = Agent(id="t")
    with pytest.raises(ValueError, match="activate_at must be one of"):
        Contract(
            agent=agent,
            assumption=_det(F(Atom("called", "P")), "F(P)"),
            guarantee=_det(G(Not(Atom("called", "Q"))), "G(!Q)"),
            activate_at="whatever",
        )


def test_first_match_without_assumption_rejected():
    agent = Agent(id="t")
    with pytest.raises(ValueError, match="requires a non-None assumption"):
        Contract(
            agent=agent,
            guarantee=_det(G(Not(Atom("called", "Q"))), "G(!Q)"),
            activate_at="first_match",
        )


def test_first_match_with_g_assumption_rejected():
    """G(φ) doesn't have a single 'first activation' position."""
    agent = Agent(id="t")
    with pytest.raises(ValueError, match="only supports F.*atomic"):
        Contract(
            agent=agent,
            assumption=_det(G(Atom("called", "P")), "G(P)"),
            guarantee=_det(G(Not(Atom("called", "Q"))), "G(!Q)"),
            activate_at="first_match",
        )


def test_first_match_with_atomic_assumption_works():
    """Atomic assumption is supported (activates when atom first holds)."""
    agent = Agent(id="t")
    contract = Contract(
        agent=agent,
        assumption=_det(Atom("called", "P"), "called(P)"),
        guarantee=_det(G(Not(Atom("called", "Q"))), "G(!Q)"),
        activate_at="first_match",
    )
    trace = _trace_of("Q", "P", "Q")  # Q before, P activates, Q after
    verdict = _verdict_for(contract, trace)
    # Atomic assumption checked at position 0.  Position 0 has tool=Q,
    # so called(P) is False at pos 0 → assumption doesn't hold globally,
    # BUT under first_match we look for the first position where the
    # atom holds: pos 1 has tool=P, atom holds.  Activation = 1.
    # Then E checked at pos 1 onward: trace[1:] = [P, Q] — Q at pos 2
    # violates G(!Q).  → enforcement violation expected.
    assert verdict.assumption_holds is True
    assert any(not e.holds for e in verdict.guarantees)


# ---------------------------------------------------------------------------
# §3 — Vacuity when assumption never activates
# ---------------------------------------------------------------------------


def test_assumption_never_activates_means_no_guarantee_violations():
    """If A's evidence never appears, no guarantee violations reported.

    Note on ``ContractVerdict.holds``: the verdict's ``holds`` property
    requires ``assumption_holds=True`` AND no violations.  When the
    assumption never activates, ``assumption_holds=False`` and
    ``holds=False``, but the *enforcement violations list is empty* —
    which is what matters for runtime blocking.  The runtime monitor
    triggers blocks only on enforcement violations, not on bare
    assumption non-activation.
    """
    agent = Agent(id="t")
    contract = Contract(
        agent=agent,
        assumption=_det(F(Atom("called", "P")), "F(P)"),
        guarantee=_det(G(Not(Atom("called", "Q"))), "G(!Q)"),
        activate_at="first_match",
    )
    trace = _trace_of("Q", "Q", "Q")  # only Q, no P
    verdict = _verdict_for(contract, trace)
    assert verdict.assumption_holds is False, "no P → assumption never activated"
    assert verdict.guarantees == [], "no enforcement evaluation when never activated"
    assert verdict.guarantee_violations == [], (
        "vacuous — no violations means runtime won't block"
    )


# ---------------------------------------------------------------------------
# §4 — Multi-assumption activation
# ---------------------------------------------------------------------------


def test_multi_assumption_activates_at_max_position():
    """Two assumptions A1=F(P1), A2=F(P2) → contract activates at max(k1, k2).

    With activate_at='first_match', E is evaluated from the position
    where ALL assumptions are satisfied — i.e. the latest first-match.
    """
    agent = Agent(id="t")
    contract = Contract(
        agent=agent,
        assumption=[
            _det(F(Atom("called", "P1")), "F(P1)"),
            _det(F(Atom("called", "P2")), "F(P2)"),
        ],
        guarantee=_det(G(Not(Atom("called", "Q"))), "G(!Q)"),
        activate_at="first_match",
    )
    # Trace: P1 (pos 0), Q (pos 1), P2 (pos 2), Q (pos 3)
    # Activation: max(0, 2) = 2
    # Q at pos 1 is BEFORE activation → not flagged
    # Q at pos 3 is AFTER activation → flagged
    trace = _trace_of("P1", "Q", "P2", "Q")
    verdict = _verdict_for(contract, trace)
    assert verdict.assumption_holds is True
    assert any(not e.holds for e in verdict.guarantees), (
        "post-activation Q should be flagged"
    )


def test_multi_assumption_one_never_activates_means_no_violations():
    """If any assumption never activates, no enforcement violations.

    Same vacuity property as the single-assumption case — runtime
    won't block, even though ``verdict.holds`` is False due to the
    failed assumption.
    """
    agent = Agent(id="t")
    contract = Contract(
        agent=agent,
        assumption=[
            _det(F(Atom("called", "P1")), "F(P1)"),
            _det(F(Atom("called", "P2_never")), "F(P2_never)"),
        ],
        guarantee=_det(G(Not(Atom("called", "Q"))), "G(!Q)"),
        activate_at="first_match",
    )
    trace = _trace_of("P1", "Q", "Q")  # P2_never never appears
    verdict = _verdict_for(contract, trace)
    assert verdict.assumption_holds is False
    assert verdict.guarantee_violations == [], (
        "one assumption never activated → no violations, no block"
    )


# ---------------------------------------------------------------------------
# §5 — YAML round-trip
# ---------------------------------------------------------------------------


def test_yaml_loads_activate_at(tmp_path):
    """YAML config → ContractEntry → Contract carries activate_at correctly."""
    from sponsio.config import config_to_guard_kwargs, load_config

    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(
        textwrap.dedent(
            """
            version: "1"
            agents:
              test:
                contracts:
                  - desc: "reactive rule"
                    activate_at: first_match
                    A:
                      ltl: 'F(called(P))'
                    G:
                      ltl: 'G(!called(Q))'
                  - desc: "global rule"
                    A:
                      ltl: 'F(called(P))'
                    G:
                      ltl: 'G(!called(Q))'
            """
        ).lstrip()
    )
    parsed = load_config(str(cfg_path))
    cfg = config_to_guard_kwargs(parsed, "test")
    contracts = cfg["contracts"]
    assert len(contracts) == 2
    by_desc = {c["desc"]: c for c in contracts}
    assert by_desc["reactive rule"].get("activate_at") == "first_match"
    assert "activate_at" not in by_desc["global rule"], (
        "default activate_at should not be serialized"
    )


def test_yaml_rejects_invalid_activate_at_value(tmp_path):
    from sponsio.config import ConfigError, load_config

    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(
        textwrap.dedent(
            """
            version: "1"
            agents:
              test:
                contracts:
                  - desc: "bad"
                    activate_at: nonsense
                    G:
                      ltl: 'G(!called(Q))'
            """
        ).lstrip()
    )
    with pytest.raises(ConfigError, match="unknown activate_at value"):
        load_config(str(cfg_path))


def test_yaml_e2e_first_match_through_baseguard(tmp_path):
    """Full path: YAML → BaseGuard → reactive enforcement."""
    from sponsio.config import config_to_guard_kwargs, load_config

    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(
        textwrap.dedent(
            """
            version: "1"
            agents:
              t:
                contracts:
                  - desc: "after P, no Q"
                    activate_at: first_match
                    A:
                      ltl: 'F(called(P))'
                    G:
                      ltl: 'G(!called(Q))'
            """
        ).lstrip()
    )
    parsed = load_config(str(cfg_path))
    cfg = config_to_guard_kwargs(parsed, "t")
    g = BaseGuard(
        agent_id="t",
        contracts=cfg["contracts"],
        verbose=False,
        verbosity=0,
        mode="enforce",
    )
    assert g.guard_before(tool_name="Q", args={}).allowed is True  # before P
    assert g.guard_before(tool_name="P", args={}).allowed is True  # activates
    assert g.guard_before(tool_name="Q", args={}).allowed is False  # post-activation
