"""End-to-end validation of the DFA backend against all Sponsio examples.

For each example agent, run the same scripted workload twice:

    1. Default recursive backend (ground truth)
    2. DFA backend forced via monkey-patch on ``RuntimeMonitor.__init__``

Compare total det/sto violations — any divergence is a DFA bug.

Usage::

    USE_MOCK=1 python scripts/validate_dfa_backend.py

Exits 0 on perfect agreement, 1 on any divergence.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


EXAMPLES = [
    "examples/integrations/python/vanilla_guard.py",
    "examples/integrations/python/langgraph_guard.py",
    "examples/integrations/python/openai_guard.py",
    "examples/integrations/python/crewai_guard.py",
    "examples/integrations/python/agents_sdk_guard.py",
    "examples/demo/demo_customer_service.py",
    "examples/demo/demo_coding_agent.py",
    "examples/demo/demo_mcp_leak.py",
    "examples/demo/demo_showcase.py",
    "examples/demo/demo_walkthrough.py",
]


WRAPPER_TEMPLATE = """\
# Auto-generated wrapper that swaps the verifier backend before running
# ``{example}`` as __main__.
import os, runpy, sys

_example = {example!r}
# Let the example find its sibling ``shared`` helper etc.
sys.path.insert(0, os.path.dirname(os.path.abspath(_example)))

from sponsio.runtime import monitor as _m

_orig_init = _m.RuntimeMonitor.__init__

def _patched_init(self, *args, **kwargs):
    _orig_init(self, *args, **kwargs)
    from sponsio.runtime.verifier import TraceVerifier
    self._verifier = TraceVerifier(backend={backend!r})

_m.RuntimeMonitor.__init__ = _patched_init

sys.argv = [_example]
runpy.run_path(_example, run_name="__main__")
"""


def _run(example: str, backend: str) -> dict:
    """Run one example with the given backend forced, return a summary dict."""
    env = os.environ.copy()
    env["USE_MOCK"] = "1"
    env["NO_DASHBOARD"] = "1"

    wrapper = WRAPPER_TEMPLATE.format(example=example, backend=backend)
    result = subprocess.run(
        [sys.executable, "-c", wrapper],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )

    # Sponsio's session summary (TerminalReporter) writes to *stderr*;
    # the demo examples write their narrative to stdout. Parse both.
    combined = (result.stdout or "") + "\n" + (result.stderr or "")

    det_violations = 0
    sto_violations = 0
    blocked_tools: list[str] = []
    for line in combined.splitlines():
        # Strip ANSI escape sequences (TerminalReporter colorizes)
        stripped = _strip_ansi(line).strip()
        if "Det violations:" in stripped:
            try:
                for p in stripped.split("|"):
                    if "Det violations" in p:
                        det_violations = int(p.split(":")[1].strip())
                    if "Sto violations" in p:
                        sto_violations = int(p.split(":")[1].strip())
            except (ValueError, IndexError):
                pass
        if "BLOCKED" in stripped and ":" in stripped:
            head = stripped.split(":", 1)[0]
            tool = head.replace("✗", "").strip()
            if tool and "BLOCKED" not in tool and len(tool) < 60:
                blocked_tools.append(tool)

    return {
        "returncode": result.returncode,
        "det_violations": det_violations,
        "sto_violations": sto_violations,
        "blocked_tools": sorted(blocked_tools),
        "stderr_tail": "\n".join(result.stderr.splitlines()[-10:])
        if result.returncode != 0
        else "",
    }


def main() -> int:
    repo_root = Path(__file__).parent.parent
    os.chdir(repo_root)

    divergences = []
    print(f"{'Example':<48} {'rec':>10} {'dfa':>10}   Status")
    print("-" * 84)

    for example in EXAMPLES:
        if not (repo_root / example).exists():
            print(f"{example:<48} missing")
            continue

        rec = _run(example, "recursive")
        dfa = _run(example, "dfa")

        rec_str = f"{rec['det_violations']}d/{rec['sto_violations']}s"
        dfa_str = f"{dfa['det_violations']}d/{dfa['sto_violations']}s"

        match = (
            rec["returncode"] == dfa["returncode"] == 0
            and rec["det_violations"] == dfa["det_violations"]
            and rec["sto_violations"] == dfa["sto_violations"]
            and rec["blocked_tools"] == dfa["blocked_tools"]
        )
        status = "✓ match" if match else "✗ DIVERGE"
        print(f"{example:<48} {rec_str:>10} {dfa_str:>10}   {status}")

        if not match:
            divergences.append((example, rec, dfa))

    print()
    if divergences:
        print(f"✗ {len(divergences)} divergence(s):")
        for example, rec, dfa in divergences:
            print(f"\n  {example}")
            print(
                f"    rec: rc={rec['returncode']} det={rec['det_violations']} "
                f"sto={rec['sto_violations']} blocked={rec['blocked_tools']}"
            )
            print(
                f"    dfa: rc={dfa['returncode']} det={dfa['det_violations']} "
                f"sto={dfa['sto_violations']} blocked={dfa['blocked_tools']}"
            )
            if rec["stderr_tail"]:
                print(f"    rec stderr:\n{rec['stderr_tail']}")
            if dfa["stderr_tail"]:
                print(f"    dfa stderr:\n{dfa['stderr_tail']}")
        return 1

    print(f"✓ All {len(EXAMPLES)} examples agree between recursive and DFA backends.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
