"""Contract-extraction prompt templates per target host.

These markdown files are read by ``sponsio plugin prompt <host>``
and printed to stdout for the host agent (Claude Code / OpenClaw)
to apply against the introspected tool inventory.

The files are package data, not Python — kept here as a sub-package
purely so ``importlib.resources.files("sponsio.plugin.prompts")``
can discover them across editable installs, wheels, and zips.
"""
