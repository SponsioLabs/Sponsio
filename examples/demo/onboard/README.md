# Onboard-flow demos

Same four trajectories as [`examples/demo/`](../) — but rewired to the
real `sponsio onboard <agent_file.py>` user journey. The original demos
hand-construct contracts inside the agent script (good for showing the
full DSL on one page); these mirror what most users actually do:

  1. They have an agent file with tools.
  2. They run `sponsio onboard <agent_file.py>`, which scans the file,
     writes a `sponsio.yaml` next to it, and prints a 2-line patch.
  3. They paste the 2-line patch into the agent file. Done.

Each subdirectory below is a self-contained checkout of step 3:

| Scenario | Folder | Framework | Story |
|---|---|---|---|
| cleanup | [cleanup_claude_agent/](cleanup_claude_agent/) | `claude_agent` | "Clean up unused files." Agent reads `.env`, then sweeps `.env`, `.git/`, commits, force-pushes. |
| backup | [backup_langgraph/](backup_langgraph/) | `langgraph` | SRE cost-optimizer deletes off-site DR backups to hit a "cut storage 20%" KPI. |
| wire | [wire_crewai/](wire_crewai/) | `crewai` | AP copilot wires $847k to a brand-new vendor under a 24h SLA — no compliance approval, no human confirm. |
| freeze | [freeze_langgraph/](freeze_langgraph/) | `langgraph` | Recreates the July 2025 Replit incident — code freeze declared, agent drops prod tables, fabricates rows, writes a "database intact" report. |

In every folder you'll find:

- `<agent>.py` — the agent file, with the **two onboard-patched lines**
  marked. Strip those two lines and you have a vanilla, framework-only
  agent file.
- `sponsio.yaml` — what `sponsio onboard <agent>.py` would have written.
  Tools come from the file's `@tool` decorators / function signatures;
  contracts are the inferred starter pack for that risk profile.

Run any of them the same way as the originals:

```bash
python3 examples/demo/onboard/cleanup_claude_agent/coding_agent.py
python3 examples/demo/onboard/cleanup_claude_agent/coding_agent.py --no-guard

python3 examples/demo/onboard/backup_langgraph/sre_optimizer.py
python3 examples/demo/onboard/wire_crewai/ap_copilot.py
python3 examples/demo/onboard/freeze_langgraph/coding_agent.py
```

The outcomes match the originals — same trajectory, same blocks, same
final state. The only thing that changes is *where the contracts live*:
in `sponsio.yaml` instead of inline in Python.

To regenerate any of these `sponsio.yaml` files yourself, run:

```bash
cd examples/demo/onboard/<folder>/
sponsio onboard <agent>.py --force
```
