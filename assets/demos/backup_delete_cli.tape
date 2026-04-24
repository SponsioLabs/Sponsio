# Backup Delete — WITH Sponsio (real CLI / TerminalReporter output)
Output assets/demos/backup_delete_cli.gif

Set FontSize 14
Set Width 1200
Set Height 800
Set Padding 20
Set Theme "Dracula"
Set TypingSpeed 20ms

# ``PYTHONPATH=.`` makes the ``sponsio`` entry-point resolve to the
# local checkout even if the user's editable install points at a
# different worktree — guarantees the recording runs the code we ship.
# Must come AFTER Set directives: in vhs 0.11 Env before Set resets
# terminal dimensions back to the default 1200x600.
Env PYTHONPATH "."

Type "sponsio demo --scenario backup"
Enter
Sleep 9s
Sleep 3s
