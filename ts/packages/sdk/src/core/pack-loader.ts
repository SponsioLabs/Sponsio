/**
 * Resolve and load contract packs referenced via ``include:`` in
 * sponsio.yaml.
 *
 * Mirrors the Python ``sponsio.config._resolve_include_spec`` /
 * ``_load_pack_contracts`` behaviour:
 *
 *   - ``sponsio:<category>/<name>`` resolves to the bundled pack
 *     yaml under this package's ``contracts/`` directory. Confined
 *     to that subtree so ``sponsio:../../etc/passwd`` can't escape.
 *   - Bare paths resolve relative to the directory containing the
 *     including yaml (or absolute as-is). Confined to the including
 *     directory's subtree.
 *   - Pack files must declare exactly one agent named ``"*"`` (the
 *     template). The pack's contract list is pulled out and stamped
 *     with ``packSource = spec`` for downstream attribution.
 *   - Nested includes are resolved recursively, with cycle detection.
 *
 * Lives separately from ``config-loader.ts`` so the loader's main
 * path stays focused on user-yaml schema; pack expansion is a
 * recursive sub-pass.
 */
import { readFileSync, existsSync, readdirSync } from "node:fs";
import { dirname, isAbsolute, join, resolve, relative } from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import type { SkippedItem } from "./config-loader.js";

// Lazily built on first use, with a fallback URL. On runtimes where
// `import.meta.url` is `undefined` (Cloudflare Workers, Deno Deploy),
// an eager `createRequire(undefined)` throws at module load and breaks
// the whole bundle even when YAML packs are never loaded. Deferring the
// call keeps Worker bundles that never touch packs from hitting it; the
// `?? noop` guard avoids the throw if it is reached. See config-loader.
let _requireCjs: ReturnType<typeof createRequire> | null = null;
function getRequireCjs(): ReturnType<typeof createRequire> {
  if (_requireCjs === null) {
    _requireCjs = createRequire(import.meta.url ?? "file:///sponsio-noop.js");
  }
  return _requireCjs;
}

interface YamlLib {
  parse: (src: string) => unknown;
}

function loadYamlLib(): YamlLib {
  return getRequireCjs()("yaml") as YamlLib;
}

/**
 * Locate the bundled ``contracts/`` directory shipped with this SDK
 * package. In a published install it lives at
 * ``node_modules/@sponsio/sdk/contracts/``; during local development
 * (``pnpm/npm link``, monorepo workspaces) it lives at
 * ``ts/packages/sdk/contracts/``. Walks up from this file's location
 * until it finds the directory.
 */
let _bundledContractsDir: string | null | undefined;
export function bundledContractsDir(): string | null {
  if (_bundledContractsDir !== undefined) return _bundledContractsDir;
  // Override hook for embedded / non-standard layouts.
  if (process.env.SPONSIO_CONTRACTS_DIR && existsSync(process.env.SPONSIO_CONTRACTS_DIR)) {
    _bundledContractsDir = resolve(process.env.SPONSIO_CONTRACTS_DIR);
    return _bundledContractsDir;
  }
  const here = dirname(fileURLToPath(import.meta.url));
  // dist/core/pack-loader.js → ../../contracts
  // src/core/pack-loader.ts  → ../../contracts (during dev)
  const candidates = [join(here, "..", "..", "contracts"), join(here, "..", "contracts")];
  for (const c of candidates) {
    if (existsSync(join(c, "core"))) {
      _bundledContractsDir = resolve(c);
      return _bundledContractsDir;
    }
  }
  _bundledContractsDir = null;
  return null;
}

function listAvailablePacks(root: string): string[] {
  const out: string[] = [];
  function walk(dir: string, prefix: string) {
    if (!existsSync(dir)) return;
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (entry.isDirectory()) {
        walk(join(dir, entry.name), prefix ? `${prefix}/${entry.name}` : entry.name);
      } else if (entry.isFile() && entry.name.endsWith(".yaml")) {
        const stem = entry.name.replace(/\.yaml$/, "");
        out.push(`sponsio:${prefix}/${stem}`);
      }
    }
  }
  walk(root, "");
  return out.sort();
}

export class IncludeResolveError extends Error {}

