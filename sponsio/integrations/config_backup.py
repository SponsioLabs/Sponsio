"""Write a host's config file without losing what was there.

Host config files belong to the user: ``~/.claude/settings.json`` holds
their permissions, env and other hooks, and Claude Code re-reads it on
every tool call. The installers used to rewrite it with ``write_text``
and keep nothing, so a bad write (it happened, during development) lost
the user's settings for good.

Every change goes through here. :func:`write` first copies the current
file to ``<name>.sponsio-backup-YYYYmmddHHMMSS`` beside it, then writes
a temp file in the same directory and renames it over the original, so a
reader sees the old file or the new one and never half of either.
:func:`remove` backs up before deleting. :func:`restore` puts a backup
back, backing up what it replaces. A config file that is a symlink (a
dotfiles checkout) is written through to its target; the link stays.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Callable

BACKUP_INFIX = ".sponsio-backup-"

_BACKUP_RE = re.compile(r"^(?P<stamp>\d{14})(?:-(?P<n>\d+))?$")


def _stamp() -> str:
    return time.strftime("%Y%m%d%H%M%S", time.localtime())


def _backup_key(path: Path, name: str) -> tuple[str, int] | None:
    prefix = path.name + BACKUP_INFIX
    if not name.startswith(prefix):
        return None
    m = _BACKUP_RE.match(name[len(prefix) :])
    if m is None:
        return None
    return (m["stamp"], int(m["n"] or 0))


def backups(path: Path) -> list[Path]:
    """Backups of ``path``, oldest first."""
    path = Path(path)
    if not path.parent.is_dir():
        return []
    found = []
    for entry in path.parent.iterdir():
        key = _backup_key(path, entry.name)
        if key is not None and entry.is_file():
            found.append((key, entry))
    return [p for _k, p in sorted(found)]


def latest_backup(path: Path) -> Path | None:
    """The most recent backup of ``path``, or ``None``."""
    found = backups(path)
    return found[-1] if found else None


def backup(path: Path) -> Path | None:
    """Copy ``path`` to a new timestamped backup beside it.

    Returns the backup, or ``None`` when there is no file to back up.
    Two backups in one second get ``-1``, ``-2``, ... suffixes.
    """
    path = Path(path)
    if not path.is_file():
        return None
    base = f"{path.name}{BACKUP_INFIX}{_stamp()}"
    target = path.parent / base
    n = 0
    while target.exists():
        n += 1
        target = path.parent / f"{base}-{n}"
    shutil.copy2(path, target)
    return target


def _write_atomic(path: Path, data: bytes) -> None:
    real = Path(os.path.realpath(path))
    real.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = real.stat().st_mode & 0o7777
    except FileNotFoundError:
        mode = 0o644
    fd, tmp = tempfile.mkstemp(prefix=f".{real.name}.", dir=real.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, real)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write(path: Path, text: str) -> Path | None:
    """Back up ``path`` if it exists, then replace it with ``text``
    atomically. Returns the backup, or ``None`` for a new file."""
    saved = backup(path)
    _write_atomic(Path(path), text.encode("utf-8"))
    return saved


def remove(path: Path) -> Path | None:
    """Back up ``path``, then delete it. Returns the backup."""
    saved = backup(path)
    Path(path).unlink(missing_ok=True)
    return saved


def prune(path: Path, ours: "Callable[[bytes], bool]") -> list[Path]:
    """Delete the backups of ``path`` whose content ``ours`` says holds
    nothing but Sponsio's own entries. Returns what was deleted.

    For an uninstall that removes a file Sponsio created: copies of it
    taken on reinstall hold only Sponsio's hooks, so they keep nothing a
    user could want back, and offering one would put the hooks back."""
    gone = []
    for saved in backups(path):
        try:
            if ours(saved.read_bytes()):
                saved.unlink()
                gone.append(saved)
        except OSError:
            continue
    return gone


def restore(path: Path, saved: Path) -> Path | None:
    """Put ``saved`` back at ``path``. What it replaces is backed up
    first; returns that backup (``None`` when ``path`` did not exist)."""
    replaced = backup(path)
    _write_atomic(Path(path), Path(saved).read_bytes())
    return replaced
