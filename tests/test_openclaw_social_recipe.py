"""Validates the OpenClaw social-account actions example recipe.

Recipe: ``examples/recipes/openclaw_social_actions/sponsio.yaml`` — a small
deterministic config that keeps ``explore`` open while gating the live
``tweetclaw`` action behind operator re-confirmation and a per-session cap.

These tests pin both halves of the issue: the config loads cleanly, and the two
declared contracts actually enforce what the recipe claims (replayed over
synthetic traces).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sponsio.config import load_config
from sponsio.discovery.trace_replay import replay_formula
from sponsio.models.trace import Event, Trace
from sponsio.patterns.library import must_precede, rate_limit

RECIPE = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "recipes"
    / "openclaw_social_actions"
    / "sponsio.yaml"
)


def _trace(*tools: str) -> Trace:
    return Trace(
        events=[
            Event(ts=i, agent="social_bot", event_type="tool_call", tool=t)
            for i, t in enumerate(tools)
        ]
    )


@pytest.fixture(scope="module")
def cfg():
    return load_config(RECIPE)


def test_recipe_loads_and_declares_expected_surface(cfg):
    """Config loader accepts the recipe with the expected tools + agent."""
    assert set(t.name for t in cfg.tools) == {
        "explore",
        "draft_tweet",
        "tweetclaw",
        "confirm_reconfirmed",
    }
    assert list(cfg.agents) == ["social_bot"]


def test_recipe_contracts_are_the_two_deterministic_patterns(cfg):
    """The agent declares exactly the confirm-gate and the rate cap."""
    guarantees = [c.guarantee for c in cfg.agents["social_bot"].contracts]
    declared = {(g.pattern, tuple(g.args)) for g in guarantees}
    assert declared == {
        ("must_precede", ("confirm_reconfirmed", "tweetclaw")),
        ("rate_limit", ("tweetclaw", 3)),
    }


def test_explore_and_draft_are_ungated(cfg):
    """Discovery/drafting must carry no contract — only the live action is gated."""
    referenced = {
        arg
        for c in cfg.agents["social_bot"].contracts
        for arg in c.guarantee.args
        if isinstance(arg, str)
    }
    assert "explore" not in referenced
    assert "draft_tweet" not in referenced


def test_confirm_gate_blocks_unconfirmed_post(cfg):
    """`must_precede(confirm_reconfirmed, tweetclaw)` enforces the gate."""
    entry = next(
        c.guarantee
        for c in cfg.agents["social_bot"].contracts
        if c.guarantee.pattern == "must_precede"
    )
    formula = must_precede(*entry.args)

    # A live post with no prior confirmation fails the contract.
    assert (
        replay_formula(
            formula, [_trace("explore", "draft_tweet", "tweetclaw")]
        ).fail_count
        == 1
    )
    # A confirmed post passes; planning beforehand is fine.
    assert (
        replay_formula(
            formula, [_trace("explore", "confirm_reconfirmed", "tweetclaw")]
        ).pass_count
        == 1
    )
    # Never posting is vacuously fine.
    assert replay_formula(formula, [_trace("explore", "draft_tweet")]).pass_count == 1


def test_rate_limit_caps_live_posts(cfg):
    """`rate_limit(tweetclaw, 3)` caps live posts per session."""
    entry = next(
        c.guarantee
        for c in cfg.agents["social_bot"].contracts
        if c.guarantee.pattern == "rate_limit"
    )
    formula = rate_limit(*entry.args)

    confirmed = ["confirm_reconfirmed"]
    assert (
        replay_formula(formula, [_trace(*(confirmed + ["tweetclaw"] * 3))]).pass_count
        == 1
    )
    assert (
        replay_formula(formula, [_trace(*(confirmed + ["tweetclaw"] * 4))]).fail_count
        == 1
    )
