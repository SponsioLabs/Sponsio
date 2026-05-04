# scripts/

Maintainer-facing helpers — sync runners, build hooks, and pre-release
QA. Not shipped to PyPI users (`[tool.setuptools.packages.find]` only
includes `sponsio*`); they live here so contributors can run them
locally and so CI can wire into them.

## What's in here

| Script | When to run |
|---|---|
| [`bench_verifier.py`](bench_verifier.py) | After engine changes — verify the README's perf claims (~50µs p99 for det enforcement). Pure Python, no external deps. |
| [`build_llms_txt.py`](build_llms_txt.py) | After editing `README.md` or anything in `docs/`. Regenerates the [`llms-full.txt`](../llms-full.txt) bundle that LLM coding tools (Cursor, Claude Code) ingest as project context. |
| [`sync_init_examples.py`](sync_init_examples.py) | After editing `examples/eval/`. Mirrors it into `sponsio/init_examples/eval/` so the bundle `sponsio init --with-example` reads stays current. CI guard: [`tests/test_init_examples_sync.py`](../tests/test_init_examples_sync.py). |
| [`sync_openclaw_artifact.py`](sync_openclaw_artifact.py) | After rebuilding `plugins/sponsio-openclaw/` (the TS plugin). Mirrors the built `dist/` into the wheel-bundled `sponsio/plugin/openclaw_artifact/` so `sponsio host install openclaw` works without node/npm on the user's machine. CI guard: [`tests/test_openclaw_artifact_sync.py`](../tests/test_openclaw_artifact_sync.py). |
| [`sync-ts-mirror.sh`](sync-ts-mirror.sh) | After Python-side edits to `sponsio/contracts/` / `sponsio/prompts/` / `sponsio/init_examples/`. Pushes the canonical Python copies into `ts/packages/sdk/` so the TS SDK ships the same packs / prompts / scaffolding. |
| [`check-ts-parity.sh`](check-ts-parity.sh) | CI step — assert that the Python side and the TS-mirrored copies haven't drifted. Pair with `sync-ts-mirror.sh` (which fixes copy-able drift). |
| [`validate_dfa_backend.py`](validate_dfa_backend.py) | Pre-release — run every shipped demo twice (recursive backend, DFA backend) and assert identical violation counts. Catches DFA-side regressions that the unit tests miss. |

## Common workflows

```bash
# After engine changes
python scripts/bench_verifier.py

# After contract / prompt / pack edits
bash scripts/sync-ts-mirror.sh
bash scripts/check-ts-parity.sh

# After example edits
python scripts/sync_init_examples.py
pytest tests/test_init_examples_sync.py

# After OpenClaw plugin rebuild
cd plugins/sponsio-openclaw && npm install && npm run build && cd ../..
python scripts/sync_openclaw_artifact.py

# After README / docs edits
python scripts/build_llms_txt.py

# Before tagging a release — full local gate
ruff check sponsio/ tests/
ruff format --check sponsio/ tests/
pytest tests/ -q --no-cov
bash scripts/check-ts-parity.sh
python scripts/bench_verifier.py
```
