#!/usr/bin/env node
// Thin shim → compiled ``dist/cli.js``.  Keeps ``npx`` invocations
// snappy and lets us publish a single pre-compiled artifact without
// shipping a ``tsx`` / ``esbuild`` dependency.
require("../dist/cli.js");
