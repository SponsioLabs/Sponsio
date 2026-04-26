"""Plugin-system shield runtime (``sponsio shield ...``).

Hosts the bundled per-plugin default contract libraries that
:command:`sponsio shield init` copies into ``~/.sponsio/plugins/``,
plus future shield-only utilities (scan, registry, daemon, …).

The runtime hook adapter itself lives in :mod:`sponsio.guard_stdin` —
it predates this package and stays there for now to avoid churning
the import path users already pin.
"""
