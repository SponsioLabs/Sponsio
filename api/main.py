"""FastAPI application for Sponsio dashboard."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from api.routers import (
    agents,
    analytics,
    contracts,
    discovery,
    playground,
    monitor,
    demo,
    patterns,
    scan,
    score,
    leaderboard,
    otel_ingest,
)

logger = logging.getLogger("sponsio.api")


def _load_dotenv() -> None:
    """Load .env from project root if it exists."""
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

_cors_origins = os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",")

# Routes that bypass token auth even when ``SPONSIO_API_TOKEN`` is set.
# Health is needed by load-balancers; the static / dashboard HTML and
# the SPA's own static assets must load without a header.
_AUTH_EXEMPT_PREFIXES: tuple[str, ...] = (
    "/api/health",
    "/static",
    "/leaderboard",
    "/docs",
    "/openapi.json",
    "/redoc",
)


def _api_token() -> str | None:
    """Read the configured API token. Empty string is treated as unset
    so accidentally-blank env vars don't silently disable auth."""
    tok = os.environ.get("SPONSIO_API_TOKEN", "").strip()
    return tok or None


@asynccontextmanager
async def _lifespan(app: FastAPI):
    if _api_token() is None:
        print(
            "\n"
            "  \033[93m\u26a0 Sponsio API running with no auth "
            "(SPONSIO_API_TOKEN unset).\033[0m\n"
            "  \033[93m  Do not expose this port to the public internet.\033[0m\n"
            f"  CORS origins: {_cors_origins}\n"
        )
    else:
        print(
            "\n"
            "  \033[92m\u2713 Sponsio API token auth enabled "
            "(SPONSIO_API_TOKEN set).\033[0m\n"
            "  Clients must send 'X-Sponsio-Token: <token>' on every "
            "/api request (except /api/health and static assets).\n"
            f"  CORS origins: {_cors_origins}\n"
        )
    yield


app = FastAPI(title="Sponsio API", version="0.1.0", lifespan=_lifespan)

# CORS: bearer-token auth means we don't need cookie credentials, so
# ``allow_credentials`` is False — that also relaxes the browser rule
# that forbids ``allow_origins=["*"]`` with credentials. Methods and
# headers are explicitly listed instead of "*" so a misconfiguration
# can't accidentally widen the surface.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Sponsio-Token"],
)


@app.middleware("http")
async def _token_auth_middleware(request: Request, call_next):
    """Enforce ``X-Sponsio-Token`` on /api/* when a token is configured.

    Behavior matrix:

    * ``SPONSIO_API_TOKEN`` unset → no auth (legacy dev workflow). A
      one-time warning is printed at startup.
    * ``SPONSIO_API_TOKEN`` set, request path matches an exempt prefix
      → no check (health, static, dashboard HTML, OpenAPI docs).
    * ``SPONSIO_API_TOKEN`` set, request is an OPTIONS preflight →
      no check (CORS preflight has no custom headers).
    * Otherwise the request must carry ``X-Sponsio-Token: <token>``;
      anything else returns ``401 Unauthorized``. Constant-time
      comparison via ``hmac.compare_digest`` to defeat timing oracles.
    """
    expected = _api_token()
    if expected is None:
        return await call_next(request)

    path = request.url.path
    if request.method == "OPTIONS" or any(
        path == p or path.startswith(p + "/") for p in _AUTH_EXEMPT_PREFIXES
    ):
        return await call_next(request)

    presented = request.headers.get("x-sponsio-token", "")
    import hmac

    if not presented or not hmac.compare_digest(presented, expected):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Missing or invalid X-Sponsio-Token"},
        )
    return await call_next(request)


app.include_router(agents.router, prefix="/api/agents", tags=["agents"])
app.include_router(contracts.router, prefix="/api/contracts", tags=["contracts"])
app.include_router(playground.router, prefix="/api/playground", tags=["playground"])
app.include_router(monitor.router, prefix="/api/monitor", tags=["monitor"])
app.include_router(demo.router, prefix="/api/demo", tags=["demo"])
app.include_router(patterns.router, prefix="/api/patterns", tags=["patterns"])
app.include_router(score.router, prefix="/api/score", tags=["score"])
app.include_router(leaderboard.router, prefix="/api/leaderboard", tags=["leaderboard"])
app.include_router(otel_ingest.router, prefix="/api/otel", tags=["otel"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["analytics"])
app.include_router(discovery.router, prefix="/api/discovery", tags=["discovery"])
app.include_router(scan.router, prefix="/api/scan", tags=["scan"])

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(_STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/leaderboard")
def leaderboard_page():
    return FileResponse(os.path.join(_STATIC_DIR, "leaderboard.html"))


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/system")
def get_system():
    from api.state import state

    return {
        "name": state.system.name,
        "agent_count": len(state.agents),
        "contract_count": len(state.system.contracts),
        "violation_count": len(state.monitor.log),
    }
