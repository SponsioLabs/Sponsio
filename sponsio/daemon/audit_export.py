"""Send the daemon's audit records to an OTLP/HTTP endpoint.

``audit: {otlp_endpoint: ...}`` in ``daemon.yaml`` turns it on. Each
record becomes one span named ``sponsio.audit.<event>``; its attributes
carry every field of the record, including ``seq``, ``prev_hash`` and
``hash``, so the receiver can check the chain and notice a gap. The
transport is :class:`~sponsio.tracer.exporters.OtlpHttpExporter`'s:
batched, retried, never blocking the daemon.
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Any

from sponsio.tracer.exporters import OtlpHttpExporter

SERVICE_NAME = "sponsio-daemon"


def _value(v: Any) -> dict[str, Any]:
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, str):
        return {"stringValue": v}
    return {"stringValue": json.dumps(v, sort_keys=True, default=str)}


def _nanos(ts: Any) -> str:
    try:
        when = _dt.datetime.fromisoformat(str(ts))
    except ValueError:
        when = _dt.datetime.now(_dt.timezone.utc)
    return str(int(when.timestamp() * 1_000_000_000))


def record_to_otlp(record: dict[str, Any], host: str | None = None) -> dict:
    """One audit record as an OTLP/HTTP JSON envelope with one span."""
    digest = str(record.get("hash") or "").ljust(64, "0")
    start = _nanos(record.get("ts"))
    resource = [{"key": "service.name", "value": {"stringValue": SERVICE_NAME}}]
    if host:
        resource.append({"key": "host.name", "value": {"stringValue": host}})
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": resource},
                "scopeSpans": [
                    {
                        "scope": {"name": "sponsio.audit"},
                        "spans": [
                            {
                                "traceId": digest[:32],
                                "spanId": digest[32:48],
                                "name": f"sponsio.audit.{record.get('event')}",
                                "kind": 1,
                                "startTimeUnixNano": start,
                                "endTimeUnixNano": start,
                                "attributes": [
                                    {"key": f"sponsio.audit.{k}", "value": _value(v)}
                                    for k, v in sorted(record.items())
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }


class AuditOtlpExporter(OtlpHttpExporter):
    """:class:`OtlpHttpExporter` for audit records instead of span trees."""

    def __init__(self, endpoint: str, *, host: str | None = None, **kwargs: Any):
        super().__init__(endpoint, **kwargs)
        self._audit_host = host

    def _to_otlp(self, record: Any) -> dict:  # type: ignore[override]
        return record_to_otlp(record, host=self._audit_host)
