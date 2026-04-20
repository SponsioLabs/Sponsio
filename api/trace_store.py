# TODO: Replace with ClickHouse/SQLite for persistence
"""In-memory trace storage for OTEL span ingestion.

Stores normalized spans keyed by trace_id. Provides tree reconstruction
and summary queries. Swap this implementation for ClickHouse/SQLite when
persistence is needed.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any, Dict, List, Optional


def _parse_otel_value(val: dict) -> Any:
    """Parse an OTEL attribute value dict to a Python value."""
    if "stringValue" in val:
        return val["stringValue"]
    if "boolValue" in val:
        return val["boolValue"]
    if "intValue" in val:
        return int(val["intValue"])
    if "doubleValue" in val:
        return float(val["doubleValue"])
    if "arrayValue" in val:
        return [_parse_otel_value(v) for v in val["arrayValue"].get("values", [])]
    return str(val)


def _parse_otel_attrs(attrs: list) -> Dict[str, Any]:
    """Parse an OTEL attributes list to a flat dict."""
    result: Dict[str, Any] = {}
    for attr in attrs or []:
        key = attr.get("key", "")
        value = attr.get("value", {})
        if key:
            result[key] = _parse_otel_value(value)
    return result


_STATUS_MAP = {0: "UNSET", 1: "OK", 2: "ERROR"}


class TraceStore:
    """In-memory trace storage.

    Stores normalized spans in ``trace_id -> list[span_dict]`` mapping.
    Thread-safe via a lock.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._traces: Dict[str, List[Dict[str, Any]]] = {}

    def ingest(self, resource_spans: list) -> int:
        """Parse OTLP resourceSpans, store normalized spans. Return count."""
        count = 0
        with self._lock:
            for rs in resource_spans or []:
                resource_attrs = _parse_otel_attrs(
                    rs.get("resource", {}).get("attributes", [])
                )
                for ss in rs.get("scopeSpans", []):
                    scope_name = ss.get("scope", {}).get("name", "")
                    for span in ss.get("spans", []):
                        normalized = self._normalize_span(
                            span, scope_name, resource_attrs
                        )
                        tid = normalized["trace_id"]
                        if tid not in self._traces:
                            self._traces[tid] = []
                        self._traces[tid].append(normalized)
                        count += 1
        return count

    def ingest_sponsio_span(self, span_dict: dict) -> None:
        """Ingest a Sponsio span tree (from POST /monitor/push-span).

        Converts the Sponsio span dict format into normalized OTEL-like
        spans and stores them alongside external OTEL spans.
        """
        trace_id = span_dict.get("trace_id") or uuid.uuid4().hex
        with self._lock:
            if trace_id not in self._traces:
                self._traces[trace_id] = []
            self._flatten_sponsio_span(
                span_dict, trace_id, None, self._traces[trace_id]
            )

    def list_traces(
        self, limit: int = 50, has_violations: Optional[bool] = None
    ) -> List[Dict[str, Any]]:
        """Return trace summaries, newest first."""
        with self._lock:
            summaries = []
            for trace_id, spans in self._traces.items():
                summary = self._build_summary(trace_id, spans)
                if has_violations is not None:
                    if summary["has_violations"] != has_violations:
                        continue
                summaries.append(summary)
            summaries.sort(key=lambda s: s["timestamp_ms"], reverse=True)
            return summaries[:limit]

    def get_trace_tree(self, trace_id: str) -> Optional[Dict[str, Any]]:
        """Return reconstructed span tree for one trace."""
        with self._lock:
            spans = self._traces.get(trace_id)
            if spans is None:
                return None
            return {
                "trace_id": trace_id,
                "root_spans": self._build_tree(spans),
            }

    def get_trace_flat(self, trace_id: str) -> Optional[List[Dict[str, Any]]]:
        """Return flat span list, sorted by start_time_ns."""
        with self._lock:
            spans = self._traces.get(trace_id)
            if spans is None:
                return None
            return sorted(spans, key=lambda s: s["start_time_ns"])

    def clear(self) -> None:
        """Delete all stored traces."""
        with self._lock:
            self._traces.clear()

    @property
    def trace_count(self) -> int:
        """Number of distinct traces stored."""
        with self._lock:
            return len(self._traces)

    # -----------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------

    @staticmethod
    def _normalize_span(
        span: dict, scope: str, resource: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Normalize an OTLP span dict to our flat format."""
        start_ns = int(span.get("startTimeUnixNano", "0"))
        end_ns = int(span.get("endTimeUnixNano", "0"))
        duration_ms = (end_ns - start_ns) / 1_000_000 if end_ns > start_ns else 0.0
        name = span.get("name", "")
        status_code = span.get("status", {}).get("code", 0)

        return {
            "trace_id": span.get("traceId", ""),
            "span_id": span.get("spanId", ""),
            "parent_id": span.get("parentSpanId") or None,
            "name": name,
            "start_time_ns": start_ns,
            "end_time_ns": end_ns,
            "duration_ms": round(duration_ms, 2),
            "status": _STATUS_MAP.get(status_code, "UNSET"),
            "attributes": _parse_otel_attrs(span.get("attributes", [])),
            "scope": scope,
            "resource": resource,
            "is_sponsio": name.startswith("sponsio."),
        }

    @staticmethod
    def _build_summary(trace_id: str, spans: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Build a trace summary from a list of normalized spans."""
        roots = [s for s in spans if s["parent_id"] is None]
        root = roots[0] if roots else spans[0] if spans else {}

        sponsio_spans = [s for s in spans if s["is_sponsio"]]
        contract_checks = [
            s for s in sponsio_spans if s["name"] == "sponsio.contract_check"
        ]
        violations = [s for s in contract_checks if s["status"] == "ERROR"]

        start_ns = root.get("start_time_ns", 0)
        total_duration = (
            max((s.get("end_time_ns", 0) for s in spans), default=0) - start_ns
        )
        duration_ms = total_duration / 1_000_000 if total_duration > 0 else 0.0

        return {
            "trace_id": trace_id,
            "root_name": root.get("name", ""),
            "root_scope": root.get("scope", ""),
            "span_count": len(spans),
            "duration_ms": round(duration_ms, 2),
            "has_sponsio_spans": len(sponsio_spans) > 0,
            "has_violations": len(violations) > 0,
            "contracts_checked": len(contract_checks),
            "violations_found": len(violations),
            "timestamp_ms": start_ns // 1_000_000 if start_ns else 0,
        }

    @staticmethod
    def _build_tree(spans: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Reconstruct span tree from flat list using parent_id."""
        by_id: Dict[str, Dict[str, Any]] = {}
        for s in spans:
            node = {**s, "children": []}
            by_id[s["span_id"]] = node

        roots: List[Dict[str, Any]] = []
        for s in spans:
            node = by_id[s["span_id"]]
            pid = s["parent_id"]
            if pid and pid in by_id:
                by_id[pid]["children"].append(node)
            else:
                roots.append(node)

        return roots

    def _flatten_sponsio_span(
        self,
        span_dict: dict,
        trace_id: str,
        parent_id: Optional[str],
        out: List[Dict[str, Any]],
    ) -> None:
        """Recursively flatten a Sponsio span tree into normalized spans."""
        span_id = uuid.uuid4().hex[:16]
        name = span_dict.get("span_type", "sponsio.unknown")
        status_str = span_dict.get("status", "ok")
        status = "ERROR" if status_str in ("violated", "error") else "OK"

        # Extract typed attributes from Sponsio span fields
        attrs: Dict[str, Any] = {}
        for key in (
            "agent_id",
            "action",
            "blocked",
            "det_violations",
            "sto_violations",
            "total_contracts_checked",
            "contract_name",
            "pipeline",
            "formula_desc",
            "result",
            "kind",
            "severity",
            "evidence",
            "strategy",
            "result_action",
            "constraint_name",
            "score",
            "threshold",
            "passed",
        ):
            if key in span_dict:
                attrs[f"sponsio.{key}"] = span_dict[key]

        # Use monotonic times as relative nanoseconds (not wall-clock, but sufficient for ordering)
        start = span_dict.get("start_time", 0)
        end = span_dict.get("end_time", start)
        # Convert to nanoseconds (monotonic, but preserves ordering)
        start_ns = int(start * 1e9)
        end_ns = int(end * 1e9) if end else start_ns

        normalized = {
            "trace_id": trace_id,
            "span_id": span_id,
            "parent_id": parent_id,
            "name": name,
            "start_time_ns": start_ns,
            "end_time_ns": end_ns,
            "duration_ms": round((end_ns - start_ns) / 1_000_000, 2),
            "status": status,
            "attributes": attrs,
            "scope": "sponsio",
            "resource": {"service.name": "sponsio"},
            "is_sponsio": True,
        }
        out.append(normalized)

        for child in span_dict.get("children", []):
            self._flatten_sponsio_span(child, trace_id, span_id, out)
