# Wire — full onboard journey for a CrewAI project (onboard → drop in
# hand-authored contracts → flip enforce → run → BLOCKED).
#
# Demo dir: examples/demo/onboard/wire_crewai (CrewAI accounts-payable
# copilot that tries to wire $847k to an unverified vendor under SLA
# pressure).
#
# Same shape as ``cleanup_onboard.tape``: the demo ships a curated
# yaml ("the rules you'd write by hand") because LLM extraction on
# this scenario is unreliable for the amount-cap rule that's the
# whole point of the breach.  The tape's visible ``cp curated.yaml
# sponsio.yaml`` step stands in for "now write the rule you care
# about".
#
# Run with:  vhs assets/demos/wire_onboard.tape
# Requires:  GOOGLE_API_KEY in shell env (sourced from .venv/bin/activate).

Output assets/demos/wire_onboard.gif

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
Type "rm -f examples/demo/onboard/wire_crewai/sponsio.yaml examples/demo/onboard/wire_crewai/.sponsiorc"
Enter
Type "cp examples/demo/onboard/wire_crewai/ap_copilot.py /tmp/sponsio_demo_orig.py"
Enter
Type `cat > /tmp/sponsio_wrap_snippet.py << 'EOF'`
Enter
Type "    # ─── sponsio onboard patch ─────────────────────────────────────"
Enter
Type "    from sponsio.crewai import Sponsio"
Enter
Type ""
Enter
Type `    guard = Sponsio(config=str(Path(__file__).parent / "sponsio.yaml"), agent_id="agent")`
Enter
Type "    tools_by_name = {t.name: t for t in guard.wrap(TOOLS)}"
Enter
Type "    # ─── /sponsio onboard patch ────────────────────────────────────"
Enter
Type "EOF"
Enter
Type `sed -i '' '/─── sponsio onboard patch ─/,/─── \/sponsio onboard patch ─/d' examples/demo/onboard/wire_crewai/ap_copilot.py`
Enter
Type "clear"
Enter
Show

# ─── Step 1: onboard ────────────────────────────────────────────────
Type "sponsio onboard examples/demo/onboard/wire_crewai --no-interactive"
Enter
# 18s budget — covers gemini-2.5-flash-lite extraction (~3s) plus
# doctor (~4s) plus typing of the next command on the way in.  Headroom
# for API jitter without rendering a stale spinner past it.
Sleep 18s

# ─── Step 2: drop in the hand-authored contracts ────────────────────
# Onboard's LLM extraction inconsistently catches the wire amount
# cap (the whole point of the breach), so the demo ships a curated
# rule set checked into ``sponsio.curated.yaml`` next to the agent
# file.  Real users would either re-run onboard until happy, audit
# / bump to a bigger model, or write rules by hand — this is option
# (c), and it's the most reproducible for the gif.
Type "cp examples/demo/onboard/wire_crewai/sponsio.curated.yaml examples/demo/onboard/wire_crewai/sponsio.yaml"
Enter

# ─── Step 3: apply the wrap snippet via sed ─────────────────────────
Type `sed -i '' '/^    tools_by_name = {f.__name__/r /tmp/sponsio_wrap_snippet.py' examples/demo/onboard/wire_crewai/ap_copilot.py`
Enter

# ─── Step 4: flip yaml from observe to enforce ──────────────────────
Type "sed -i '' 's/mode: observe/mode: enforce/' examples/demo/onboard/wire_crewai/sponsio.yaml"
Enter

# ─── Step 5: run the agent — first violation halts the trajectory ───
Type "python examples/demo/onboard/wire_crewai/ap_copilot.py --fast"
Enter
Sleep 6s
Sleep 2s

Hide
Type "cp /tmp/sponsio_demo_orig.py examples/demo/onboard/wire_crewai/ap_copilot.py"
Enter
Type "rm -f /tmp/sponsio_wrap_snippet.py /tmp/sponsio_demo_orig.py"
Enter
Show
