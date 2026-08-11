"""Client for Sponsio Cloud. Open source; the service behind it is not.

Nothing here decides anything. Enforcement is local, deterministic, and free:
without a key this package never makes a request, and the guard behaves
identically. What a key buys is access to things that are not on your
machine — a hosted rulebook, trace history, and the judgment services.

Two rules hold everywhere in this package:

* **The hot path never waits on the network.** Cloud calls happen at guard
  construction and at session end, never inside ``guard_before``.
* **Fail closed to what you already have.** A network failure falls back to
  the last cached rulebook, then to a local yaml, and says so out loud. It
  never silently runs with no contracts.
"""

from sponsio.cloud.client import CloudClient, CloudError
from sponsio.cloud.ref import CloudRef, is_cloud_ref, resolve_config_ref

__all__ = [
    "CloudClient",
    "CloudError",
    "CloudRef",
    "is_cloud_ref",
    "resolve_config_ref",
]
