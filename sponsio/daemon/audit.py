"""The daemon's audit log: one JSON record per line, append only, chained.

``<state_dir>/audit.jsonl`` holds what the daemon did and what it saw
done to it: starts and restarts, config drift, trace deletion, admin
calls, tamper attempts. Records carry ``ts`` (UTC, ISO 8601), ``event``
(the kind), ``pid``, and the kind's own fields.

Each record also carries ``seq`` (1, 2, ...), ``prev_hash`` (the
previous record's ``hash``, or 64 zeros for the first) and ``hash``,
the SHA-256 of the record's canonical JSON without ``hash``. Editing or
deleting a record breaks the chain at that point (:func:`verify`);
removing records from the end is caught by comparing with the hash a
running daemon last wrote (``audit.head`` on the admin socket), and by
whatever receives the exported records. Records written before the
chain existed are reported as unchained, not as broken.

A process that has written to the log keeps the last record it wrote.
If the file no longer holds that record when it next appends (the log
was deleted, cut, or rewritten), it does not start a new chain from
what is left: it records ``audit_log_cut`` chained to the record it
remembers, so the break stays in the log for :func:`verify` to report
however many records follow. A daemon that restarts remembers nothing,
so a cut made while it was down is caught only by the exported copy.

Appends take an exclusive lock on the file and read its last record, so
the daemon and a CLI process writing at the same moment still produce
one chain. Writing is best effort: a full disk must not stop the daemon
from deciding, so an ``OSError`` here is logged and swallowed.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

_logger = logging.getLogger("sponsio.daemon.audit")

AUDIT_FILE = "audit.jsonl"
GENESIS = "0" * 64

DAEMON_START = "daemon_start"
"""A daemon came up with no prior instance on record."""
DAEMON_RESTART = "daemon_restart"
"""A daemon came up after a prior instance went away without stopping
cleanly (its pid was still in the lock file and is not alive)."""
DAEMON_STOP = "daemon_stop"
AUDIT_LOG_CUT = "audit_log_cut"
"""The log no longer held the last record this process wrote."""

_lock = threading.Lock()
_exporters: dict[str, Callable[[dict[str, Any]], None]] = {}
_heads: dict[str, tuple[int, str]] = {}


def audit_path(state_dir: Path) -> Path:
    return Path(state_dir) / AUDIT_FILE


def record_hash(record: dict[str, Any]) -> str:
    """SHA-256 of ``record``'s canonical JSON, ``hash`` itself left out.

    ``record`` is what a JSON round trip gives (:func:`_as_json`): string
    keys only, so the order ``sort_keys`` puts them in is the order they
    will have when the line is read back.
    """
    body = {k: v for k, v in record.items() if k != "hash"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _as_json(record: dict[str, Any]) -> dict[str, Any]:
    """``record`` as it reads back from its line: keys turned into
    strings, values JSON can not hold turned into their ``str``."""
    return json.loads(json.dumps(record, separators=(",", ":"), default=str))


def set_exporter(
    state_dir: Path, exporter: Callable[[dict[str, Any]], None] | None
) -> None:
    """Send every record appended under ``state_dir`` to ``exporter`` too
    (the daemon's OTLP export), or stop with ``None``."""
    key = str(Path(state_dir))
    with _lock:
        if exporter is None:
            _exporters.pop(key, None)
        else:
            _exporters[key] = exporter


def head(state_dir: Path) -> tuple[int, str] | None:
    """``(seq, hash)`` of the last record this process appended."""
    with _lock:
        return _heads.get(str(Path(state_dir)))


def _chained_tail(fd: int):
    """``(seq, hash)`` of the file's chained records, last first, read
    backwards from the end so a long log costs only its tail."""
    pos = os.lseek(fd, 0, os.SEEK_END)
    rest = b""
    while pos > 0:
        step = min(65536, pos)
        pos -= step
        os.lseek(fd, pos, os.SEEK_SET)
        lines = (os.read(fd, step) + rest).split(b"\n")
        # The first piece may be a partial line unless we reached the start.
        rest = lines[0] if pos > 0 else b""
        for raw in reversed(lines[1:] if pos > 0 else lines):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if isinstance(rec, dict) and isinstance(rec.get("hash"), str):
                seq = rec.get("seq")
                yield (seq if isinstance(seq, int) else 0), rec["hash"]


def _last_chained(fd: int) -> tuple[int, str]:
    """``(seq, hash)`` of the file's last chained record, read from its
    tail; ``(0, GENESIS)`` when there is none."""
    return next(_chained_tail(fd), (0, GENESIS))


def _holds(fd: int, seq: int, hash_: str) -> bool:
    """Does the file still hold the chained record ``(seq, hash_)``?"""
    for s, h in _chained_tail(fd):
        if s == seq:
            return h == hash_
        if s < seq:
            return False
    return False


def _ends_mid_line(fd: int) -> bool:
    size = os.lseek(fd, 0, os.SEEK_END)
    if size == 0:
        return False
    os.lseek(fd, size - 1, os.SEEK_SET)
    return os.read(fd, 1) != b"\n"


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        if n <= 0:  # pragma: no cover - os.write raises instead
            raise OSError("short write")
        view = view[n:]


def _chain_and_write(fd: int, key: str, record: dict[str, Any]) -> None:
    """Give ``record`` its place in the chain and write it, with the
    file locked."""
    record["seq"], record["prev_hash"] = _last_chained(fd)
    record["seq"] += 1
    record["hash"] = record_hash(record)
    _write_all(fd, (json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8"))
    _heads[key] = (record["seq"], record["hash"])


def append(state_dir: Path, event: str, **fields: Any) -> dict[str, Any] | None:
    """Append one chained record. Returns it, or ``None`` when it could
    not be written."""
    record: dict[str, Any] = {
        "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds"),
        "event": event,
        "pid": os.getpid(),
    }
    record.update(fields)
    path = audit_path(state_dir)
    key = str(Path(state_dir))
    exporter = None
    written: list[dict[str, Any]] = []
    try:
        record = _as_json(record)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            with _lock:
                _flock(fd)
                try:
                    if _ends_mid_line(fd):
                        # A torn last line (a crash or a full disk mid-write):
                        # end it, so this record is a line of its own.
                        _write_all(fd, b"\n")
                    remembered = _heads.get(key)
                    if remembered is not None and not _holds(fd, *remembered):
                        cut = _as_json(
                            {
                                "ts": record["ts"],
                                "event": AUDIT_LOG_CUT,
                                "pid": os.getpid(),
                                "expected_seq": remembered[0],
                                "file_seq": _last_chained(fd)[0],
                            }
                        )
                        cut["seq"] = remembered[0] + 1
                        cut["prev_hash"] = remembered[1]
                        cut["hash"] = record_hash(cut)
                        _write_all(
                            fd,
                            (json.dumps(cut, separators=(",", ":")) + "\n").encode(),
                        )
                        _heads[key] = (cut["seq"], cut["hash"])
                        written.append(cut)
                    _chain_and_write(fd, key, record)
                    written.append(record)
                finally:
                    _funlock(fd)
                exporter = _exporters.get(key)
        finally:
            os.close(fd)
    except Exception as e:  # noqa: BLE001 - the log never stops a decision
        _logger.warning("could not write audit record %s: %s", event, e)
        return None
    if exporter is not None:
        for rec in written:
            try:
                exporter(rec)
            except Exception as e:  # noqa: BLE001 - export never blocks the log
                _logger.warning("could not export audit record %s: %s", event, e)
    return record


def _flock(fd: int) -> None:
    try:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)
    except (ImportError, OSError):  # pragma: no cover - no flock here
        pass


def _funlock(fd: int) -> None:
    try:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)
    except (ImportError, OSError):  # pragma: no cover
        pass


