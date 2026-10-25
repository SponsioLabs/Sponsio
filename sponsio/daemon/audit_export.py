"""Send the daemon's audit records to an OTLP/HTTP endpoint.

``audit: {otlp_endpoint: ...}`` in ``daemon.yaml`` turns it on. Each
record becomes one span named ``sponsio.audit.<event>``; its attributes
carry every field of the record, including ``seq``, ``prev_hash`` and
``hash``, so the receiver can check the chain and notice a gap. The
resource carries ``sponsio.audit.stream``, a hash of the host and the
state dir, so a receiver keeps two daemons on one host apart. The
transport is :class:`~sponsio.tracer.exporters.OtlpHttpExporter`'s:
batched, retried, never blocking the daemon.
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Any

from sponsio.tracer.exporters import OtlpHttpExporter

SERVICE_NAME = "sponsio-daemon"


STREAM_FILE = "audit.stream"


def persistent_stream_id(state_dir: Any, host: str) -> str:
    """The id kept in ``<state_dir>/audit.stream``, made once at random.

    A hash of host and path alone changed with the hostname (laptops
    rename themselves), so a receiver saw a chain stop and another begin.
    When the file cannot be kept, :func:`stream_id` stands in.
    """
    import os
    import secrets
    from pathlib import Path

    path = Path(state_dir) / STREAM_FILE

    def read() -> str | None:
        try:
            value = path.read_text(encoding="ascii").strip()
        except (OSError, UnicodeDecodeError):
            return None
        ok = len(value) == 16 and all(c in "0123456789abcdef" for c in value)
        return value if ok else None

    found = read()
    if found:
        return found
    value = secrets.token_hex(8)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(value + "\n")
        return value
    except FileExistsError:
        # Another process made it first, or it holds something else.
        return read() or stream_id(host, state_dir)
    except OSError:
        return stream_id(host, state_dir)


def stream_id(host: str, state_dir: Any) -> str:
    """Which daemon's chain this is: the same for every record a state
    dir's log holds, different for another daemon on the same host."""
    import hashlib
    import os

    key = f"{host}\0{os.path.realpath(str(state_dir))}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


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


def record_to_otlp(
    record: dict[str, Any], host: str | None = None, stream: str | None = None
) -> dict:
    """One audit record as an OTLP/HTTP JSON envelope with one span."""
    digest = str(record.get("hash") or "").ljust(64, "0")
    start = _nanos(record.get("ts"))
    resource = [{"key": "service.name", "value": {"stringValue": SERVICE_NAME}}]
    if host:
        resource.append({"key": "host.name", "value": {"stringValue": host}})
    if stream:
        resource.append(
            {"key": "sponsio.audit.stream", "value": {"stringValue": stream}}
        )
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

    def __init__(
        self,
        endpoint: str,
        *,
        host: str | None = None,
        stream: str | None = None,
        **kwargs: Any,
    ):
        super().__init__(endpoint, **kwargs)
        self._audit_host = host
        self._audit_stream = stream

    def _to_otlp(self, record: Any) -> dict:  # type: ignore[override]
        return record_to_otlp(record, host=self._audit_host, stream=self._audit_stream)
