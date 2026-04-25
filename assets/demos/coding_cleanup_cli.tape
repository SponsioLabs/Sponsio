# Coding Cleanup — WITH Sponsio (real CLI / TerminalReporter output)
Output assets/demos/coding_cleanup_cli.gif

Set FontSize 14
Set Width 1200
Set Height 1000
Set Padding 20
Set Theme "Dracula"
Set TypingSpeed 20ms

# ``PYTHONPATH=.`` makes the ``sponsio`` entry-point resolve to the
# local checkout regardless of any editable install. Must come AFTER
# the Set directives — in vhs 0.11 Env before Set resets terminal
# dimensions back to the default 1200x600.
Env PYTHONPATH "."

Type "sponsio demo --scenario cleanup"
Enter
Sleep 9s