def read(state_dir: Path, event: str | None = None) -> list[dict[str, Any]]:
    """Every record, oldest first, optionally of one kind."""
    path = audit_path(state_dir)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if isinstance(rec, dict) and (event is None or rec.get("event") == event):
            out.append(rec)
    return out


@dataclass
class ChainReport:
    """What :func:`verify` found."""

    records: int = 0
    chained: int = 0
    unchained: int = 0
    """Records before the first chained one (written by an older daemon)."""
    head: tuple[int, str] | None = None
    """``(seq, hash)`` of the last chained record."""
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def verify(
    state_dir: Path, expected_head: tuple[int, str] | None = None
) -> ChainReport:
    """Check the chain from the first chained record to the last.

    Each record's ``hash`` must match its content, its ``prev_hash`` the
    previous record's ``hash``, and its ``seq`` the previous one plus
    one. A line that does not parse, or an unchained record after the
    chain began, is a problem too. ``expected_head`` (the last record a
    running daemon wrote) must still be in the log with that hash, or
    the log lost its tail; records after it, from another process, are
    fine.
    """
    report = ChainReport()
    path = audit_path(state_dir)
    if not path.exists():
        if expected_head is not None:
            report.problems.append("the audit log is missing")
        return report
    prev_hash: str | None = None
    prev_seq = 0
    found_head = False
    for lineno, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        raw = raw.strip()
        if not raw:
            continue
        report.records += 1
        try:
            rec = json.loads(raw)
        except ValueError:
            report.problems.append(f"line {lineno}: not JSON")
            continue
        if not isinstance(rec, dict):
            report.problems.append(f"line {lineno}: not a record")
            continue
        if not isinstance(rec.get("hash"), str):
            if prev_hash is None:
                report.unchained += 1
            else:
                report.problems.append(f"line {lineno}: unchained record in the chain")
            continue
        report.chained += 1
        seq = rec.get("seq")
        if record_hash(rec) != rec["hash"]:
            report.problems.append(
                f"line {lineno} (seq {seq}): content does not match its hash (edited)"
            )
        expected_prev = GENESIS if prev_hash is None else prev_hash
        if rec.get("prev_hash") != expected_prev:
            report.problems.append(
                f"line {lineno} (seq {seq}): prev_hash does not match the record "
                f"before it (a record was removed or replaced)"
            )
        if seq != prev_seq + 1:
            report.problems.append(f"line {lineno}: seq {seq} follows {prev_seq}")
        prev_hash = rec["hash"]
        prev_seq = seq if isinstance(seq, int) else prev_seq
        report.head = (prev_seq, prev_hash)
        if expected_head is not None and (seq, rec["hash"]) == tuple(expected_head):
            found_head = True
    if expected_head is not None and not found_head:
        report.problems.append(
            f"the daemon last wrote seq {expected_head[0]}, but the log ends at "
            f"{report.head[0] if report.head else 'nothing'} (records were "
            f"removed from the end)"
        )
    return report


def pid_alive(pid: int) -> bool:
    """Is a process with ``pid`` running (as far as this user can tell)?"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
