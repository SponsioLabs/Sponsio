"""Shared formatting helpers for demos.

All demos import from here to keep output style consistent.
"""

import json
import os
import random
import sys
import time
import urllib.request

# -- ANSI colors --------------------------------------------------------------

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
MAGENTA = "\033[95m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

DIVIDER = "=" * 60


# -- Realistic LLM simulation ------------------------------------------------

# Set DEMO_FAST=1 to skip delays (for testing)
_FAST = os.environ.get("DEMO_FAST", "0") == "1"


def _sleep(seconds: float) -> None:
    """Sleep unless DEMO_FAST is set."""
    if not _FAST:
        time.sleep(seconds)


def thinking(label: str = "Thinking", duration: float = 1.5) -> None:
    """Show a 'thinking...' spinner to simulate LLM latency."""
    if _FAST:
        return
    frames = [".", "..", "..."]
    end_time = time.time() + duration
    i = 0
    while time.time() < end_time:
        sys.stderr.write(f"\r  {DIM}{label}{frames[i % len(frames)]}{RESET}   ")
        sys.stderr.flush()
        time.sleep(0.4)
        i += 1
    sys.stderr.write("\r" + " " * 40 + "\r")
    sys.stderr.flush()


def typewrite(text: str, prefix: str = "", speed: float = 0.03) -> None:
    """Print text character by character to simulate LLM streaming output."""
    if _FAST:
        print(f"{prefix}{text}")
        return
    sys.stdout.write(prefix)
    sys.stdout.flush()
    for ch in text:
        sys.stdout.write(ch)
        sys.stdout.flush()
        # Vary speed slightly for realism
        time.sleep(speed * random.uniform(0.5, 1.5))
    sys.stdout.write("\n")
    sys.stdout.flush()


def pause(seconds: float = 0.8) -> None:
    """Pause between demo steps (shorter than thinking)."""
    _sleep(seconds)


# -- Printers -----------------------------------------------------------------


def print_header(text: str, color: str = BLUE):
    print(f"\n{DIVIDER}")
    print(f"{color}{BOLD}{text}{RESET}")
    print(f"{DIVIDER}\n")


def print_tool_call(name: str, args: dict):
    print(f"  {YELLOW}\U0001f527 Tool call:{RESET} {name}({json.dumps(args)})")


def print_result(text: str):
    print(f"  {GREEN}\u2192 {text}{RESET}")


def print_violation(constraint: str, tool: str):
    print(f"  {RED}\U0001f6e1\ufe0f  VIOLATION: {constraint}{RESET}")
    print(f"  {RED}   Tool '{tool}' was BLOCKED{RESET}")


def print_agent(text: str):
    print(f"  {BLUE}\U0001f916 Agent:{RESET} {text}")


def print_step(text: str):
    print(f"  {DIM}{text}{RESET}")


def print_soft_violation(constraint: str, score: float, threshold: float):
    print(f"  {MAGENTA}\U0001f50d SOFT VIOLATION: {constraint}{RESET}")
    print(f"  {MAGENTA}   Score: {score:.2f} < threshold: {threshold:.2f}{RESET}")


def print_feedback(text: str):
    print(f"  {YELLOW}\U0001f4ac FEEDBACK: {text}{RESET}")


def print_retry(attempt: int, max_retries: int):
    print(
        f"  {YELLOW}\U0001f504 RETRY ({attempt}/{max_retries}): agent regenerating with feedback...{RESET}"
    )


def print_contracts(hard: list[tuple[str, str, str]], sto: list[tuple[str, str, str]]):
    """Print contract summary.

    Args:
        hard: List of (description, pattern, pipeline) tuples.
        sto: List of (description, evaluator_info, pipeline) tuples.
    """
    print_header("CONTRACTS", BLUE)
    print(f"  {BOLD}Det constraints{RESET} (binary \u2014 block or allow):")
    print()
    for i, (desc, pattern, pipeline) in enumerate(hard, 1):
        print(f'  {i}. "{desc}"')
        print(f"     \u2192 {pattern}")
        print(f"     \u2192 {pipeline}")
        print()
    print(
        f"  {BOLD}Sto constraints{RESET} (scored \u2014 evaluate + feedback + retry):"
    )
    print()
    for i, (desc, evaluator_info, pipeline) in enumerate(sto, len(hard) + 1):
        print(f'  {i}. "{desc}"')
        print(f"     \u2192 {evaluator_info}")
        print(f"     \u2192 {pipeline}")


def print_summary(hard_lines: list[str], soft_lines: list[str]):
    """Print demo summary."""
    print_header("SUMMARY", BLUE)
    print(f"  {BOLD}Det constraints:{RESET}")
    for line in hard_lines:
        print(f"    {line}")
    print()
    print(f"  {BOLD}Sto constraints:{RESET}")
    for line in soft_lines:
        print(f"    {line}")


