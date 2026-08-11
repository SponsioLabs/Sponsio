"""``sponsio://project`` — a config that lives in the cloud.

    guard = sponsio.Sponsio(config="sponsio://alpha-pod-research", agent_id="quant")

The reference names a project, not an agent: the guard already knows its own
``agent_id``, and making the user repeat it in two places is a free chance to
get them out of sync. A project-wide pull returns every agent's book in one
document and the guard loads only its own section.

Resolution order, and the diagnostics that go with it — a user must never
have to guess which rulebook is running:

1. **Cloud** — fetch, cache, use it. Prints the version and sha.
2. **Cache** — network down but we pulled before: use the cached copy and say
   it is stale.
3. **Local yaml** — never pulled: fall back to ``sponsio.yaml`` next to the
   process and say so.
4. **Nothing** — raise. Running an agent with no contracts because the network
   blipped is the one outcome worse than failing to start.

No key is not an error at any step: it means the user never asked for cloud,
and steps 3-4 apply directly.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from sponsio.cloud.client import CloudClient, CloudError

SCHEME = "sponsio://"

# sponsio://project  |  sponsio://project@v7
_REF_RE = re.compile(r"^sponsio://(?P<project>[A-Za-z0-9][A-Za-z0-9._-]*)(?:@v(?P<version>\d+))?$")

# Local fallbacks, in the order a repo usually names them.
LOCAL_FALLBACKS = ("sponsio.yaml", "sponsio.yml", ".sponsio/sponsio.yaml")


class CloudRefError(ValueError):
    """The reference is malformed, or nothing could be resolved."""


@dataclass(frozen=True)
class CloudRef:
    project: str
    version: int | None = None

    def __str__(self) -> str:
        return f"{SCHEME}{self.project}" + (f"@v{self.version}" if self.version else "")


def is_cloud_ref(value: object) -> bool:
    return isinstance(value, str) and value.startswith(SCHEME)


def parse_ref(value: str) -> CloudRef:
    match = _REF_RE.match(value.strip())
    if not match:
        raise CloudRefError(
            f"malformed cloud config reference: {value!r}. "
            f"Expected {SCHEME}<project> or {SCHEME}<project>@v<N>"
        )
    version = match.group("version")
    return CloudRef(project=match.group("project"), version=int(version) if version else None)


def cache_dir() -> Path:
    """Per-endpoint so a staging key never serves a production book."""
    root = os.environ.get("SPONSIO_CACHE_DIR")
    if root:
        return Path(root)
    host = hashlib.sha256(CloudClient().url.encode()).hexdigest()[:8]
    return Path.home() / ".sponsio" / "cache" / host


def cache_path(ref: CloudRef) -> Path:
    name = ref.project + (f"@v{ref.version}" if ref.version else "") + ".yaml"
    return cache_dir() / name


def _find_local_fallback(start: Path | None = None) -> Path | None:
    base = start or Path.cwd()
    for name in LOCAL_FALLBACKS:
        candidate = base / name
        if candidate.is_file():
            return candidate
    return None


def _say(message: str, *, quiet: bool) -> None:
    """Every implicit network action announces itself. A package that pulls
    config behind your back is a package you stop trusting."""
    if not quiet:
        # flush: the guard's own banner goes through a buffered writer, and a
        # checkout line that lands after the contract table reads like it
        # happened after the fact.
        print(f"  sponsio: {message}", flush=True)


def resolve_config_ref(
    value: str,
    *,
    client: CloudClient | None = None,
    quiet: bool = False,
    cwd: Path | None = None,
) -> Path:
    """Resolve a ``sponsio://`` reference to a local yaml path.

    Raises :class:`CloudRefError` only when no rulebook could be found at all.
    """
    ref = parse_ref(value)
    client = client or CloudClient()
    cached = cache_path(ref)

    if client.configured:
        try:
            pulled = client.pull_rulebook(ref.project, version=ref.version)
        except CloudError as exc:
            if exc.status == 404:
                _say(f"{ref.project}: nothing published yet ({exc})", quiet=quiet)
            elif exc.status in (401, 403):
                # A rejected key is worth shouting about: the user thinks they
                # are on the cloud path and they are not.
                _say(f"key rejected ({exc}) — falling back to local rules", quiet=quiet)
            else:
                _say(f"cloud unreachable ({exc})", quiet=quiet)
        else:
            try:
                cached.parent.mkdir(parents=True, exist_ok=True)
                cached.write_text(pulled.yaml_text)
            except OSError as exc:  # cache is an optimisation, not a requirement
                _say(f"could not write cache: {exc}", quiet=quiet)
                scratch = Path(os.environ.get("TMPDIR", "/tmp")) / f"sponsio-{ref.project}.yaml"
                scratch.write_text(pulled.yaml_text)
                return scratch
            stamp = pulled.versions or "?"
            _say(f"rulebook ← cloud checkout · {ref.project} {stamp}", quiet=quiet)
            return cached
    else:
        _say(
            f"no API key — {ref} needs one; using local rules "
            f"(set SPONSIO_API_KEY or run `sponsio login`)",
            quiet=quiet,
        )

    if cached.is_file():
        _say(f"rulebook ← cache (stale) · {cached}", quiet=quiet)
        return cached

    local = _find_local_fallback(cwd)
    if local is not None:
        _say(f"rulebook ← local file · {local}", quiet=quiet)
        return local

    raise CloudRefError(
        f"cannot resolve {ref}: the cloud is unreachable, nothing is cached, and no "
        f"local rulebook was found (looked for {', '.join(LOCAL_FALLBACKS)}). "
        f"Refusing to start an agent with no contracts."
    )
