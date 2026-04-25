# Backup Delete — WITHOUT Sponsio (breach trajectory)
Output assets/demos/backup_delete_without.gif

Set FontSize 14
Set Width 1200
Set Height 1000
Set Padding 20
Set Theme "Dracula"
Set TypingSpeed 20ms

# Env must come AFTER Set — vhs 0.11 resets terminal dims otherwise.
Env PYTHONPATH "."

Type "sponsio demo --scenario backup --no-guard"
Enter
Sleep 9s
