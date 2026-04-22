# Scan example

Two files that show how `sponsio scan` turns an agent's tool set into a
suggested contract config:

- [`scan_test_agent.py`](scan_test_agent.py) — a small agent with 7 tools
  (PII reads, destructive writes, external comms, raw SQL). The docstrings
  describe what each tool does.
- [`sponsio.yaml`](sponsio.yaml) — the contract config proposed by
  `sponsio scan`, with `assume`/`enforce` pairs grounded in the tools
  above. Regenerate with:

  ```bash
  sponsio scan examples/scan/scan_test_agent.py -o examples/scan/sponsio.yaml
  ```

  Add `--llm` for LLM-assisted inference, or `--policy <file.md>` to
  feed a policy document alongside the code.

Once you have a yaml, validate + use it:

```bash
sponsio validate --config examples/scan/sponsio.yaml
sponsio check --trace trace.json --config examples/scan/sponsio.yaml --agent agent
```
