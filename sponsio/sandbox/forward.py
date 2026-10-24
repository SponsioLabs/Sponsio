"""Inside the Linux sandbox: the agent's only door out.

Standard library only; runs as ``python3 -I -S forward.py --unix PATH
--port N -- <command>``. It listens on ``127.0.0.1:N`` inside the
sandbox's empty network namespace, passes every connection to the egress
proxy's Unix socket (bind-mounted in), then runs the command and exits
with its status. The command sees ``HTTP(S)_PROXY`` pointing here; there
is no other route out.
"""

from __future__ import annotations

import os
import select
import socket
import subprocess
import sys
import threading


def _pipe(a: socket.socket, b: socket.socket) -> None:
    try:
        while True:
            ready, _w, _x = select.select([a, b], [], [], 300)
            if not ready:
                return
            for s in ready:
                data = s.recv(65536)
                if not data:
                    return
                (b if s is a else a).sendall(data)
    except OSError:
        return
    finally:
        a.close()
        b.close()


def _serve(listener: socket.socket, unix_path: str) -> None:
    while True:
        try:
            conn, _addr = listener.accept()
        except OSError:
            return
        try:
            up = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            up.connect(unix_path)
        except OSError:
            conn.close()
            continue
        threading.Thread(target=_pipe, args=(conn, up), daemon=True).start()


def main(argv: list[str]) -> int:
    if "--" not in argv:
        sys.stderr.write("usage: forward.py --unix PATH --port N -- command...\n")
        return 2
    split = argv.index("--")
    opts, command = argv[:split], argv[split + 1 :]
    unix_path = opts[opts.index("--unix") + 1]
    port = int(opts[opts.index("--port") + 1])
    # The namespace starts with loopback down.
    subprocess.run(["ip", "link", "set", "lo", "up"], capture_output=True, check=False)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(64)
    threading.Thread(target=_serve, args=(listener, unix_path), daemon=True).start()
    if not command:
        return 0
    try:
        return subprocess.call(command, env=os.environ)
    except FileNotFoundError:
        sys.stderr.write(f"sponsio sandbox: {command[0]}: not found\n")
        return 127


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
