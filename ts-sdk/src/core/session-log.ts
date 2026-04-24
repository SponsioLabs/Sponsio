/**
 * Per-session JSONL event log for the TypeScript runtime.
 *
 * Mirrors ``sponsio/runtime/session_log.py`` byte-for-byte on the
 * record schema so ``sponsio report --agent <id>`` reads
 * Python-produced and TS-produced logs with the same reducer.
 *
 * Layout:
 *
 *     ~/.sponsio/sessions/<sanitized_agent_id>/
 *         <YYYYMMDD_HHMMSS>_<pid>.jsonl
 *
 * Rotation on startup:
 *   * files older than `keepDays` days are unlinked
 *   * if the remaining total exceeds `maxMB` megabytes, the oldest
 *     remaining files are unlinked until the budget is met
 *
 * Stdlib-only — no filesystem dep beyond `node:fs`, `node:os`, `node:path`.
 * Errors during rotation / writes are swallowed: a log failure must
 * never take down the agent.
 */

import {
  appendFileSync,
  existsSync,
  mkdirSync,
  readdirSync,
  statSync,
  unlinkSync,
} from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { pid } from "node:process";

const DEFAULT_KEEP_DAYS = 7;
const DEFAULT_MAX_MB = 100;

// Allow letters, digits, dot (``agent.v2``), dash, underscore, colon
// (``team:bot``). Disallow path separators and control characters.
// Matches the Python regex in ``session_log._SAFE_AGENT_ID_RE``.
const SAFE_AGENT_ID_RE = /[^A-Za-z0-9._:\-]/g;

function sanitizeAgentId(agentId: string): string {
  if (!agentId) return "_unknown";
  const cleaned = agentId.replace(SAFE_AGENT_ID_RE, "_").replace(/^[._]+|[._]+$/g, "");
  if (!cleaned || cleaned === "." || cleaned === "..") return "_unknown";
  return cleaned.slice(0, 128);
}

function defaultBaseDir(): string {
  return join(homedir(), ".sponsio", "sessions");
}

function pad2(n: number): string {
  return n < 10 ? `0${n}` : `${n}`;
}

function timestampStamp(d: Date = new Date()): string {
  return (
    `${d.getFullYear()}` +
    `${pad2(d.getMonth() + 1)}` +
    `${pad2(d.getDate())}` +
    "_" +
    `${pad2(d.getHours())}` +
    `${pad2(d.getMinutes())}` +
    `${pad2(d.getSeconds())}`
  );
}

/**
 * Prune old / oversized session files under ``baseDir``.
 *
 * Walks two levels (``<baseDir>/<agent_id>/*.jsonl``) since that's
 * how session files are laid out in practice. Any IO error is
 * swallowed silently.
 */
export function rotateSessions(
  baseDir: string,
  keepDays: number = DEFAULT_KEEP_DAYS,
  maxMB: number = DEFAULT_MAX_MB,
): string[] {
  const removed: string[] = [];
  if (!existsSync(baseDir)) return removed;

  const now = Date.now();
  const cutoffMs = now - keepDays * 86400 * 1000;

  type FileMeta = { path: string; mtime: number; size: number };
  const files: FileMeta[] = [];

  let agentDirs: string[] = [];
  try {
    agentDirs = readdirSync(baseDir);
  } catch {
    return removed;
  }

  for (const entry of agentDirs) {
    const dir = join(baseDir, entry);
    let inner: string[] = [];
    try {
      inner = readdirSync(dir);
    } catch {
      continue;
    }
    for (const f of inner) {
      if (!f.endsWith(".jsonl")) continue;
      const p = join(dir, f);
      let st;
      try {
        st = statSync(p);
      } catch {
        continue;
      }
      const mtime = st.mtimeMs;
      if (mtime < cutoffMs) {
        try {
          unlinkSync(p);
          removed.push(p);
        } catch {
          // ignore
        }
        continue;
      }
      files.push({ path: p, mtime, size: st.size });
    }
  }

  const budget = maxMB * 1024 * 1024;
  let total = files.reduce((acc, f) => acc + f.size, 0);
  if (total > budget) {
    files.sort((a, b) => a.mtime - b.mtime);
    for (const f of files) {
      if (total <= budget) break;
      try {
        unlinkSync(f.path);
        removed.push(f.path);
        total -= f.size;
      } catch {
        // ignore
      }
    }
  }
  return removed;
}

export interface SessionRecord {
  /** Unix seconds, to match the Python writer (float). */
  ts: number;
  agent_id: string;
  /** ``allow`` | ``block`` | ``observe_log``. */
  action: "allow" | "block" | "observe_log";
  pipeline: "det";
  constraint: string;
  result: {
    action: "allow" | "block" | "observe_log";
    message: string;
  };
}

export interface SessionLoggerOptions {
  baseDir?: string;
  keepDays?: number;
  maxMB?: number;
  /** Fixed stamp for deterministic tests. */
  timestamp?: string;
  /** Skip startup rotation (tests). */
  skipRotation?: boolean;
}

/**
 * Append-only JSONL logger. Instances own one file for the lifetime
 * of the process; repeated guards from the same pid reuse it.
 */
export class SessionLogger {
  readonly agentId: string;
  readonly path: string;

  constructor(agentId: string, options: SessionLoggerOptions = {}) {
    this.agentId = agentId;
    const base = options.baseDir ?? defaultBaseDir();
    const safe = sanitizeAgentId(agentId);

    try {
      mkdirSync(base, { recursive: true });
    } catch {
      // ignore — write will fail loudly below if truly broken
    }

    const agentDir = join(base, safe);
    try {
      mkdirSync(agentDir, { recursive: true });
    } catch {
      // ignore
    }

    if (!options.skipRotation) {
      rotateSessions(
        base,
        options.keepDays ?? DEFAULT_KEEP_DAYS,
        options.maxMB ?? DEFAULT_MAX_MB,
      );
    }

    const stamp = options.timestamp ?? timestampStamp();
    this.path = join(agentDir, `${stamp}_${pid}.jsonl`);
  }

  log(record: SessionRecord): void {
    let line: string;
    try {
      line = JSON.stringify(record);
    } catch {
      // Never break the agent because of log serialization.
      return;
    }
    try {
      appendFileSync(this.path, line + "\n", "utf-8");
    } catch {
      // Disk full, perms, etc. — silently drop.
    }
  }
}
