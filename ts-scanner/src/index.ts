/**
 * Public API for ``@sponsio/scan-ts``.
 *
 * Usage:
 *
 *   import { scan } from "@sponsio/scan-ts";
 *   const result = await scan(["src/**\/*.ts"]);
 *   console.log(JSON.stringify(result, null, 2));
 *
 * The emitted ``{ tools: [...] }`` object is directly consumable by
 * ``sponsio scan <file.json>`` — see the Python-side loader at
 * ``sponsio/discovery/extractors/tool_inventory.py``.
 */

import fg from "fast-glob";
import * as path from "path";
import { Project } from "ts-morph";
import { extractGenericTools } from "./extractors/generic";
import { extractLangChainTools } from "./extractors/langchain";
import { extractVercelTools } from "./extractors/vercel";
import type { Diagnostic, OpenAITool, ScanOutput, ToolProvenance } from "./types";

export type { OpenAITool, ScanOutput, ToolProvenance, Diagnostic };

export interface ScanOptions {
  /** Working directory used to resolve relative globs.  Defaults to ``process.cwd()``. */
  cwd?: string;
  /** Glob patterns to exclude (applied on top of defaults). */
  ignore?: string[];
}

const DEFAULT_IGNORE = [
  "**/node_modules/**",
  "**/dist/**",
  "**/build/**",
  "**/.next/**",
  "**/*.d.ts",
  "**/*.test.ts",
  "**/*.spec.ts",
];

/**
 * Scan one or more file globs for agent tool definitions.
 *
 * Only static AST inspection — we never execute user code, so
 * dynamically-constructed tools (computed names, schemas built at
 * runtime) won't be picked up.  For those, emit the tool inventory
 * by running the Node process directly and calling ``JSON.stringify``
 * on your tools array.
 */
export async function scan(
  patterns: string[],
  options: ScanOptions = {}
): Promise<ScanOutput> {
  const cwd = options.cwd ?? process.cwd();
  const files = await fg(patterns, {
    cwd,
    absolute: true,
    ignore: [...DEFAULT_IGNORE, ...(options.ignore ?? [])],
    onlyFiles: true,
  });

  const project = new Project({
    // Skip loading tsconfig.json — we only need to parse, not type-check.
    skipAddingFilesFromTsConfig: true,
    skipFileDependencyResolution: true,
    compilerOptions: {
      allowJs: true,
      noEmit: true,
      target: 99, // ESNext
      module: 99, // ESNext
    },
  });

  const tools: OpenAITool[] = [];
  const provenance: Record<string, ToolProvenance> = {};
  const diagnostics: Diagnostic[] = [];

  for (const file of files) {
    let source;
    try {
      source = project.addSourceFileAtPath(file);
    } catch (err) {
      diagnostics.push({
        filepath: file,
        line: 0,
        level: "warn",
        message: `failed to parse: ${(err as Error).message}`,
      });
      continue;
    }

    // Order matters: framework-specific extractors run first so the
    // dedupe layer below keeps their richer ``provenance.extractor``
    // label (``"vercel_ai"`` / ``"langchain_js"``) instead of the
    // generic fallback.
    const hits = [
      ...extractVercelTools(source),
      ...extractLangChainTools(source),
      ...extractGenericTools(source),
    ];

    for (const { tool, provenance: prov } of hits) {
      // Dedup: multiple extractors may match the same call site (e.g.
      // Vercel's ``tool({...})`` looks like LangGraph's
      // ``tool(fn, cfg)`` for a moment).  First-writer wins, keyed on
      // (name, filepath, line).
      const key = `${tool.function.name}@${path.relative(cwd, prov.filepath)}:${prov.line}`;
      if (provenance[key]) continue;
      tools.push(tool);
      provenance[key] = prov;
    }
  }

  return { tools, provenance, diagnostics };
}

/**
 * Re-exported after ``scan`` is defined to avoid a circular
 * `index → onboard → index` import while ``scan`` is still in TDZ.
 */
export {
  runOnboard,
  suggestDetNlContracts,
  buildFallbackPayload,
  detectFramework,
} from "./onboard";
export type { OnboardOptions, OnboardResult, TsOnboardFramework } from "./onboard";