def print_banner(title: str, subtitle: str, mode: str, scenario: str):
    """Print demo opening banner."""
    print(f"\n{BOLD}{DIVIDER}{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{BOLD}{DIVIDER}{RESET}")
    print(f"\n  Mode:     {BOLD}{mode}{RESET}")
    print(f"  Scenario: {scenario}\n")


def print_footer():
    print(f"\n{DIVIDER}")
    print(f"{BOLD}  Demo complete. Star us: github.com/SponsioLabs/Sponsio{RESET}")
    print(f"{DIVIDER}\n")


# -- Span tree output ---------------------------------------------------------


def print_span_tree(guard, label: str = "CONTRACT CHECK TRACE"):
    """Print the structured span tree from a guard's check history.

    Args:
        guard: A BaseGuard (or subclass) instance with check history.
        label: Header label for the span tree section.
    """
    output = guard.render_checks(colorize=True)
    if not output:
        return
    print()
    print(f"  {DIM}{'─' * 50}{RESET}")
    print(f"  {BOLD}{label}{RESET}")
    print(f"  {DIM}{'─' * 50}{RESET}")
    for line in output.split("\n"):
        print(f"  {line}")
    print()


# -- LangGraph helpers --------------------------------------------------------


def extract_tool_calls_from_messages(messages):
    """Extract tool call info from LangGraph message history for display."""
    tool_calls = []
    for msg in messages:
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append(
                    {
                        "name": tc["name"],
                        "args": tc["args"],
                    }
                )
        if (
            hasattr(msg, "name")
            and hasattr(msg, "content")
            and msg.__class__.__name__ == "ToolMessage"
        ):
            if tool_calls and tool_calls[-1].get("result") is None:
                tool_calls[-1]["result"] = msg.content
                tool_calls[-1]["status"] = getattr(msg, "status", "success")
    return tool_calls


def print_llm_run(messages):
    """Pretty-print what the LLM did from its message history."""
    tool_calls = extract_tool_calls_from_messages(messages)

    for tc in tool_calls:
        print_tool_call(tc["name"], tc["args"])
        status = tc.get("status", "success")
        result = tc.get("result", "")
        if status == "error" or "BLOCKED" in str(result):
            print(f"  {RED}\U0001f6e1\ufe0f  BLOCKED by sponsio{RESET}")
            first_line = str(result).split("\n")[0][:100]
            print(f"  {RED}   {first_line}{RESET}")
        else:
            print_result(str(result)[:120])
        print()

    for msg in reversed(messages):
        if (
            msg.__class__.__name__ == "AIMessage"
            and msg.content
            and not getattr(msg, "tool_calls", None)
        ):
            print_agent(str(msg.content)[:200])
            break


def build_gemini_graph(tools, system_prompt, model_name="gemini-2.0-flash"):
    """Build a LangGraph react agent with Gemini. Returns the graph."""
    import sys
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langgraph.prebuilt import create_react_agent

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print(
            f"\n  {RED}{BOLD}ERROR: Set GOOGLE_API_KEY or GEMINI_API_KEY to use real LLM mode.{RESET}"
        )
        print(f"  {DIM}Tip: USE_MOCK=1 to run without an API key{RESET}\n")
        sys.exit(1)

    llm = ChatGoogleGenerativeAI(
        model=model_name, temperature=0.0, google_api_key=api_key
    )
    return create_react_agent(llm, tools, prompt=system_prompt)


# -- Dashboard integration ---------------------------------------------------

DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://localhost:8000")


def dashboard_seed(demo_id: str) -> None:
    """Seed a demo scenario on the dashboard (POST /api/demo/seed)."""
    try:
        data = json.dumps({"demo_id": demo_id}).encode()
        req = urllib.request.Request(
            f"{DASHBOARD_URL}/api/demo/seed",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass


def dashboard_reset() -> None:
    """Reset the dashboard monitor state (POST /api/monitor/reset)."""
    try:
        req = urllib.request.Request(
            f"{DASHBOARD_URL}/api/monitor/reset",
            data=b"",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass


def dashboard_push_span(guard) -> None:
    """Push the guard's last span tree to the dashboard."""
    span = guard.last_check_span
    if span is None:
        return
    try:
        data = json.dumps(span.to_dict()).encode()
        req = urllib.request.Request(
            f"{DASHBOARD_URL}/api/monitor/push-span",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass


def dashboard_push_all_spans(guard) -> None:
    """Push all span trees from the guard to the dashboard."""
    for span in guard.check_spans:
        try:
            data = json.dumps(span.to_dict()).encode()
            req = urllib.request.Request(
                f"{DASHBOARD_URL}/api/monitor/push-span",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=3)
        except Exception:
            pass
