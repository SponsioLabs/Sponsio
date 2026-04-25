# Freeze Violation — WITH Sponsio (real CLI / TerminalReporter output)
Output assets/demos/freeze_violation_cli.gif

# ``PYTHONPATH=.`` makes the ``sponsio`` entry-point resolve to the
# local checkout even if the user's editable install points at a
# different worktree — guarantees the recording runs the code we ship.
Env PYTHONPATH "."

Set FontSize 14
Set Width 1200
Set Height 1000
Set Padding 20
Set Theme "Dracula"
Set TypingSpeed 20ms

Type "sponsio demo --scenario freeze"
Enter
Sleep 12s
Sleep 3s
