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


def verify_file(path: Path, public_key_b64: str) -> str | None:
    """``None`` when ``path`` carries a valid signature by the key, else
    why not."""
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
        key.verify(signature, Path(path).read_bytes())
    except InvalidSignature:
        return f"{path.name} is not signed by the configured key"
    except OSError as e:
        return f"{path.name} cannot be read: {e}"
    return None


def engine_dir() -> Path:
    import sponsio

    return Path(sponsio.__file__).resolve().parent


def unsigned_reason(
    files: list[str] | tuple[str, ...], public_key_b64: str
) -> str | None:
    """Why a library made of ``files`` fails the signature check, or
    ``None``. Files inside the engine are skipped."""
    engine = engine_dir()
    for name in files:
        path = Path(name).resolve()
        if engine in path.parents:
            continue
        problem = verify_file(path, public_key_b64)
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
