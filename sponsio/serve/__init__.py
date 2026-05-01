"""Local single-user dashboard backend for Sponsio OSS.

Read-only FastAPI app that exposes session-log JSONL files at
``~/.sponsio/sessions/`` to a local web UI. Bound to ``127.0.0.1`` by
default — no auth, no multi-tenant, no hosted ingestion. The
multi-tenant variant lives in Sponsio Cloud.

Public API:

    from sponsio.serve import create_app
    app = create_app()
"""

from sponsio.serve.app import create_app

__all__ = ["create_app"]
