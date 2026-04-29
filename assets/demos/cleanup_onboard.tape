# Cleanup — full onboard journey for a Claude Agent SDK project
# (onboard → drop in hand-authored contracts → flip enforce → run
# → BLOCKED).
#
# Demo dir: examples/demo/onboard/cleanup_claude_agent (coding agent
# under "clean up unused files" pressure that wants to delete
# secrets + force-push main).
#
# Why this tape has an extra "cp curated.yaml" step the others
# don't: Claude Agent SDK uses the SDK's native ``Bash`` tool — the
# project doesn't define ``@tool`` functions, so onboard's AST scan
# finds zero tools and the LLM extraction has nothing to mine.
# Real SDK users author Bash-shaped guardrails by hand; the
# demo's checked-in ``sponsio.curated.yaml`` stands in for that
# step so the gif can land a proper enforce-mode block.
#
# Run with:  vhs assets/demos/cleanup_onboard.tape
# Requires:  GOOGLE_API_KEY in shell env (sourced from .venv/bin/activate).

Output assets/demos/cleanup_onboard.gif

Set FontSize 14
Set Width 1200
Set Height 1000
Set Padding 20
Set Theme "Dracula"
Set TypingSpeed 30ms

Hide
Type "source .venv/bin/activate"
Enter
Type `export PS1='$ '`
Enter
Type "rm -f examples/demo/onboard/cleanup_claude_agent/sponsio.yaml examples/demo/onboard/cleanup_claude_agent/.sponsiorc"
Enter
Type "cp examples/demo/onboard/cleanup_claude_agent/coding_agent.py /tmp/sponsio_demo_orig.py"
Enter
Type `cat > /tmp/sponsio_wrap_snippet.py << 'EOF'`
Enter
Type "    # ─── sponsio onboard patch ─────────────────────────────────────"
Enter
Type "    from sponsio.claude_agent import Sponsio"
Enter
Type ""
Enter
Type `    guard = Sponsio(config=str(Path(__file__).parent / "sponsio.yaml"), agent_id="agent")`
Enter
Type `    pre_tool_hook = guard.hooks()["PreToolUse"][0].hooks[0]`
Enter
Type "    # ─── /sponsio onboard patch ────────────────────────────────────"
Enter
Type "EOF"
Enter
Type `sed -i '' '/─── sponsio onboard patch ─/,/─── \/sponsio onboard patch ─/d' examples/demo/onboard/cleanup_claude_agent/coding_agent.py`
Enter
Type "clear"
Enter
Show

# ─── Step 1: onboard ────────────────────────────────────────────────
Type "sponsio onboard examples/demo/onboard/cleanup_claude_agent --no-interactive"
Enter
Sleep 8s

# ─── Step 2: drop in the hand-authored contracts ────────────────────
# Onboard's contracts: section is empty (no ``@tool`` definitions in
# a Claude Agent SDK project for the LLM to mine).  Real SDK users
# author Bash-shaped rules by hand; we ship a ready-made set in
# ``sponsio.curated.yaml`` and copy it in here.  This is the visible
# stand-in for the "now write the rules you care about" step.
Type "cp examples/demo/onboard/cleanup_claude_agent/sponsio.curated.yaml examples/demo/onboard/cleanup_claude_agent/sponsio.yaml"
Enter

# ─── Step 3: apply the wrap snippet via sed ─────────────────────────
Type `sed -i '' '/^    pre_tool_hook = _no_check/r /tmp/sponsio_wrap_snippet.py' examples/demo/onboard/cleanup_claude_agent/coding_agent.py`
Enter

# ─── Step 4: flip yaml from observe to enforce ──────────────────────
Type "sed -i '' 's/mode: observe/mode: enforce/' examples/demo/onboard/cleanup_claude_agent/sponsio.yaml"
Enter

# ─── Step 5: run the agent — first violation halts the trajectory ───
Type "python examples/demo/onboard/cleanup_claude_agent/coding_agent.py --fast"
Enter
Sleep 6s
Sleep 2s

Hide
Type "cp /tmp/sponsio_demo_orig.py examples/demo/onboard/cleanup_claude_agent/coding_agent.py"
Enter
Type "rm -f /tmp/sponsio_wrap_snippet.py /tmp/sponsio_demo_orig.py"
Enter
Show
