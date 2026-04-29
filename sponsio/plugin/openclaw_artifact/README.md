# sponsio/plugin/openclaw_artifact/

Pre-built copy of the `sponsio-openclaw` OpenClaw plugin, bundled
into the Sponsio Python wheel so `sponsio host install openclaw`
can deploy the plugin without requiring node/npm/tsc on the user's
machine.

The canonical source lives at `plugins/sponsio-openclaw/` in the
Sponsio repo. **Do not edit files here directly** — re-run
`scripts/sync_openclaw_artifact.py` after rebuilding the plugin's
TypeScript. The CI test in `tests/test_openclaw_artifact_sync.py`
will fail the build if the two trees drift.

Update flow:

```bash
cd plugins/sponsio-openclaw
npm install && npm run build
python scripts/sync_openclaw_artifact.py
```
