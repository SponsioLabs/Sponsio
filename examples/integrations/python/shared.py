"""Shared utilities for Sponsio integration examples."""

from __future__ import annotations

import os
import sys

# Ensure sponsio is importable from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
MAGENTA = "\033[95m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

USE_MOCK = os.environ.get("USE_MOCK", "1") == "1"


def banner(title: str, integration: str, contracts: list[str]):
    """Print a formatted banner at the start of each example."""
    print(f"\n{'=' * 60}")
    print(f"{BOLD}{MAGENTA}  Sponsio Integration Example{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{DIM}  Integration: {integration}{RESET}")
    print(f"{'=' * 60}")
    print(f"\n{YELLOW}Contracts enforced:{RESET}")
    for c in contracts:
        print(f"  \u2022 {c}")
    print()


def print_action(action: str, detail: str = ""):
    """Print a tool call action."""
    print(f"  {BLUE}\u25b6 {action}{RESET}  {DIM}{detail}{RESET}")


def print_ok(msg: str):
    print(f"  {GREEN}\u2713 {msg}{RESET}")


def print_blocked(msg: str):
    print(f"  {RED}\u2717 BLOCKED: {msg}{RESET}")


def print_section(title: str):
    print(f"\n{BOLD}\u2500\u2500 {title} \u2500\u2500{RESET}\n")
