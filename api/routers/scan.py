"""Scan upload endpoint — accept source files, run CodeAnalyzer + scorer."""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import List

import yaml
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from api import db
from sponsio.discovery.extractors.code_analysis import CodeAnalyzer
from sponsio.scoring import ToolDef, badge_url, score_tools

from api.routers.score import DeductionResponse, ScoreResponse

router = APIRouter()


_ACCEPTED_SUFFIXES = {".py", ".yaml", ".yml", ".zip"}
_MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB (zips need more headroom)
_MAX_ZIP_FILES = 500
_MAX_EXTRACTED_BYTES = 50 * 1024 * 1024  # 50 MB after extraction


def _inventory_to_tooldefs(inventory: list[dict]) -> List[ToolDef]:
    """Convert CodeAnalyzer inventory dicts to ToolDef objects."""
    tools: List[ToolDef] = []
    for t in inventory:
        docstring = (t.get("docstring") or "").strip()
        params_str = t.get("params") or ""
        params: dict[str, str] = {}
        if params_str:
            for part in params_str.split(","):
                part = part.strip()
                if not part:
                    continue
                if ":" in part:
                    key, _, val = part.partition(":")
                    params[key.strip()] = val.strip()
                else:
                    params[part] = ""
        tools.append(
            ToolDef(
                name=t["name"],
                description=docstring.split("\n")[0] if docstring else "",
                parameters=params,
            )
        )
    return tools


def _scan_python(path: Path, agent_id: str) -> tuple[List[ToolDef], str]:
    """Scan a single .py file. Returns (tools, yaml_content)."""
    analyzer = CodeAnalyzer(use_llm=False)
    inventory = analyzer.get_tool_inventory([path])
    yaml_content = analyzer.generate_yaml(
        [path], agent_id=agent_id, tool_inventory=inventory
    )
    return _inventory_to_tooldefs(inventory), yaml_content


def _scan_zip(zip_path: Path, agent_id: str) -> tuple[List[ToolDef], str]:
    """Extract a .zip and scan all .py files. Returns (tools, yaml_content).

    Guards against zip-bombs by capping file count and total extracted size,
    and rejects absolute or parent-traversal paths.
    """
    extract_dir = Path(tempfile.mkdtemp(prefix="sponsio_scan_"))
    try:
        with zipfile.ZipFile(zip_path) as zf:
            members = zf.infolist()
            if len(members) > _MAX_ZIP_FILES:
                raise HTTPException(
                    413,
                    f"Zip contains too many entries ({len(members)} > {_MAX_ZIP_FILES})",
                )
            total_size = sum(m.file_size for m in members)
            if total_size > _MAX_EXTRACTED_BYTES:
                raise HTTPException(
                    413,
                    f"Zip too large when extracted ({total_size} bytes > "
                    f"{_MAX_EXTRACTED_BYTES})",
                )
            for m in members:
                name = m.filename
                if name.startswith("/") or ".." in Path(name).parts:
                    raise HTTPException(400, f"Unsafe path in zip: {name!r}")
            zf.extractall(extract_dir)

        analyzer = CodeAnalyzer(use_llm=False)
        inventory = analyzer.get_tool_inventory([extract_dir])
        yaml_content = analyzer.generate_yaml(
            [extract_dir], agent_id=agent_id, tool_inventory=inventory
        )
        return _inventory_to_tooldefs(inventory), yaml_content
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)


def _tools_from_yaml(path: Path) -> List[ToolDef]:
    """Parse a sponsio.yaml file and extract its tools section."""
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise HTTPException(400, f"Invalid YAML: {e}") from e

    if not isinstance(data, dict):
        raise HTTPException(400, "YAML root must be a mapping")

    raw_tools = data.get("tools") or []
    if not isinstance(raw_tools, list):
        raise HTTPException(400, "'tools' must be a list")

    tools: List[ToolDef] = []
    for raw in raw_tools:
        if not isinstance(raw, dict) or "name" not in raw:
            continue
        params_val = raw.get("params") or raw.get("parameters") or {}
        params: dict[str, str] = {}
        if isinstance(params_val, dict):
            params = {str(k): str(v) for k, v in params_val.items()}
        elif isinstance(params_val, str):
            for part in params_val.split(","):
                part = part.strip()
                if not part:
                    continue
                if ":" in part:
                    key, _, val = part.partition(":")
                    params[key.strip()] = val.strip()
                else:
                    params[part] = ""
        tools.append(
            ToolDef(
                name=str(raw["name"]),
                description=str(raw.get("description", "")),
                parameters=params,
            )
        )
    return tools


