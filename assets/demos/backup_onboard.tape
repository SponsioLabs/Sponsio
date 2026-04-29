# Backup — full onboard journey (onboard → apply wrap snippet → flip
# enforce → run → BLOCKED).
#
# Demo dir: examples/demo/onboard/backup_langgraph (LangGraph SRE
# cost optimizer that wants to delete prod snapshots).
#
# How the gif fakes the user journey from a single file:
#   * Repo state of sre_optimizer.py is the FINAL form (already
#     integrated) so users who clone and run get a working demo.
#   * Hidden setup at the top of this tape (a) backs up that file,
#     (b) writes the wrap snippet to /tmp/sponsio_wrap_snippet.py
#     mimicking "user copied onboard's printed snippet to a file",
#     (c) sed-strips the patch block out of the agent file so the
#     visible portion of the gif starts from a naive state.
#   * The visible ``sed ... 'r .../wrap_snippet.py'`` step re-inserts
#     the snippet — net result is the agent file is back to its
#     original repo state by the time recording ends, so no git
#     diff is produced.
#
# Run with:  vhs assets/demos/backup_onboard.tape
# Requires:  GOOGLE_API_KEY in shell env (sourced from .venv/bin/activate).

Output assets/demos/backup_onboard.gif

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
# Reset prior onboard outputs so the gif always records from a
# clean slate (would-be 2nd-run path otherwise preserves yaml +
# skips prompts, which would skip the LLM-inference shot we want
# to show).
Type "rm -f examples/demo/onboard/backup_langgraph/sponsio.yaml examples/demo/onboard/backup_langgraph/.sponsiorc"
Enter
# Back up the integrated agent file so we can restore it cleanly
# at the end (defense in depth — the visible sed below is supposed
# to put the file back to identical-to-repo state, but if the .tape
# aborts mid-recording we don't want a stripped agent.py left
# behind).
Type "cp examples/demo/onboard/backup_langgraph/sre_optimizer.py /tmp/sponsio_demo_orig.py"
Enter
# Pre-stage the wrap snippet at /tmp/sponsio_wrap_snippet.py so the
# visible ``sed ... 'r .../wrap_snippet.py'`` step has something to
# read.  Content matches exactly what onboard's wrap-snippet output
# looks like for LangGraph projects (3 functional lines).
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
# Strip the patch block from the agent file so the user journey
# starts from a naive state.  Range delete from the BEGIN marker to
# the /END marker, inclusive.
Type `sed -i '' '/─── sponsio onboard patch ─/,/─── \/sponsio onboard patch ─/d' examples/demo/onboard/backup_langgraph/sre_optimizer.py`
Enter
Type "clear"
Enter
Show

# ─── Step 1: onboard ────────────────────────────────────────────────
# ``--no-interactive`` skips the framework / provider prompts.  The
# LLM extraction now runs on gemini-2.5-flash-lite (~2-3s vs ~24s
# on plain flash) so the wait is brief; we still budget 8s to cover
# API jitter without colliding with the next visible keystroke.
Type "sponsio onboard examples/demo/onboard/backup_langgraph --no-interactive"
Enter
Sleep 8s

# ─── Step 2: apply the wrap snippet via sed ─────────────────────────
# ``sed`` with the ``r`` directive reads the contents of the file
# after the slash and inserts them immediately after the line that
# matches the address regex.  In effect: take the snippet onboard
# printed (here saved to /tmp/sponsio_wrap_snippet.py) and splice
# it into the agent file right after the existing ``tools_by_name =
# {...}`` line.  Real users would copy-paste; sed makes the step
# legible in 6 seconds of gif.
Type `sed -i '' '/^    tools_by_name = {t.name/r /tmp/sponsio_wrap_snippet.py' examples/demo/onboard/backup_langgraph/sre_optimizer.py`
Enter

# ─── Step 3: flip yaml from observe to enforce ──────────────────────
Type "sed -i '' 's/mode: observe/mode: enforce/' examples/demo/onboard/backup_langgraph/sponsio.yaml"
Enter

# ─── Step 4: run the agent — first violation halts the trajectory ───
Type "python examples/demo/onboard/backup_langgraph/sre_optimizer.py --fast"
Enter
Sleep 6s

# Final beat — leave the block message on screen for the gif's last
# frame so README readers see the punch line.
Sleep 2s

# Hidden cleanup — restore the agent file from backup so a future
# ``git status`` doesn't show this .tape's side effects.  Yaml
# doesn't need restoring (it gets re-generated on the next run via
# the ``rm -f`` in setup).
Hide
Type "cp /tmp/sponsio_demo_orig.py examples/demo/onboard/backup_langgraph/sre_optimizer.py"
Enter
Type "rm -f /tmp/sponsio_wrap_snippet.py /tmp/sponsio_demo_orig.py"
Enter
Show
