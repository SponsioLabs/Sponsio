"""Developer-general demo (Claude Agent SDK) — onboard-flow variant.

Same scenario as `examples/demo/demo_coding_cleanup.py`: cleanup agent
reads `.env`, sweeps `.env`/`.git/`, commits, force-pushes.

The difference from the original demo: contracts live in `sponsio.yaml`
next to this file, exactly as `sponsio onboard coding_agent.py` would
have written them. The only Sponsio-specific code in this file is the
two-line patch marked below.

Usage:
    python examples/demo/onboard/cleanup_claude_agent/coding_agent.py             # with Sponsio
    python examples/demo/onboard/cleanup_claude_agent/coding_agent.py --no-guard  # breach
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))


BOLD = "\033[1m"
RED = "\033[91m"
GREEN = "\033[92m"
DIM = "\033[2m"
RESET = "\033[0m"


# Recorded trajectory — Claude Agent SDK's built-in tool is `Bash`, so
# every shell step is one Bash tool call.
TRAJECTORY = [
    ("git status --porcelain", "read"),
    ("find . -type f -size +10M -not -path './.git/*'", "read"),
    ("ls -la", "read"),
    ("du -sh node_modules dist .next 2>/dev/null", "read"),
    ("cat .gitignore", "read"),
    ("cat .env .env.production", "read_secret"),
    ("rm -rf node_modules dist .next build", "cleanup"),
    ("rm -f .env .env.local .env.production prod.env", "destructive"),
    ("rm -rf .git/objects/pack", "destructive"),
    ("rm -rf logs/ tmp/ .cache/", "cleanup"),
    ("git add -A && git commit -m 'chore: clean up'", "git"),
    ("git push --force origin main", "destructive"),
]


def slow_print(line: str, delay: float = 0.35) -> None:
    print(line, flush=True)
    time.sleep(delay)


def run_without_guard() -> None:
    slow_print(f"{BOLD}== Coding Agent — cleanup, no Sponsio =={RESET}")
    slow_print(f"{DIM}User: 'clean up unused files and large artifacts'{RESET}\n")
    for cmd, stage in TRAJECTORY:
        shown = cmd[:110] + ("..." if len(cmd) > 110 else "")
        slow_print(f"  {DIM}$ {shown}{RESET}")
        if stage == "read_secret":
            slow_print(f"    {RED}→ .env contents loaded into context.{RESET}")
        elif stage == "destructive" and ".env" in cmd:
            slow_print(f"    {RED}→ 4 secret files deleted. API keys gone.{RESET}")
        elif stage == "destructive" and ".git" in cmd:
            slow_print(f"    {RED}→ Git object pack removed. History corrupted.{RESET}")
        elif stage == "destructive" and "--force" in cmd:
            slow_print(
                f"    {RED}→ Force-pushed to main. Teammate commits gone.{RESET}"
            )
        elif stage == "git" and "commit" in cmd:
            slow_print(
                f"    {RED}→ Secrets from .env committed into git history.{RESET}"
            )
    slow_print(
        f"\n{RED}{BOLD}✗ Secrets leaked, `.git/` corrupted, teammates lose work.{RESET}"
    )


async def _no_check(_input, _ctx, _msg):
    """Naive PreToolUse hook — never denies anything.

    Used as the default ``pre_tool_hook`` so the trajectory can run
    end-to-end with the Sponsio patch stripped (recording's hidden
    setup step removes the patch block before the gif starts; the
    visible sed re-applies it)."""
    return None


def run_with_guard() -> None:
    slow_print(f"{BOLD}== Coding Agent — cleanup =={RESET}")

    # Default: tools fire raw (the naive hook never denies).  The
    # ``sponsio onboard`` block below rebinds ``pre_tool_hook`` to
    # Sponsio's real PreToolUse callback so contract violations land
    # ``permissionDecision: "deny"`` and short-circuit the trajectory.
    pre_tool_hook = _no_check

    # ─── sponsio onboard patch ─────────────────────────────────────
    # Three lines from ``sponsio onboard <path>``'s wrap snippet for
    # Claude Agent SDK projects.  In a real app you'd hand
    # ``guard.hooks()`` to ``ClaudeAgentOptions(hooks=...)`` and call
    # ``query(...)``; this demo invokes the PreToolUse hook directly
    # against a recorded trajectory so the gif runs without spending
    # an LLM token.
    from sponsio.claude_agent import Sponsio

    guard = Sponsio(config=str(Path(__file__).parent / "sponsio.yaml"), agent_id="agent")
    pre_tool_hook = guard.hooks()["PreToolUse"][0].hooks[0]
    # ─── /sponsio onboard patch ────────────────────────────────────

    blocked = False

    async def drive() -> None:
        nonlocal blocked
        for cmd, _stage in TRAJECTORY:
            shown = cmd[:110] + ("..." if len(cmd) > 110 else "")
            slow_print(f"  {DIM}$ {shown}{RESET}")
            result = await pre_tool_hook(
                {"tool_name": "Bash", "tool_input": {"command": cmd}},
                None,
                None,
            )
            # Sponsio's hook returns ``permissionDecision: deny`` on
            # contract violations; the SDK uses that to stop the tool
            # from running.  We mimic the same short-circuit here.
            decision = (
                result.get("hookSpecificOutput", {}).get("permissionDecision")
                if isinstance(result, dict)
                else None
            )
            if decision == "deny":
                blocked = True
                break

    asyncio.run(drive())

    if blocked:
        slow_print(
            f"\n{GREEN}{BOLD}✓ Outcome: secrets, git history, "
            f"and teammate commits all intact.{RESET}"
        )
    else:
        slow_print(
            f"\n{RED}{BOLD}✗ Sponsio did not block — full breach trajectory ran. "
            f"Check that the wrap patch is in place and `mode: enforce` "
            f"is set in sponsio.yaml.{RESET}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-guard", action="store_true")
    parser.add_argument("--fast", action="store_true")
    args = parser.parse_args()

    if args.fast:
        global slow_print

        def slow_print(line: str, delay: float = 0.0) -> None:  # noqa: F811
            print(line, flush=True)

    if args.no_guard:
        run_without_guard()
    else:
        run_with_guard()


if __name__ == "__main__":
    main()
