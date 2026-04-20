"""Micro-benchmark for the Verifier / grounding / eval pipeline.

Measures:
  1. Full grounding + evaluation per check_action (current path).
  2. A simulated session of N actions against K contracts.

Run::

    python scripts/bench_verifier.py
"""

from __future__ import annotations

import time
from sponsio.integrations.base import BaseGuard


def _rate_limit_contracts(n_contracts: int) -> list[str]:
    return [f"tool `tool_{i}` at most {10**6} times" for i in range(n_contracts)]


def _must_precede_contracts(n_contracts: int) -> list[str]:
    # Always satisfied (prefix_i always precedes tool_i), so the formula
    # stays True and must be re-evaluated every turn.
    return [
        f"tool `prefix_{i}` must precede `tool_{i}`" for i in range(n_contracts)
    ]


def simulate_session(
    n_actions: int, n_contracts: int, contract_factory
) -> float:
    contracts = contract_factory(n_contracts)
    guard = BaseGuard(agent_id="bench", contracts=contracts, verbose=False)

    # Warm up: satisfy any must_precede preconditions.
    for i in range(n_contracts):
        guard.guard_before(f"prefix_{i}")
    # Baseline warm-up call.
    guard.guard_before("tool_0")
    guard.reset()

    # Re-seed preconditions after reset so must_precede stays satisfied.
    for i in range(n_contracts):
        guard.guard_before(f"prefix_{i}")

    start = time.perf_counter()
    for i in range(n_actions):
        tool = f"tool_{i % n_contracts}"
        guard.guard_before(tool)
    return time.perf_counter() - start


def main() -> None:
    for label, factory in [
        ("rate_limit (G-rooted, cacheable)", _rate_limit_contracts),
        ("must_precede (U-rooted, fall-through)", _must_precede_contracts),
    ]:
        print(f"\n=== {label} ===")
        print(f"{'n_actions':>10} {'n_contracts':>12} {'seconds':>10} {'per_call_ms':>14}")
        print("-" * 50)
        for n_actions in [50, 100, 200, 500]:
            for n_contracts in [1, 5, 10]:
                sec = simulate_session(n_actions, n_contracts, factory)
                per_call_ms = (sec / n_actions) * 1000
                print(
                    f"{n_actions:>10} {n_contracts:>12} {sec:>10.3f} {per_call_ms:>14.3f}"
                )


if __name__ == "__main__":
    main()
