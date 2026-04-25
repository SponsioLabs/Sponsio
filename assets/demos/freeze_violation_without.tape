# Freeze Violation — WITHOUT Sponsio (breach trajectory)
Output assets/demos/freeze_violation_without.gif

Set FontSize 14
Set Width 1200
Set Height 1000
Set Padding 20
Set Theme "Dracula"
Set TypingSpeed 20ms

# Env must come AFTER Set — vhs 0.11 resets terminal dims otherwise.
Env PYTHONPATH "."

Type "sponsio demo --scenario freeze --no-guard"
Enter
Sleep 11s
