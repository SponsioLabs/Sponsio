"""Signed rule files: a bucket's rules must be signed by a pinned key.

With ``signing:`` in ``daemon.yaml`` (a base64 Ed25519 public key and the
buckets it covers), every rule file of a covered bucket must have a
detached signature beside it, ``<file>.sig``: the base64 Ed25519
signature of the file's exact bytes. Packs that ship inside the engine
(``sponsio:...`` includes) are not signed; at Tier 2 the engine is
root-owned. A local file a bucket includes needs its own signature.

A bucket whose rules are unsigned, or signed by another key, is held:
every call it decides is denied with the reason, whatever ``on_error``
says, until signed rules are in place. The signing side (a fleet's
private key, and pushing signed books) belongs to the cloud; this module
only verifies.

Verification needs ``cryptography`` (``pip install --pre 'sponsio[signing]'``).
Without it a covered bucket is held too, never served unchecked.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

SIG_SUFFIX = ".sig"
RULES_UNSIGNED = "rules_unsigned"
"""Audit event: a bucket's rules failed their signature check."""


class SigningUnavailable(RuntimeError):
    """``cryptography`` is not installed."""


def _public_key(public_key_b64: str):
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
    except ImportError as e:  # pragma: no cover - exercised without the extra
        raise SigningUnavailable(
            "rule signatures need `cryptography`: pip install --pre 'sponsio[signing]'"
        ) from e
    raw = base64.b64decode(public_key_b64.strip(), validate=True)
    return Ed25519PublicKey.from_public_bytes(raw)


def verify_file(
    path: Path, public_key_b64: str, content: bytes | None = None
) -> str | None:
    """``None`` when ``path`` carries a valid signature by the key, else
    why not. ``content`` is the bytes to check, when the caller has them
    (the bytes it parsed); otherwise the file is read."""
    sig_path = Path(str(path) + SIG_SUFFIX)
    try:
        signature = base64.b64decode(
            sig_path.read_text(encoding="ascii").strip(), validate=True
        )
    except FileNotFoundError:
        return f"{path.name} has no signature ({sig_path.name})"
    except (OSError, ValueError) as e:
        return f"{sig_path.name} cannot be read: {e}"
    try:
        key = _public_key(public_key_b64)
    except SigningUnavailable as e:
        return str(e)
    except ValueError as e:
        return f"the configured signing key is not valid: {e}"
    from cryptography.exceptions import InvalidSignature

    try:
        key.verify(signature, content if content is not None else path.read_bytes())
    except InvalidSignature:
        return f"{path.name} is not signed by the configured key"
    except OSError as e:
        return f"{path.name} cannot be read: {e}"
    return None


def engine_dirs() -> tuple[Path, ...]:
    """The engine's package directory, as imported and as resolved."""
    import sponsio

    here = Path(os.path.abspath(sponsio.__file__)).parent
    return tuple(dict.fromkeys((here, here.resolve())))


def engine_dir() -> Path:
    return engine_dirs()[-1]


def _in_engine(name: str) -> bool:
    """A pack that ships in the engine: its path is in the engine both as
    named and once links are followed. A rule file that only links into
    the engine is not one."""
    dirs = engine_dirs()
    named = Path(os.path.abspath(name))
    real = Path(os.path.realpath(name))
    return any(d in named.parents for d in dirs) and any(
        d in real.parents for d in dirs
    )


def unsigned_reason(
    files: list[str] | tuple[str, ...],
    public_key_b64: str,
    contents: dict[str, bytes] | None = None,
) -> str | None:
    """Why a library made of ``files`` fails the signature check, or
    ``None``. Packs inside the engine are skipped. ``contents`` maps a
    file's real path to the bytes that were parsed; those bytes are the
    ones checked."""
    contents = contents or {}
    for name in files:
        if _in_engine(name):
            continue
        real = os.path.realpath(name)
        problem = verify_file(Path(name), public_key_b64, contents.get(real))
        if problem is not None:
            return problem
    return None


def sign_file(path: Path, private_key) -> Path:
    """Write ``<path>.sig`` with ``private_key`` (an
    ``Ed25519PrivateKey``). For tests and self-hosted setups; a fleet
    signs in the cloud."""
    signature = private_key.sign(Path(path).read_bytes())
    sig_path = Path(str(path) + SIG_SUFFIX)
    sig_path.write_text(base64.b64encode(signature).decode("ascii") + "\n")
    return sig_path
