from __future__ import annotations

from pathlib import Path

from sponsio.config import config_to_system, load_config


REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG = (
    REPO_ROOT
    / "examples"
    / "integrations"
    / "openclaw-social-account-actions"
    / "sponsio.yaml"
)


def test_openclaw_social_account_example_loads() -> None:
    config = load_config(EXAMPLE_CONFIG)

    assert config.runtime.mode == "observe"
    assert {tool.name for tool in config.tools} >= {
        "confirm_reconfirmed",
        "explore",
        "tweetclaw",
    }

    agent = config.agents["openclaw_social_account_agent"]
    descriptions = {contract.desc for contract in agent.contracts}

    assert (
        "TweetClaw live X/Twitter actions require a fresh operator approval"
        in descriptions
    )
    assert "Use TweetClaw explore before live TweetClaw execution" in descriptions
    assert "Bound TweetClaw calls in one OpenClaw session" in descriptions


def test_openclaw_social_account_example_compiles_to_system() -> None:
    system = config_to_system(load_config(EXAMPLE_CONFIG))

    compiled = {
        (contract.guarantee.pattern_name, contract.guarantee.desc)
        for contract in system.contracts
    }

    assert (
        "ltl",
        "G(called(tweetclaw) -> count(confirm_reconfirmed) >= count(tweetclaw))",
    ) in compiled
    assert ("must_precede", "explore must precede tweetclaw") in compiled
    assert ("rate_limit", "tweetclaw limited to 10 invocations") in compiled
    assert len(system.contracts) > 3
