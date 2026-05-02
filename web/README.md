# Sponsio Web Dashboard

Local single-user dashboard for Sponsio. Pairs with `sponsio serve` to
surface session-log JSONL files (`~/.sponsio/sessions/<agent>/*.jsonl`)
and the active rulebook to a browser UI for trace inspection.

This is the **OSS** dashboard — read-only, bound to `127.0.0.1`, no
auth, no ingestion endpoint. The multi-tenant hosted dashboard with
auth, OTel ingest, and cross-trace aggregation lives in Sponsio Cloud
(`pip install sponsio[cloud]`). See
[docs/oss_scope.md](../docs/oss_scope.md) for the full boundary.

## Pages

- **Monitor** — live session feed, contracts checked, violations,
  per-tool latency.
- **Rulebook** — the contracts loaded for the current agent, sourced
  from `sponsio.yaml` plus any included capability/incident bundles.

## Run it

```bash
# Backend (from the repo root)
pip install -e ".[web]"
sponsio serve --dev          # http://127.0.0.1:8000

# Frontend (this directory)
npm install
npm run dev                  # http://127.0.0.1:5173
```

The frontend dev server proxies `/api/*` to the backend on port 8000.

## Build for production

```bash
npm run build
```

Outputs to `web/dist/`. `sponsio serve` (without `--dev`) serves the
built bundle directly, so you only need Python at runtime.

## Stack

React 19 · TypeScript · Vite 6 · Tailwind 4 · React Router 7.
