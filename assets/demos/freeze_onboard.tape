# Freeze — full onboard journey (onboard → apply wrap snippet → flip
# enforce → run → BLOCKED).
#
# Demo dir: examples/demo/onboard/freeze_langgraph (LangGraph coding
# agent under a code-freeze that wants to run destructive SQL).
#
# Same shape as ``assets/demos/backup_onboard.tape``: hidden setup
# strips the patch block + writes the wrap snippet to /tmp; visible
# steps run onboard, sed the snippet back in, flip the yaml mode,
# and run the agent.  Net effect on the repo is zero.
#
# Run with:  vhs assets/demos/freeze_onboard.tape
# Requires:  GOOGLE_API_KEY in shell env (sourced from .venv/bin/activate).

Output assets/demos/freeze_onboard.gif

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
Type "rm -f examples/demo/onboard/freeze_langgraph/sponsio.yaml examples/demo/onboard/freeze_langgraph/.sponsiorc"
Enter
Type "cp examples/demo/onboard/freeze_langgraph/coding_agent.py /tmp/sponsio_demo_orig.py"
Enter
Type `cat > /tmp/sponsio_wrap_snippet.py << 'EOF'`
Enter
Type "    # ─── sponsio onboard patch ─────────────────────────────────────"
Enter
Type "    from sponsio.langgraph import Sponsio"
Enter
Type ""
Enter
Type `    guard = Sponsio(config=str(Path(__file__).parent / "sponsio.yaml"), agent_id="agent")`
Enter
Type "    tools_by_name = guard.wrap(TOOLS).tools_by_name"
Enter
Type "    # ─── /sponsio onboard patch ────────────────────────────────────"
Enter
Type "EOF"
Enter
Type `sed -i '' '/─── sponsio onboard patch ─/,/─── \/sponsio onboard patch ─/d' examples/demo/onboard/freeze_langgraph/coding_agent.py`
Enter
Type "clear"
Enter
Show

Type "sponsio onboard examples/demo/onboard/freeze_langgraph --no-interactive"
Enter
Sleep 13s

Type `sed -i '' '/^    tools_by_name = {t.name/r /tmp/sponsio_wrap_snippet.py' examples/demo/onboard/freeze_langgraph/coding_agent.py`
Enter

Type "sed -i '' 's/mode: observe/mode: enforce/' examples/demo/onboard/freeze_langgraph/sponsio.yaml"
Enter

Type "python examples/demo/onboard/freeze_langgraph/coding_agent.py --fast"
Enter
Sleep 6s
Sleep 2s

Hide
Type "cp /tmp/sponsio_demo_orig.py examples/demo/onboard/freeze_langgraph/coding_agent.py"
Enter
Type "rm -f /tmp/sponsio_wrap_snippet.py /tmp/sponsio_demo_orig.py"
Enter
Show
