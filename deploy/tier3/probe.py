"""CI probe for the Tier 3 container recipe, run as the agent.

Prints one JSON object: whether a direct connection out works (it must
not), whether HTTPS to an allowed host works through the egress proxy
(it must), and whether a host outside egress.allow is refused (it must).
"""

import json
import os
import socket
import urllib.error
import urllib.request

out = {"proxy": os.environ.get("HTTPS_PROXY")}
try:
    socket.create_connection(("1.1.1.1", 443), timeout=5).close()
    out["direct"] = "ok"
except OSError as e:
    out["direct"] = f"fail: {type(e).__name__}"
try:
    with urllib.request.urlopen("https://pypi.org/simple/", timeout=30) as r:
        out["allowed"] = r.status
except Exception as e:  # noqa: BLE001
    out["allowed"] = f"fail: {e}"
try:
    urllib.request.urlopen("https://example.com/", timeout=30)
    out["refused"] = "reached"
except urllib.error.URLError as e:
    out["refused"] = f"refused: {e.reason}"
except Exception as e:  # noqa: BLE001
    out["refused"] = f"refused: {e}"
print(json.dumps(out))
