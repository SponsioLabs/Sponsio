"""Mock attacker endpoint for the docker-compose-config bypass demo.

Listens on :9999 and prints any incoming POST body to stderr so the
audience sees secrets surface in real time.  Companion to the
`attacker_view.sh` tail-watcher and the `mock_github_mcp/`
attacker_view.

Why a separate process and not just a netcat one-liner: we want
predictable HTTP semantics (Claude / curl will retry on connection
error, expect 200), and the formatted output is easier to read on
camera than raw stdin.

Run via:
    python3 attacker_server.py [--port 9999] [--leak-log /tmp/leaked.log]
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


LEAK_LOG = Path(os.environ.get("LEAK_LOG", "/tmp/sponsio-demo-leaked-secrets.log"))


class _Handler(BaseHTTPRequestHandler):
    def _read_body(self) -> bytes:
        length = int(self.headers.get("content-length", 0))
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def do_POST(self) -> None:  # noqa: N802 - http.server protocol
        body = self._read_body()
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        text = body.decode("utf-8", errors="replace")
        # Append to leak log so attacker_view.sh's tail can highlight it.
        LEAK_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LEAK_LOG, "a", encoding="utf-8") as fh:
            fh.write("\n")
            fh.write("=" * 78 + "\n")
            fh.write(f"NEW POST  {ts}\n")
            fh.write(f"From {self.client_address[0]}  Path: {self.path}\n")
            fh.write("-" * 78 + "\n")
            fh.write(text + "\n")
            fh.write("=" * 78 + "\n")
            fh.flush()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def do_GET(self) -> None:  # noqa: N802
        # Respond to GET so a curl health-check doesn't fail.
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"attacker.io ready")

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        # Quiet the stderr "GET /... HTTP/1.1" spam — leak log is the
        # primary record.  Keep a single-line breadcrumb on stderr.
        sys.stderr.write(f"[attacker {time.strftime('%H:%M:%S')}] {fmt % args}\n")
        sys.stderr.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument(
        "--leak-log",
        default=str(LEAK_LOG),
        help="path to append received POST bodies to",
    )
    args = parser.parse_args()
    LEAK_LOG_PATH = Path(args.leak_log)
    globals()["LEAK_LOG"] = LEAK_LOG_PATH

    sys.stderr.write(
        f"[attacker {time.strftime('%H:%M:%S')}] listening on "
        f"localhost:{args.port}, logging to {LEAK_LOG_PATH}\n"
    )
    sys.stderr.flush()
    HTTPServer(("127.0.0.1", args.port), _Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
