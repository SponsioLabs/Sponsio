"""FastAPI application for Sponsio dashboard."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
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

app = FastAPI(title="Sponsio API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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


@app.on_event("startup")
def _startup_warning() -> None:
    print(
        "\n"
        "  \033[93m\u26a0 Sponsio API running in development mode (no auth).\033[0m\n"
        "  \033[93m  Do not expose to public internet.\033[0m\n"
        f"  CORS origins: {_cors_origins}\n"
    )


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