function resolveIncludeSpec(spec: string, baseDir: string): string {
  if (typeof spec !== "string" || !spec.trim()) {
    throw new IncludeResolveError(`include: entry must be a non-empty string, got ${JSON.stringify(spec)}`);
  }
  if (spec.startsWith("sponsio:")) {
    const rel = spec.slice("sponsio:".length).trim();
    if (!rel) {
      throw new IncludeResolveError(
        `include: bundled spec is empty: '${spec}' — expected e.g. 'sponsio:core/universal'`,
      );
    }
    const root = bundledContractsDir();
    if (!root) {
      throw new IncludeResolveError(
        `include: cannot locate bundled contracts/ directory for '${spec}'. ` +
          `Set SPONSIO_CONTRACTS_DIR if running from a non-standard layout.`,
      );
    }
    const filename = rel.endsWith(".yaml") ? rel : rel + ".yaml";
    const candidate = resolve(root, filename);
    // Confine to the bundled tree.
    const relFromRoot = relative(root, candidate);
    if (relFromRoot.startsWith("..") || isAbsolute(relFromRoot)) {
      throw new IncludeResolveError(
        `include: spec '${spec}' resolves outside the bundled contracts tree`,
      );
    }
    if (!existsSync(candidate)) {
      const available = listAvailablePacks(root);
      throw new IncludeResolveError(
        `include: bundled pack not found: '${spec}'. Available: ${available.join(", ")}`,
      );
    }
    return candidate;
  }
  // Bare filesystem include.
  let candidate: string;
  if (isAbsolute(spec)) {
    candidate = resolve(spec);
  } else {
    candidate = resolve(baseDir, spec);
    // Confine to baseDir subtree (mirror Python's safe_resolve).
    const relFromBase = relative(baseDir, candidate);
    if (relFromBase.startsWith("..") || isAbsolute(relFromBase)) {
      throw new IncludeResolveError(
        `include: spec '${spec}' resolves outside the including file's directory (${baseDir}). ` +
          `Use an absolute path or the bundled 'sponsio:...' form for cross-tree includes.`,
      );
    }
  }
  if (!existsSync(candidate)) {
    throw new IncludeResolveError(`include: file not found: ${candidate}`);
  }
  return candidate;
}

export interface PackContractItem {
  raw: unknown;
  packSource: string;
}

/**
 * Resolve a single ``include:`` entry to a flat list of contract
 * items (pre-projection). The caller projects each item through the
 * same pipeline as user-yaml contracts.
 */
export function loadPackContracts(
  spec: string,
  baseDir: string,
  seen: Set<string> = new Set(),
  skipped: SkippedItem[] = [],
): PackContractItem[] {
  if (seen.has(spec)) {
    const chain = [...seen, spec].join(" -> ");
    throw new IncludeResolveError(`include: cycle detected: ${chain}`);
  }
  let path: string;
  try {
    path = resolveIncludeSpec(spec, baseDir);
  } catch (e) {
    if (e instanceof IncludeResolveError) {
      skipped.push({ kind: "pack", detail: e.message });
      return [];
    }
    throw e;
  }
  const yamlLib = loadYamlLib();
  let raw: unknown;
  try {
    raw = yamlLib.parse(readFileSync(path, "utf-8"));
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    skipped.push({ kind: "pack", detail: `${spec}: invalid yaml in ${path}: ${msg}` });
    return [];
  }
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    skipped.push({ kind: "pack", detail: `${spec}: pack root must be a mapping` });
    return [];
  }
  const agents = (raw as Record<string, unknown>)["agents"];
  if (!agents || typeof agents !== "object" || Array.isArray(agents)) {
    skipped.push({
      kind: "pack",
      detail: `${spec}: pack must define an 'agents:' mapping with a single '*' agent`,
    });
    return [];
  }
  const agentKeys = Object.keys(agents as Record<string, unknown>);
  if (agentKeys.length !== 1 || agentKeys[0] !== "*") {
    skipped.push({
      kind: "pack",
      detail: `${spec}: pack must define exactly one agent named '*' (got ${JSON.stringify(agentKeys)})`,
    });
    return [];
  }
  const template = (agents as Record<string, unknown>)["*"];
  if (!template || typeof template !== "object" || Array.isArray(template)) {
    skipped.push({ kind: "pack", detail: `${spec}: '*' agent value must be a mapping` });
    return [];
  }

  const out: PackContractItem[] = [];

  // Recurse into nested includes first (matches Python order).
  const nested = (template as Record<string, unknown>)["include"];
  if (Array.isArray(nested)) {
    seen.add(spec);
    try {
      for (const child of nested) {
        if (typeof child !== "string") {
          skipped.push({
            kind: "pack",
            detail: `${spec}: nested include entry must be a string, got ${typeof child}`,
          });
          continue;
        }
        for (const item of loadPackContracts(child, dirname(path), seen, skipped)) {
          out.push(item);
        }
      }
    } finally {
      seen.delete(spec);
    }
  } else if (nested !== undefined) {
    skipped.push({ kind: "pack", detail: `${spec}: nested 'include' must be a list of strings` });
  }

  const contracts = (template as Record<string, unknown>)["contracts"];
  if (Array.isArray(contracts)) {
    for (const item of contracts) {
      out.push({ raw: item, packSource: spec });
    }
  } else if (contracts !== undefined) {
    skipped.push({ kind: "pack", detail: `${spec}: '*' agent's 'contracts' must be a list` });
  }

  return out;
}
