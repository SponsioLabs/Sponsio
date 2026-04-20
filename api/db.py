"""SQLite storage for scoring submissions.

Creates ``data/scores.db`` on first use.  All access goes through
module-level functions; the connection is lazily initialized and
thread-safe (SQLite WAL mode + ``check_same_thread=False``).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional

_DB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
_DB_PATH = os.path.join(_DB_DIR, "scores.db")
_lock = threading.Lock()
_local = threading.local()
_initialized = False

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS scores (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name    TEXT    NOT NULL,
    score         INTEGER NOT NULL,
    grade         TEXT    NOT NULL,
    timestamp     TEXT    NOT NULL,
    details_json  TEXT    NOT NULL,
    display_name  TEXT    DEFAULT NULL,
    description   TEXT    DEFAULT NULL,
    email         TEXT    DEFAULT NULL,
    framework     TEXT    DEFAULT NULL,
    use_case      TEXT    DEFAULT NULL,
    is_public     INTEGER DEFAULT 0
);
"""

# Columns added after the initial schema.  _migrate() ensures they exist
# on databases created before the leaderboard feature.
_MIGRATION_COLUMNS = [
    ("display_name", "TEXT DEFAULT NULL"),
    ("description", "TEXT DEFAULT NULL"),
    ("email", "TEXT DEFAULT NULL"),
    ("framework", "TEXT DEFAULT NULL"),
    ("use_case", "TEXT DEFAULT NULL"),
    ("is_public", "INTEGER DEFAULT 0"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    """Add any missing columns to an existing scores table."""
    for col_name, col_def in _MIGRATION_COLUMNS:
        try:
            conn.execute(f"ALTER TABLE scores ADD COLUMN {col_name} {col_def}")
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()


def _get_conn() -> sqlite3.Connection:
    """Get a thread-local SQLite connection.

    Each thread gets its own connection to avoid segfaults from
    concurrent access to a shared connection under uvicorn.
    """
    global _initialized
    conn = getattr(_local, "conn", None)
    if conn is None:
        os.makedirs(_DB_DIR, exist_ok=True)
        conn = sqlite3.connect(_DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        # Schema + migration only once (first thread)
        if not _initialized:
            with _lock:
                if not _initialized:
                    conn.execute(_SCHEMA)
                    conn.commit()
                    _migrate(conn)
                    _initialized = True
        _local.conn = conn
    return conn


# ---------------------------------------------------------------------------
# Score CRUD
# ---------------------------------------------------------------------------
def insert_score(
    agent_name: str,
    score: int,
    grade: str,
    timestamp: str,
    details: Dict[str, Any],
    *,
    display_name: Optional[str] = None,
    description: Optional[str] = None,
    email: Optional[str] = None,
    framework: Optional[str] = None,
    use_case: Optional[str] = None,
    is_public: bool = False,
) -> int:
    """Insert a scoring result.  Returns the new row id."""
    conn = _get_conn()
    with _lock:
        cur = conn.execute(
            "INSERT INTO scores "
            "(agent_name, score, grade, timestamp, details_json, "
            " display_name, description, email, framework, use_case, is_public) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                agent_name,
                score,
                grade,
                timestamp,
                json.dumps(details),
                display_name,
                description,
                email,
                framework,
                use_case,
                1 if is_public else 0,
            ),
        )
        conn.commit()
        return cur.lastrowid  # type: ignore[return-value]


def get_score(row_id: int) -> Dict[str, Any] | None:
    """Fetch a single score by id."""
    conn = _get_conn()

    row = conn.execute("SELECT * FROM scores WHERE id = ?", (row_id,)).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def list_scores(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    """List scores, newest first."""
    conn = _get_conn()

    rows = conn.execute(
        "SELECT * FROM scores ORDER BY id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def count_scores() -> int:
    """Return total number of scores in the database."""
    conn = _get_conn()
    row = conn.execute("SELECT COUNT(*) FROM scores").fetchone()
    return row[0]


# ---------------------------------------------------------------------------
# Leaderboard queries
# ---------------------------------------------------------------------------
def _leaderboard_where(
    period: str = "all",
    framework: Optional[str] = None,
) -> tuple[str, list[Any]]:
    """Build the WHERE clause shared by leaderboard queries."""
    where = ["is_public = 1", "display_name IS NOT NULL"]
    params: list[Any] = []
    if period == "today":
        where.append("timestamp >= datetime('now', '-1 day')")
    elif period == "week":
        where.append("timestamp >= datetime('now', '-7 days')")
    if framework and framework != "all":
        where.append("framework = ?")
        params.append(framework)
    return " AND ".join(where), params


def leaderboard(
    limit: int = 50,
    offset: int = 0,
    period: str = "all",
    framework: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return public entries, best score per display_name.

    Sorted by score DESC, then timestamp ASC (earlier = tiebreak winner).
    """
    conn = _get_conn()

    where_clause, params = _leaderboard_where(period, framework)

    # Best score per display_name: pick the row with the highest score
    # (and earliest timestamp as tiebreak).
    sql = f"""
        SELECT *
        FROM scores
        WHERE id IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY display_name
                    ORDER BY score DESC, timestamp ASC
                ) AS rn
                FROM scores
                WHERE {where_clause}
            )
            WHERE rn = 1
        )
        ORDER BY score DESC, timestamp ASC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def count_public_entries(
    period: str = "all",
    framework: Optional[str] = None,
) -> int:
    """Count unique display_names on the leaderboard (after dedup)."""
    conn = _get_conn()
    where_clause, params = _leaderboard_where(period, framework)
    sql = f"""
        SELECT COUNT(DISTINCT display_name)
        FROM scores
        WHERE {where_clause}
    """
    return conn.execute(sql, params).fetchone()[0]


def leaderboard_stats() -> Dict[str, Any]:
    """Aggregate stats across all submissions."""
    conn = _get_conn()

    total = conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0]
    public = conn.execute(
        "SELECT COUNT(*) FROM scores WHERE is_public = 1 AND display_name IS NOT NULL"
    ).fetchone()[0]
    avg = conn.execute("SELECT AVG(score) FROM scores").fetchone()[0]

    # Grade distribution
    grade_rows = conn.execute(
        "SELECT grade, COUNT(*) as cnt FROM scores GROUP BY grade"
    ).fetchall()
    grade_dist = {r[0]: r[1] for r in grade_rows}

    # Framework distribution (skip NULLs)
    fw_rows = conn.execute(
        "SELECT framework, COUNT(*) as cnt FROM scores "
        "WHERE framework IS NOT NULL GROUP BY framework"
    ).fetchall()
    fw_dist = {r[0]: r[1] for r in fw_rows}

    # Top public agent

    top_row = conn.execute(
        "SELECT display_name, score, grade FROM scores "
        "WHERE is_public = 1 AND display_name IS NOT NULL "
        "ORDER BY score DESC, timestamp ASC LIMIT 1"
    ).fetchone()
    top_agent = dict(top_row) if top_row else None

    return {
        "total_submissions": total,
        "public_entries": public,
        "average_score": round(avg, 1) if avg is not None else 0.0,
        "grade_distribution": grade_dist,
        "framework_distribution": fw_dist,
        "top_agent": top_agent,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    d["details"] = json.loads(d.pop("details_json"))
    return d