_VALID_SOURCES = {"upload", "cli"}


def _source_use_case(source: str) -> str:
    """Map a source value to its use_case tag in the DB."""
    return "scan_cli" if source == "cli" else "scan_upload"


@router.post("/upload", response_model=ScoreResponse)
async def upload_scan(
    file: UploadFile = File(...),
    source: str = Query(
        "upload",
        description="Where this scan came from: 'upload' (browser) or 'cli' (sponsio scan --push)",
    ),
):
    """Accept a .py / .zip / .yaml file, extract tools, score, and persist.

    The ``source`` query parameter lets the caller tag the scan so that the
    CLI tab and Upload tab in the dashboard can show only their own results.
    """
    if source not in _VALID_SOURCES:
        raise HTTPException(
            400, f"Invalid source {source!r}. Expected one of: {sorted(_VALID_SOURCES)}"
        )

    filename = file.filename or "uploaded"
    suffix = Path(filename).suffix.lower()
    if suffix not in _ACCEPTED_SUFFIXES:
        raise HTTPException(
            400,
            f"Unsupported file type {suffix!r}. "
            f"Accepted: {', '.join(sorted(_ACCEPTED_SUFFIXES))}",
        )

    content = await file.read()
    if len(content) > _MAX_FILE_BYTES:
        raise HTTPException(413, f"File too large (max {_MAX_FILE_BYTES // 1024} KB)")

    with tempfile.NamedTemporaryFile(mode="wb", suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    agent_name = Path(filename).stem or "uploaded_agent"

    try:
        if suffix == ".py":
            tools, yaml_content = _scan_python(tmp_path, agent_id=agent_name)
        elif suffix == ".zip":
            tools, yaml_content = _scan_zip(tmp_path, agent_id=agent_name)
        else:
            # .yaml / .yml — preserve the uploaded content verbatim
            tools = _tools_from_yaml(tmp_path)
            yaml_content = content.decode("utf-8", errors="replace")
    finally:
        tmp_path.unlink(missing_ok=True)

    if not tools:
        raise HTTPException(
            422,
            f"No tools found in {filename}. "
            "For .py files, look for @tool decorators or Agent(tools=[...]). "
            "For .zip files, the archive must contain at least one .py file with tools. "
            "For .yaml files, add a 'tools:' section.",
        )

    report = score_tools(tools, agent_name=agent_name)

    details = report.to_dict()
    details["yaml_content"] = yaml_content
    details["source_filename"] = filename
    details["source"] = source

    description_prefix = "CLI" if source == "cli" else "Uploaded"

    row_id = db.insert_score(
        agent_name=report.agent_name,
        score=report.score,
        grade=report.grade,
        timestamp=report.timestamp,
        details=details,
        display_name=None,
        description=f"{description_prefix}: {filename}",
        email=None,
        framework=None,
        use_case=_source_use_case(source),
        is_public=False,
    )

    return ScoreResponse(
        id=row_id,
        score=report.score,
        grade=report.grade,
        agent_name=report.agent_name,
        timestamp=report.timestamp,
        badge_url=report.to_badge_url(),
        deductions=[DeductionResponse(**d.to_dict()) for d in report.deductions],
        suggested_contracts=report.suggested_contracts,
    )


class ScanDetailResponse(BaseModel):
    id: int
    agent_name: str
    score: int
    grade: str
    timestamp: str
    description: str
    yaml_content: str
    source_filename: str
    deductions: list[dict]
    suggested_contracts: list[str]


_SCAN_USE_CASES = {"scan_upload", "scan_cli"}


def _matches_source(row: dict, source: str | None) -> bool:
    """True if row belongs to the requested source (or any source if None)."""
    use_case = row.get("use_case")
    if use_case not in _SCAN_USE_CASES:
        return False
    if source is None:
        return True
    return use_case == _source_use_case(source)


@router.get("/history")
def list_scan_history(
    limit: int = 10,
    source: str | None = Query(
        None, description="Filter by source: 'upload', 'cli', or omit for both"
    ),
):
    """Return recent scan results, newest first, optionally filtered by source."""
    rows = db.list_scores(limit=200, offset=0)
    scans = [r for r in rows if _matches_source(r, source)][:limit]
    return {
        "items": [
            {
                "id": r["id"],
                "agent_name": r["agent_name"],
                "score": r["score"],
                "grade": r["grade"],
                "timestamp": r["timestamp"],
                "description": r.get("description", ""),
                "source": "cli" if r.get("use_case") == "scan_cli" else "upload",
                "badge_url": badge_url(r["grade"], r["score"]),
            }
            for r in scans
        ],
        "count": len(scans),
    }


@router.get("/latest", response_model=ScanDetailResponse | None)
def get_latest_scan(
    source: str | None = Query(
        None, description="Filter by source: 'upload', 'cli', or omit for either"
    ),
):
    """Return the most recent scan detail (including YAML), or null.

    When ``source`` is specified, only scans from that source are considered.
    This lets the dashboard's CLI tab poll for CLI pushes without being
    contaminated by browser uploads (and vice versa).
    """
    rows = db.list_scores(limit=200, offset=0)
    for r in rows:
        if _matches_source(r, source):
            details = r.get("details", {})
            return ScanDetailResponse(
                id=r["id"],
                agent_name=r["agent_name"],
                score=r["score"],
                grade=r["grade"],
                timestamp=r["timestamp"],
                description=r.get("description", ""),
                yaml_content=details.get("yaml_content", ""),
                source_filename=details.get("source_filename", ""),
                deductions=details.get("deductions", []),
                suggested_contracts=details.get("suggested_contracts", []),
            )
    return None


@router.delete("/history")
def clear_scan_history(
    source: str | None = Query(
        None, description="Clear only one source; omit to clear both"
    ),
):
    """Delete all persisted scan-upload / scan-cli rows.

    This wipes backend history only; the frontend's localStorage paste-history
    is cleared separately by the UI.
    """
    from api import db as _db

    rows = _db.list_scores(limit=10000, offset=0)
    deleted = 0
    for r in rows:
        if _matches_source(r, source):
            # db.py doesn't expose a delete helper; delete directly.
            conn = _db._get_conn()
            with _db._lock:
                conn.execute("DELETE FROM scores WHERE id = ?", (r["id"],))
                conn.commit()
            deleted += 1
    return {"deleted": deleted}


@router.get("/{scan_id}", response_model=ScanDetailResponse)
def get_scan_detail(scan_id: int):
    """Full scan detail, including the stored YAML content."""
    row = db.get_score(scan_id)
    if row is None or row.get("use_case") not in _SCAN_USE_CASES:
        raise HTTPException(404, f"Scan {scan_id} not found")
    details = row.get("details", {})
    return ScanDetailResponse(
        id=row["id"],
        agent_name=row["agent_name"],
        score=row["score"],
        grade=row["grade"],
        timestamp=row["timestamp"],
        description=row.get("description", ""),
        yaml_content=details.get("yaml_content", ""),
        source_filename=details.get("source_filename", ""),
        deductions=details.get("deductions", []),
        suggested_contracts=details.get("suggested_contracts", []),
    )


@router.get("/{scan_id}/yaml", response_class=PlainTextResponse)
def download_scan_yaml(scan_id: int):
    """Download the generated sponsio.yaml for a given scan.

    Returns the raw YAML as text/yaml with a Content-Disposition header so
    browsers treat it as a download.
    """
    row = db.get_score(scan_id)
    if row is None or row.get("use_case") not in _SCAN_USE_CASES:
        raise HTTPException(404, f"Scan {scan_id} not found")
    yaml_content = row.get("details", {}).get("yaml_content", "")
    if not yaml_content:
        raise HTTPException(404, f"No YAML stored for scan {scan_id}")
    return PlainTextResponse(
        yaml_content,
        media_type="text/yaml",
        headers={
            "Content-Disposition": f'attachment; filename="sponsio-{scan_id}.yaml"',
        },
    )
