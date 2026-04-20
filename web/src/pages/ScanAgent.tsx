import { useState, useCallback, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { scoreTools, uploadScan, getLatestScan, scanYamlUrl, clearScanHistory } from '../api/client';
import type { ScoreResponse, SuggestedContract } from '../types';
import Spinner from '../components/Spinner';
import ScoreRing from '../components/ScoreRing';
import FileUpload from '../components/FileUpload';
import PipelineNav from '../components/PipelineNav';

// ─── Types ────────────────────────────────────────────────────────────────────

interface ToolInput {
  name: string;
  description: string;
  parameters: string;
}

type Tab = 'paste' | 'upload' | 'cli' | 'history';

// ─── Scan history hook (localStorage-backed) ────────────────────────────────

interface HistoryEntry extends ScoreResponse {
  scanned_at: string;
}

function useScanHistory(): [
  HistoryEntry[],
  (scan: ScoreResponse) => void,
  () => void,
] {
  const [history, setHistory] = useState<HistoryEntry[]>(() => {
    try {
      const raw = localStorage.getItem('sponsio_scan_history');
      return raw ? JSON.parse(raw) : [];
    } catch {
      return [];
    }
  });

  const addScan = useCallback((scan: ScoreResponse) => {
    const entry: HistoryEntry = { ...scan, scanned_at: new Date().toISOString() };
    setHistory(prev => {
      const next = [entry, ...prev].slice(0, 50);
      try { localStorage.setItem('sponsio_scan_history', JSON.stringify(next)); } catch {}
      return next;
    });
  }, []);

  const clearScans = useCallback(() => {
    setHistory([]);
    try { localStorage.removeItem('sponsio_scan_history'); } catch {}
  }, []);

  return [history, addScan, clearScans];
}

// ─── Constants ────────────────────────────────────────────────────────────────

const EXAMPLE_TOOLS: ToolInput[] = [
  {
    name: 'execute_sql',
    description: 'Run arbitrary SQL queries on the production database',
    parameters: 'query:string,database:string',
  },
  {
    name: 'send_email',
    description: 'Send an email to any recipient on behalf of the user',
    parameters: 'to:string,subject:string,body:string,cc:string',
  },
  {
    name: 'read_file',
    description: 'Read the contents of a file from the local filesystem',
    parameters: 'path:string,encoding:string',
  },
];

const gradeColors: Record<string, string> = {
  'A+': 'text-emerald-400 bg-emerald-500/10',
  A: 'text-emerald-400 bg-emerald-500/10',
  B: 'text-blue-400 bg-blue-500/10',
  C: 'text-amber-400 bg-amber-500/10',
  D: 'text-orange-400 bg-orange-500/10',
  F: 'text-red-400 bg-red-500/10',
};

// Deduction category colours for the stacked bar
const deductionColors = [
  'bg-red-500',
  'bg-orange-400',
  'bg-amber-400',
  'bg-yellow-400',
  'bg-rose-500',
  'bg-pink-500',
];

// ─── Helpers ─────────────────────────────────────────────────────────────────

function parseParams(s: string): Record<string, string> {
  const result: Record<string, string> = {};
  for (const pair of s.split(',').map(p => p.trim()).filter(Boolean)) {
    const [k, v] = pair.split(':');
    if (k) result[k.trim()] = v?.trim() ?? 'string';
  }
  return result;
}

function generateSuggestions(tools: ToolInput[]): SuggestedContract[] {
  const suggestions: SuggestedContract[] = [];

  tools.forEach(tool => {
    const combined = `${tool.name} ${tool.description}`.toLowerCase();

    if (/delete|remove|destroy|drop|truncate/.test(combined)) {
      suggestions.push({
        id: `sug-confirm-${tool.name}`,
        nlText: `tool \`confirm_action\` must precede \`${tool.name}\``,
        patternName: 'must_confirm',
        confidence: 0.93,
        reason: `"${tool.name}" is a destructive operation that should require explicit confirmation.`,
      });
    }

    if (/payment|refund|charge|billing|invoice|transfer/.test(combined)) {
      suggestions.push({
        id: `sug-precede-${tool.name}`,
        nlText: `tool \`verify_identity\` must precede \`${tool.name}\``,
        patternName: 'must_precede',
        confidence: 0.91,
        reason: `"${tool.name}" involves financial operations and should be gated by identity verification.`,
      });
    }

    if (/send|email|notify|message|slack|webhook|sms/.test(combined)) {
      suggestions.push({
        id: `sug-rate-${tool.name}`,
        nlText: `tool \`${tool.name}\` at most 5 times per minute`,
        patternName: 'rate_limit',
        confidence: 0.87,
        reason: `"${tool.name}" sends outbound communications and should be rate-limited to prevent spam.`,
      });
    }

    if (/read|write|file|path|disk|filesystem|storage/.test(combined)) {
      suggestions.push({
        id: `sug-scope-${tool.name}`,
        nlText: `tool \`${tool.name}\` must not use arguments matching "\\.\\./"`,
        patternName: 'scope_limit',
        confidence: 0.89,
        reason: `"${tool.name}" accesses the filesystem and should be scoped to prevent path traversal.`,
      });
    }

    // Always suggest bounded_retry for every tool
    suggestions.push({
      id: `sug-retry-${tool.name}`,
      nlText: `tool \`${tool.name}\` retried at most 3 times on failure`,
      patternName: 'bounded_retry',
      confidence: 0.75,
      reason: `All tools should have a bounded retry policy to prevent infinite loops on transient failures.`,
    });
  });

  // Deduplicate by id
  const seen = new Set<string>();
  return suggestions.filter(s => {
    if (seen.has(s.id)) return false;
    seen.add(s.id);
    return true;
  });
}

// ─── Sub-components ──────────────────────────────────────────────────────────

function PatternBadge({ name }: { name: string }) {
  const colors: Record<string, string> = {
    must_confirm: 'bg-red-500/10 text-red-400',
    must_precede: 'bg-orange-500/10 text-orange-400',
    rate_limit: 'bg-blue-500/10 text-blue-400',
    scope_limit: 'bg-violet-500/10 text-violet-400',
    bounded_retry: 'bg-surface-200 dark:bg-surface-700 text-muted',
  };
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-[10px] font-mono font-semibold ${colors[name] ?? 'bg-surface-700 text-muted'}`}>
      {name}
    </span>
  );
}

interface ToggleSwitchProps {
  checked: boolean;
  onChange: (v: boolean) => void;
}

function ToggleSwitch({ checked, onChange }: ToggleSwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors focus:outline-none ${
        checked ? 'bg-brand' : 'bg-surface-300 dark:bg-surface-700'
      }`}
    >
      <span
        className={`pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow transition-transform ${
          checked ? 'translate-x-4' : 'translate-x-0'
        }`}
      />
    </button>
  );
}

// ─── Shared code block (matches Integrate page styling) ─────────────────────

interface CodeBlockProps {
  id: string;
  cmd: string;
  copied: string | null;
  onCopy: (cmd: string, id: string) => void;
}

function CodeBlock({ id, cmd, copied, onCopy }: CodeBlockProps) {
  return (
    <div className="relative">
      <pre className="bg-surface-50 dark:bg-surface-900 rounded-xl px-4 py-3 font-mono text-sm text-zinc-600 dark:text-zinc-300 overflow-x-auto whitespace-pre border border-surface-200 dark:border-surface-800">
        <span className="text-brand select-none">$ </span>
        {cmd}
      </pre>
      <button
        onClick={() => onCopy(cmd, id)}
        className="absolute top-2 right-2 px-2.5 py-1 text-xs rounded-lg border border-surface-200 dark:border-surface-700 bg-surface-100 dark:bg-surface-800 text-zinc-600 dark:text-zinc-300 hover:text-stone-900 dark:hover:text-zinc-100 hover:border-surface-500 transition-colors font-mono"
      >
        {copied === id ? 'Copied!' : 'Copy'}
      </button>
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function ScanAgent() {
  const navigate = useNavigate();

  // Tab
  const [tab, setTab] = useState<Tab>('paste');

  // Paste-tab state
  const [agentName, setAgentName] = useState('my_agent');
  const [displayName, setDisplayName] = useState('');
  const [isPublic, setIsPublic] = useState(false);
  const [tools, setTools] = useState<ToolInput[]>([{ name: '', description: '', parameters: '' }]);

  // Upload-tab state
  const [uploadFile, setUploadFile] = useState<File | null>(null);

  // CLI-tab state
  const [cliCopied, setCliCopied] = useState<string | null>(null);

  // Per-tab result state. Each input surface (paste form, upload, CLI push)
  // owns its own result so switching tabs doesn't bleed one tab's score into
  // another. The CLI tab result is restored via polling on mount (see below);
  // paste and upload are session-local form state.
  const [pasteResult, setPasteResult] = useState<ScoreResponse | null>(null);
  const [uploadResult, setUploadResult] = useState<ScoreResponse | null>(null);
  const [cliResult, setCliResult] = useState<ScoreResponse | null>(null);

  // Derived: the result for the currently active tab (null on history tab)
  const result: ScoreResponse | null =
    tab === 'paste' ? pasteResult
    : tab === 'upload' ? uploadResult
    : tab === 'cli' ? cliResult
    : null;

  // Only upload and CLI pushes persist YAML on the backend, so only those
  // tabs should show a Download button.
  const resultHasYaml = tab === 'upload' || tab === 'cli';

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // Per-tab suggestion state — same isolation rationale.
  type SuggestionStateMap = Record<string, boolean>;
  const [pasteSuggestionStates, setPasteSuggestionStates] = useState<SuggestionStateMap>({});
  const [uploadSuggestionStates, setUploadSuggestionStates] = useState<SuggestionStateMap>({});
  const [cliSuggestionStates, setCliSuggestionStates] = useState<SuggestionStateMap>({});
  const suggestionStates: SuggestionStateMap =
    tab === 'paste' ? pasteSuggestionStates
    : tab === 'upload' ? uploadSuggestionStates
    : tab === 'cli' ? cliSuggestionStates
    : {};
  const setSuggestionStates = (
    update: SuggestionStateMap | ((prev: SuggestionStateMap) => SuggestionStateMap),
  ) => {
    if (tab === 'paste') setPasteSuggestionStates(update);
    else if (tab === 'upload') setUploadSuggestionStates(update);
    else if (tab === 'cli') setCliSuggestionStates(update);
  };

  // Leaderboard publish state
  const [publishAnon, setPublishAnon] = useState(true);
  const [publishName, setPublishName] = useState('');
  const [publishDone, setPublishDone] = useState(false);

  // Badge visibility
  const [showBadge, setShowBadge] = useState(false);

  // Scan history
  const [scanHistory, addScanToHistory, clearLocalScanHistory] = useScanHistory();
  const [expandedHistoryIdx, setExpandedHistoryIdx] = useState<number | null>(null);
  const [clearingHistory, setClearingHistory] = useState(false);

  const handleClearHistory = async () => {
    if (!window.confirm('Clear all scan history? This removes both local paste/upload history and backend scan records.')) return;
    setClearingHistory(true);
    try {
      await clearScanHistory();
    } catch {
      // non-fatal: local clear still runs below
    } finally {
      // Wipe every piece of in-memory scan state so the user gets a clean
      // slate after clearing. Without this, stale per-tab results from
      // before the clear would linger and make the UI feel broken.
      clearLocalScanHistory();
      setExpandedHistoryIdx(null);
      setPasteResult(null);
      setUploadResult(null);
      setCliResult(null);
      setPasteSuggestionStates({});
      setUploadSuggestionStates({});
      setCliSuggestionStates({});
      setUploadFile(null);
      setError('');
      setLoading(false);
      setClearingHistory(false);
      // Land on Paste tab — History is now empty and gives no obvious next step.
      setTab('paste');
    }
  };

  // Derived suggestions. For paste-tab scans we generate heuristic suggestions
  // from the user-typed tool inputs. For upload / CLI scans the backend returns
  // real suggested_contracts derived from the actual source code, so we build
  // suggestion cards from those instead.
  const suggestions: SuggestedContract[] = result
    ? (tab === 'paste'
        ? generateSuggestions(tools.filter(t => t.name.trim()))
        : result.deductions.map((d, i) => ({
            id: `ded-${i}-${d.check_id}`,
            nlText: d.suggested_contract,
            patternName: d.check_id.toLowerCase().replace(/_/g, ' '),
            confidence: 0.9,
            reason: d.description,
          })))
    : [];

  // ── CLI tab: poll for the latest scan pushed via `sponsio scan --push` ──
  // This effect only runs while the CLI tab is active, and only touches
  // `cliResult` — paste/upload tabs are unaffected. It also runs a single
  // immediate fetch on mount so returning to the CLI tab restores the
  // last pushed scan without waiting for the 3s interval.
  useEffect(() => {
    if (tab !== 'cli') return;
    let cancelled = false;

    const sync = async () => {
      try {
        const latest = await getLatestScan('cli');
        if (cancelled || !latest) return;
        // Only update if this is a newer scan than what we're showing
        setCliResult(prev => {
          if (prev && prev.id === latest.id) return prev;
          const next: ScoreResponse = {
            id: latest.id,
            agent_name: latest.agent_name,
            score: latest.score,
            grade: latest.grade,
            deductions: latest.deductions as ScoreResponse['deductions'],
            suggested_contracts: latest.suggested_contracts,
            badge_url: '',
          };
          const states: Record<string, boolean> = {};
          latest.deductions.forEach((d, i) => {
            states[`ded-${i}-${(d as { check_id: string }).check_id}`] = true;
          });
          setCliSuggestionStates(states);
          return next;
        });
      } catch {
        // Silent — polling failures shouldn't disrupt the UI
      }
    };

    sync();  // immediate fetch on tab activation
    const id = setInterval(sync, 3000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [tab]);

  // ── Paste-tab helpers ──
  const updateTool = (i: number, field: keyof ToolInput, value: string) => {
    setTools(prev => prev.map((t, j) => (j === i ? { ...t, [field]: value } : t)));
  };
  const addTool = () => setTools(prev => [...prev, { name: '', description: '', parameters: '' }]);
  const removeTool = (i: number) => setTools(prev => prev.filter((_, j) => j !== i));
  const loadExample = () => {
    setTools(EXAMPLE_TOOLS);
    setAgentName('demo_agent');
    setDisplayName('Demo Agent');
  };

  // ── API calls ──
  const handleScore = async () => {
    const validTools = tools.filter(t => t.name.trim());
    if (!validTools.length) return;
    setLoading(true);
    setError('');
    setPasteResult(null);
    try {
      const res = await scoreTools({
        agent_name: agentName || 'my_agent',
        tools: validTools.map(t => ({
          name: t.name,
          description: t.description,
          parameters: parseParams(t.parameters),
        })),
        display_name: displayName || agentName || 'my_agent',
        is_public: isPublic,
      });
      setPasteResult(res);
      addScanToHistory(res);
      // Initialise all paste suggestions as accepted
      const newStates: Record<string, boolean> = {};
      generateSuggestions(validTools).forEach(s => { newStates[s.id] = true; });
      setPasteSuggestionStates(newStates);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Scoring failed');
    } finally {
      setLoading(false);
    }
  };

  const handleUploadScan = async () => {
    if (!uploadFile) return;
    setLoading(true);
    setError('');
    setUploadResult(null);
    try {
      const res = await uploadScan(uploadFile);
      setUploadResult(res);
      addScanToHistory(res);
      // Initialise suggestions state for the derived upload suggestions.
      const newStates: Record<string, boolean> = {};
      res.deductions.forEach((d, i) => {
        newStates[`ded-${i}-${d.check_id}`] = true;
      });
      setUploadSuggestionStates(newStates);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Upload scan failed');
    } finally {
      setLoading(false);
    }
  };

  // ── CLI copy ──
  const handleCopyCli = (cmd: string, id: string) => {
    navigator.clipboard.writeText(cmd).catch(() => {});
    setCliCopied(id);
    setTimeout(() => setCliCopied(null), 2000);
  };

  // ── Apply contracts ──
  const handleApplyContracts = () => {
    const accepted = suggestions.filter(s => suggestionStates[s.id] !== false);
    navigate('/rulebook', { state: { suggestedContracts: accepted } });
  };

  // ── Stacked bar data ──
  const totalDeducted = result ? result.deductions.reduce((sum, d) => sum + d.points_lost, 0) : 0;

  return (
    <div>
      {/* Page header */}
      <h1 className="text-3xl font-display text-stone-900 dark:text-white mb-1">Scan Your Agent</h1>
      <p className="text-muted text-sm mb-6 max-w-2xl">
        Analyze your agent's tool definitions and configuration to assess safety risks, generate a risk
        report, and get actionable contract suggestions.
      </p>

      {/* ── Tab bar ── */}
      <div className="flex gap-1 border-b border-surface-200 dark:border-surface-800">
        {(['paste', 'upload', 'cli', 'history'] as Tab[]).map(t => {
          const labels: Record<Tab, string> = {
            paste: 'Paste Tools',
            upload: 'Upload File',
            cli: 'CLI',
            history: `History${scanHistory.length ? ` (${scanHistory.length})` : ''}`,
          };
          return (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px ${
                tab === t
                  ? 'border-brand text-stone-900 dark:text-zinc-100'
                  : 'border-transparent text-zinc-600 dark:text-zinc-300 hover:text-stone-900 dark:hover:text-stone-900 dark:hover:text-zinc-100'
              }`}
            >
              {labels[t]}
            </button>
          );
        })}
      </div>

      {/* Tab subtitle — explains what the active tab does */}
      <p className="text-xs text-muted mt-3 mb-5">
        {tab === 'paste' && 'Manually enter tool definitions (no code needed) and get an instant safety score. Good for evaluating a design you haven\'t written yet.'}
        {tab === 'upload' && 'Upload a Python source file or sponsio.yaml. The backend runs the same AST analyzer and scorer used by the CLI.'}
        {tab === 'cli' && 'Run the scanner in your terminal. Best for large codebases or when you already have a project on disk.'}
        {tab === 'history' && 'Past scans from this browser (paste + upload) plus any CLI runs persisted to the backend.'}
      </p>

      {/* ── Tab: Paste Tool Definitions ── */}
      {tab === 'paste' && (
        <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5 mb-6">
          <p className="text-[10px] text-muted uppercase tracking-widest font-medium mb-4">Tool Definitions</p>

          {/* Agent meta */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-4">
            <div>
              <label className="text-xs text-muted mb-1 block">Agent Name</label>
              <input
                value={agentName}
                onChange={e => setAgentName(e.target.value)}
                placeholder="my_agent"
                className="w-full bg-surface-50 dark:bg-surface-800 border border-surface-200 dark:border-surface-800 rounded-lg px-3 py-1.5 text-sm text-stone-900 dark:text-zinc-100 focus:outline-none focus:border-brand"
              />
            </div>
            <div>
              <label className="text-xs text-muted mb-1 block">Display Name (for leaderboard)</label>
              <input
                value={displayName}
                onChange={e => setDisplayName(e.target.value)}
                placeholder="My Agent"
                className="w-full bg-surface-50 dark:bg-surface-800 border border-surface-200 dark:border-surface-800 rounded-lg px-3 py-1.5 text-sm text-stone-900 dark:text-zinc-100 focus:outline-none focus:border-brand"
              />
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm text-muted mb-5 cursor-pointer">
            <input
              type="checkbox"
              checked={isPublic}
              onChange={e => setIsPublic(e.target.checked)}
              className="rounded border-surface-600 text-brand focus:ring-brand"
            />
            Show on public leaderboard
          </label>

          {/* Tool rows */}
          <div className="space-y-3 mb-4">
            {tools.map((t, i) => (
              <div
                key={i}
                className="rounded-lg border border-surface-200 dark:border-surface-800 p-3 space-y-2"
              >
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted font-mono shrink-0">Tool {i + 1}</span>
                  <div className="flex-1" />
                  {tools.length > 1 && (
                    <button
                      onClick={() => removeTool(i)}
                      className="text-xs text-red-400 hover:text-red-300 transition-colors"
                    >
                      Remove
                    </button>
                  )}
                </div>
                <input
                  placeholder="Tool name (e.g. execute_sql)"
                  value={t.name}
                  onChange={e => updateTool(i, 'name', e.target.value)}
                  className="w-full bg-surface-50 dark:bg-surface-800 border border-surface-200 dark:border-surface-800 rounded px-3 py-1.5 text-sm font-mono text-stone-900 dark:text-zinc-100 focus:outline-none focus:border-brand"
                />
                <input
                  placeholder="Description of what this tool does"
                  value={t.description}
                  onChange={e => updateTool(i, 'description', e.target.value)}
                  className="w-full bg-surface-50 dark:bg-surface-800 border border-surface-200 dark:border-surface-800 rounded px-3 py-1.5 text-sm text-stone-900 dark:text-zinc-100 focus:outline-none focus:border-brand"
                />
                <input
                  placeholder="Parameters (e.g. query:string, limit:number)"
                  value={t.parameters}
                  onChange={e => updateTool(i, 'parameters', e.target.value)}
                  className="w-full bg-surface-50 dark:bg-surface-800 border border-surface-200 dark:border-surface-800 rounded px-3 py-1.5 text-xs font-mono text-muted focus:outline-none focus:border-brand"
                />
              </div>
            ))}
          </div>

          {/* Actions row */}
          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={addTool}
              className="px-3 py-1.5 text-xs border border-surface-200 dark:border-surface-800 text-muted rounded-lg hover:bg-surface-100 dark:hover:bg-surface-800 dark:hover:text-stone-900 dark:hover:text-white transition-colors"
            >
              + Add Tool
            </button>
            <button
              onClick={loadExample}
              className="px-3 py-1.5 text-xs text-zinc-600 dark:text-zinc-300 hover:text-stone-900 dark:hover:text-zinc-100 transition-colors"
            >
              Load Example
            </button>
            <div className="flex-1" />
            <button
              onClick={handleScore}
              disabled={loading || !tools.some(t => t.name.trim())}
              className="px-5 py-1.5 bg-brand hover:bg-brand-400 text-surface-950 text-sm font-semibold rounded-lg transition-colors disabled:opacity-40 flex items-center gap-2"
            >
              {loading ? <Spinner size="sm" /> : null}
              {loading ? 'Scanning…' : 'Scan Tools'}
            </button>
          </div>
          {error && <p className="mt-3 text-sm text-red-400">{error}</p>}
        </div>
      )}

      {/* ── Tab: Upload File ── */}
      {tab === 'upload' && (
        <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5 mb-6">
          <p className="text-[10px] text-muted uppercase tracking-widest font-medium mb-4">Upload source file, zip, or sponsio.yaml</p>
          <FileUpload
            accept=".py,.yaml,.yml,.zip"
            onFile={f => setUploadFile(f)}
            label="Drop a file here"
            sublabel="Accepts .py (single file), .zip (project archive, scans all .py inside), or sponsio.yaml. Max 10 MB."
          />
          {uploadFile && (
            <p className="mt-3 text-xs text-muted font-mono">
              Selected: <span className="text-stone-900 dark:text-zinc-100">{uploadFile.name}</span>
              {' · '}{(uploadFile.size / 1024).toFixed(1)} KB
            </p>
          )}
          <div className="flex justify-end mt-4">
            <button
              onClick={handleUploadScan}
              disabled={loading || !uploadFile}
              className="px-5 py-1.5 bg-brand hover:bg-brand-400 text-surface-950 text-sm font-semibold rounded-lg transition-colors disabled:opacity-40 flex items-center gap-2"
            >
              {loading ? <Spinner size="sm" /> : null}
              {loading ? 'Scanning…' : 'Scan file'}
            </button>
          </div>
          {error && <p className="mt-3 text-sm text-red-400">{error}</p>}
        </div>
      )}

      {/* ── Tab: CLI Command Reference ── */}
      {tab === 'cli' && (
        <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5 mb-6 space-y-5">
          <div>
            <p className="text-[10px] text-muted uppercase tracking-widest font-medium mb-2">Basic usage</p>
            <p className="text-sm text-muted mb-3">
              Point the scanner at a directory or a single file. Output is written to stdout unless you pass <code className="font-mono text-xs bg-surface-100 dark:bg-surface-800 px-1 rounded">--out</code>.
            </p>
            <CodeBlock
              id="basic"
              cmd="sponsio scan src/"
              copied={cliCopied}
              onCopy={handleCopyCli}
            />
          </div>

          <div>
            <p className="text-[10px] text-muted uppercase tracking-widest font-medium mb-2">Write YAML to disk</p>
            <CodeBlock
              id="out"
              cmd="sponsio scan src/ -o sponsio.yaml"
              copied={cliCopied}
              onCopy={handleCopyCli}
            />
            <p className="text-xs text-muted mt-2">
              Add <code className="font-mono bg-surface-100 dark:bg-surface-800 px-1 rounded">--append</code> to merge into an existing file instead of overwriting.
            </p>
          </div>

          <div>
            <p className="text-[10px] text-muted uppercase tracking-widest font-medium mb-2">LLM-assisted inference</p>
            <p className="text-sm text-muted mb-3">
              Adds constraints the rule-based pass can't infer. Requires <code className="font-mono text-xs bg-surface-100 dark:bg-surface-800 px-1 rounded">GEMINI_API_KEY</code> or <code className="font-mono text-xs bg-surface-100 dark:bg-surface-800 px-1 rounded">OPENAI_API_KEY</code>.
            </p>
            <CodeBlock
              id="llm"
              cmd="sponsio scan src/ --llm"
              copied={cliCopied}
              onCopy={handleCopyCli}
            />
          </div>

          <div>
            <p className="text-[10px] text-muted uppercase tracking-widest font-medium mb-2">Extract constraints from policy docs</p>
            <p className="text-sm text-muted mb-3">
              Pass one or more <code className="font-mono text-xs bg-surface-100 dark:bg-surface-800 px-1 rounded">.md</code> / <code className="font-mono text-xs bg-surface-100 dark:bg-surface-800 px-1 rounded">.txt</code> policy documents. The tool inventory from your code is used as context so the LLM knows what tools exist.
            </p>
            <CodeBlock
              id="policy"
              cmd="sponsio scan src/ --policy docs/security.md --llm"
              copied={cliCopied}
              onCopy={handleCopyCli}
            />
          </div>

          <div>
            <p className="text-[10px] text-muted uppercase tracking-widest font-medium mb-2">All options</p>
            <pre className="bg-surface-50 dark:bg-surface-900 rounded-xl px-4 py-3 font-mono text-xs text-zinc-600 dark:text-zinc-300 overflow-x-auto whitespace-pre border border-surface-200 dark:border-surface-800">
{`  paths                   One or more files or directories to scan (required)
  -a, --agent TEXT        Agent ID for the generated config (default: "agent")
  --llm                   Enable LLM-based constraint inference
  -m, --model TEXT        LLM model name (default: auto-detect)
  --provider [gemini|openai]
                          LLM provider (default: auto-detect from env)
  -o, --out PATH          Write sponsio.yaml to this path instead of stdout
  --append                Merge into existing file instead of overwriting
  -p, --policy PATH       Policy document (.md/.txt) to extract constraints from
                          (can be passed multiple times)
  --push / --no-push      Auto-push result to the local dashboard (default: on)
  --push-url URL          Dashboard URL (default: http://127.0.0.1:8000)`}
            </pre>
          </div>
        </div>
      )}

      {/* CLI tab — hint about where the result lands */}
      {tab === 'cli' && !result && (
        <div className="rounded-xl border border-dashed border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-6 mb-6 text-center">
          <p className="text-sm text-muted">
            Run <code className="font-mono bg-surface-100 dark:bg-surface-800 px-1 rounded">sponsio scan src/</code>{' '}
            in your terminal — the result will appear in the panel below within a few seconds.
          </p>
        </div>
      )}
      {tab === 'cli' && result && (
        <div className="rounded-xl border border-brand/30 bg-brand/5 p-3 mb-6">
          <p className="text-xs text-muted">
            <span className="font-medium text-stone-900 dark:text-zinc-100">Latest CLI scan</span>{' '}
            — auto-refreshing every 3s. Run <code className="font-mono bg-surface-100 dark:bg-surface-800 px-1 rounded">sponsio scan src/</code> to push a new result.
          </p>
        </div>
      )}

      {/* ═══════════════════════════════════════════════════
          Results Panel (shown once result is available)
      ═══════════════════════════════════════════════════ */}
      {result && (
        <div className="space-y-5">
          {/* ── 1. Score Header ── */}
          <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5">
            <div className="flex items-center justify-between mb-4">
              <p className="text-[10px] text-muted uppercase tracking-widest font-medium">Safety Score</p>
              {resultHasYaml && result.id > 0 && (
                <a
                  href={scanYamlUrl(result.id)}
                  download={`sponsio-${result.id}.yaml`}
                  className="px-2.5 py-1 text-xs rounded-lg bg-brand text-black font-semibold hover:opacity-90 transition-opacity font-mono"
                >
                  Download sponsio.yaml
                </a>
              )}
            </div>
            <div className="flex flex-col sm:flex-row items-center gap-6">
              <ScoreRing score={result.score} grade={result.grade} size="lg" />
              <div>
                <div className="flex items-center gap-3 mb-2">
                  <span className="text-4xl font-bold font-mono text-stone-900 dark:text-white">
                    {result.score}
                  </span>
                  <span
                    className={`px-3 py-1 rounded-lg text-xl font-bold ${
                      gradeColors[result.grade] ?? 'text-muted bg-surface-100 dark:bg-surface-800'
                    }`}
                  >
                    {result.grade}
                  </span>
                </div>
                <p className="text-sm text-muted">
                  Agent:{' '}
                  <span className="font-mono text-stone-900 dark:text-zinc-100">
                    {result.agent_name}
                  </span>
                </p>
                {result.deductions.length > 0 && (
                  <p className="text-xs text-red-400 mt-1">
                    {result.deductions.length} risk{result.deductions.length !== 1 ? 's' : ''} found
                    &nbsp;·&nbsp;{totalDeducted} pts deducted
                  </p>
                )}
              </div>
            </div>
          </div>

          {/* ── 2. Risk Breakdown ── */}
          {result.deductions.length > 0 && (
            <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5">
              <p className="text-[10px] text-muted uppercase tracking-widest font-medium mb-4">
                Risk Breakdown
              </p>

              {/* Stacked bar */}
              <div className="flex h-3 rounded-full overflow-hidden mb-5 bg-surface-100 dark:bg-surface-800">
                {result.deductions.map((d, i) => {
                  const pct = totalDeducted > 0 ? (d.points_lost / totalDeducted) * 100 : 0;
                  return (
                    <div
                      key={d.check_id}
                      title={`${d.description}: -${d.points_lost} pts`}
                      className={`h-full transition-all ${deductionColors[i % deductionColors.length]}`}
                      style={{ width: `${pct}%` }}
                    />
                  );
                })}
              </div>

              {/* Deduction list */}
              <div className="space-y-2">
                {result.deductions.map((d, i) => (
                  <div
                    key={d.check_id}
                    className="rounded-lg border border-red-500/20 bg-red-500/5 px-3 py-2.5"
                  >
                    <div className="flex items-start gap-2 mb-1">
                      <span
                        className={`mt-0.5 w-2.5 h-2.5 rounded-sm shrink-0 ${
                          deductionColors[i % deductionColors.length]
                        }`}
                      />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-sm font-bold text-red-400 shrink-0">
                            -{d.points_lost} pts
                          </span>
                          <span className="text-sm text-stone-900 dark:text-zinc-100">
                            {d.description}
                          </span>
                        </div>
                        {d.affected_tools.length > 0 && (
                          <p className="text-xs text-muted font-mono mt-0.5">
                            Affected: {d.affected_tools.join(', ')}
                          </p>
                        )}
                        {d.suggested_contract && (
                          <p className="text-xs text-emerald-400 mt-1">
                            Suggested: {d.suggested_contract}
                          </p>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ── 3. Suggested Contracts ── */}
          {suggestions.length > 0 && (
            <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5">
              <div className="flex items-center justify-between mb-4">
                <p className="text-[10px] text-muted uppercase tracking-widest font-medium">
                  Recommended Contracts
                </p>
                <span className="text-xs text-muted">
                  {Object.values(suggestionStates).filter(Boolean).length} of {suggestions.length} accepted
                </span>
              </div>

              <div className="space-y-2 mb-4">
                {suggestions.map(s => {
                  const accepted = suggestionStates[s.id] !== false;
                  return (
                    <div
                      key={s.id}
                      className={`rounded-lg border px-3 py-2.5 transition-colors ${
                        accepted
                          ? 'border-emerald-500/30 bg-emerald-500/5'
                          : 'border-surface-200 dark:border-surface-800 bg-surface-50 dark:bg-surface-800 opacity-60'
                      }`}
                    >
                      <div className="flex items-start gap-3">
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-mono text-stone-900 dark:text-zinc-100 mb-1">
                            {s.nlText}
                          </p>
                          <div className="flex items-center gap-2 flex-wrap">
                            <PatternBadge name={s.patternName} />
                            <span className="text-[10px] text-muted">{s.reason}</span>
                          </div>
                        </div>
                        <div className="flex items-center gap-2 shrink-0">
                          <span className="text-[10px] text-muted hidden sm:block">
                            {accepted ? 'Accept' : 'Skip'}
                          </span>
                          <ToggleSwitch
                            checked={accepted}
                            onChange={v =>
                              setSuggestionStates(prev => ({ ...prev, [s.id]: v }))
                            }
                          />
                          <button
                            className="text-[10px] text-muted hover:text-stone-700 dark:hover:text-stone-200 border border-surface-200 dark:border-surface-700 rounded px-1.5 py-0.5 transition-colors"
                            title="Edit contract text"
                            onClick={() => {
                              // Edit in-place is a future feature; navigate to contracts
                              navigate('/rulebook', { state: { suggestedContracts: [s] } });
                            }}
                          >
                            Edit
                          </button>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>

              <button
                onClick={handleApplyContracts}
                className="px-5 py-1.5 bg-brand hover:bg-brand-400 text-surface-950 text-sm font-semibold rounded-lg transition-colors"
              >
                Apply Selected Contracts →
              </button>
            </div>
          )}

          {/* ── 4. Leaderboard Push ── */}
          <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5">
            <p className="text-[10px] text-muted uppercase tracking-widest font-medium mb-4">
              Publish to Leaderboard
            </p>
            {publishDone ? (
              <p className="text-sm text-emerald-400">
                Published! Your score is now visible on the leaderboard.
              </p>
            ) : (
              <div className="space-y-3">
                <label className="flex items-center gap-2 text-sm text-muted cursor-pointer">
                  <ToggleSwitch checked={publishAnon} onChange={setPublishAnon} />
                  Publish anonymously
                </label>
                {!publishAnon && (
                  <div>
                    <label className="text-xs text-muted mb-1 block">Display name</label>
                    <input
                      value={publishName}
                      onChange={e => setPublishName(e.target.value)}
                      placeholder="e.g. Acme Corp Agent"
                      className="w-full bg-surface-50 dark:bg-surface-800 border border-surface-200 dark:border-surface-800 rounded-lg px-3 py-1.5 text-sm text-stone-900 dark:text-zinc-100 focus:outline-none focus:border-brand"
                    />
                  </div>
                )}
                <button
                  onClick={() => setPublishDone(true)}
                  className="px-5 py-1.5 bg-brand hover:bg-brand-400 text-surface-950 text-sm font-semibold rounded-lg transition-colors"
                >
                  Publish
                </button>
              </div>
            )}
          </div>

          {/* ── 5. Badge ── */}
          <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5">
            <div className="flex items-center justify-between mb-4">
              <p className="text-[10px] text-muted uppercase tracking-widest font-medium">README Badge</p>
              <button
                onClick={() => setShowBadge(v => !v)}
                className="text-xs text-zinc-600 dark:text-zinc-300 hover:text-stone-900 dark:hover:text-zinc-100 transition-colors"
              >
                {showBadge ? 'Hide' : 'Show embed code'}
              </button>
            </div>
            {showBadge && (
              <div className="rounded-lg bg-stone-950 border border-surface-200 dark:border-surface-800 p-4 space-y-3">
                <div className="flex items-center justify-center">
                  <img
                    src={result.badge_url}
                    alt={`Sponsio Safety Score ${result.grade}`}
                    className="h-6"
                    onError={e => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
                  />
                </div>
                <div>
                  <p className="text-[10px] text-muted mb-1">Markdown:</p>
                  <code className="text-[11px] font-mono text-stone-700 dark:text-zinc-300 select-all block break-all">
                    {`![Sponsio Safety Score](${result.badge_url})`}
                  </code>
                </div>
              </div>
            )}
            {!showBadge && (
              <p className="text-xs text-muted">
                Add a live safety score badge to your agent's README.
              </p>
            )}
          </div>

          {/* ── 6. Export Report ── */}
          <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5">
            <p className="text-[10px] text-muted uppercase tracking-widest font-medium mb-4">
              Export Report
            </p>
            <p className="text-sm text-muted mb-3">
              Download a full risk report with all deductions and recommended contracts.
            </p>
            <button
              onClick={() => alert('PDF export coming soon')}
              className="px-5 py-1.5 bg-brand hover:bg-brand-400 text-surface-950 text-sm font-semibold rounded-lg transition-colors"
            >
              Download Report (PDF)
            </button>
          </div>
        </div>
      )}

      {/* ── Tab: History ── */}
      {tab === 'history' && (
        <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5 mb-6">
          <div className="flex items-center justify-between mb-4">
            <p className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium">
              Scan History
            </p>
            {scanHistory.length > 0 && (
              <button
                onClick={handleClearHistory}
                disabled={clearingHistory}
                className="px-2 py-1 text-[10px] text-red-400 hover:bg-red-500/10 rounded transition-colors disabled:opacity-40"
              >
                {clearingHistory ? 'Clearing…' : 'Clear history'}
              </button>
            )}
          </div>
          {scanHistory.length === 0 ? (
            <div className="py-8 text-center">
              <p className="text-sm text-zinc-600 dark:text-zinc-300 mb-3">No scans yet. Run your first scan to see results here.</p>
              <button onClick={() => setTab('paste')} className="text-sm text-zinc-600 dark:text-zinc-300 hover:text-stone-900 dark:hover:text-zinc-100 transition-colors">
                Go to Paste Tools &rarr;
              </button>
            </div>
          ) : (
            <div className="space-y-1">
              {scanHistory.map((entry, idx) => (
                <div key={`${entry.id}-${idx}`} className="rounded-lg border border-surface-200 dark:border-surface-800 overflow-hidden">
                  <button
                    onClick={() => setExpandedHistoryIdx(expandedHistoryIdx === idx ? null : idx)}
                    className="w-full text-left px-4 py-3 hover:bg-surface-50 dark:hover:bg-surface-800 transition-colors"
                  >
                    <div className="flex items-center gap-3">
                      <span className="text-sm font-mono text-zinc-700 dark:text-zinc-300 flex-1 truncate">{entry.agent_name}</span>
                      <span className="text-sm font-bold font-mono text-stone-900 dark:text-zinc-100">{entry.score}</span>
                      <span className={`px-2 py-0.5 rounded text-xs font-bold ${gradeColors[entry.grade] ?? 'text-zinc-600 dark:text-zinc-300 bg-surface-100 dark:bg-surface-800'}`}>{entry.grade}</span>
                      <span className="text-xs text-zinc-600 dark:text-zinc-300">{new Date(entry.scanned_at).toLocaleDateString()}</span>
                      <span className="text-xs text-zinc-600 dark:text-zinc-300">{entry.deductions.length} deductions</span>
                      <span className="text-zinc-600 dark:text-zinc-300 text-xs">{expandedHistoryIdx === idx ? '\u25BE' : '\u25B8'}</span>
                    </div>
                  </button>
                  {expandedHistoryIdx === idx && (
                    <div className="px-4 pb-4 border-t border-surface-200 dark:border-surface-800 pt-4">
                      <div className="flex items-center gap-4 mb-4">
                        <ScoreRing score={entry.score} grade={entry.grade} size="sm" />
                        <div>
                          <p className="text-xs text-zinc-600 dark:text-zinc-300">{entry.deductions.length} risk{entry.deductions.length !== 1 ? 's' : ''} found</p>
                          <p className="text-xs text-zinc-600 dark:text-zinc-300">Scanned {new Date(entry.scanned_at).toLocaleString()}</p>
                        </div>
                      </div>
                      {entry.deductions.length > 0 && (
                        <div className="space-y-1.5">
                          {entry.deductions.map((d, di) => (
                            <div key={di} className="text-xs flex items-center gap-2">
                              <span className="text-red-400 font-bold shrink-0">-{d.points_lost}</span>
                              <span className="text-zinc-600 dark:text-zinc-300 truncate">{d.description}</span>
                            </div>
                          ))}
                        </div>
                      )}
                      {entry.suggested_contracts.length > 0 && (
                        <div className="mt-3 pt-3 border-t border-surface-200 dark:border-surface-800">
                          <p className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium mb-2">Suggested Contracts</p>
                          {entry.suggested_contracts.map((c, ci) => (
                            <p key={ci} className="text-xs font-mono text-zinc-600 dark:text-zinc-300 mb-1">{c}</p>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Empty state — no result yet and not loading (not shown on history tab) */}
      {!result && !loading && tab !== 'history' && (
        <div className="rounded-xl border border-dashed border-surface-300 dark:border-surface-700 p-10 text-center mt-2">
          <div className="w-12 h-12 rounded-full bg-surface-100 dark:bg-surface-800 flex items-center justify-center mx-auto mb-4">
            <svg className="w-6 h-6 text-zinc-600 dark:text-zinc-300" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={1.5}
                d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"
              />
            </svg>
          </div>
          <p className="text-zinc-600 dark:text-zinc-300 text-sm mb-1">Results will appear here after your scan</p>
          <p className="text-zinc-600 dark:text-zinc-300 text-xs">
            {tab === 'paste' && 'Define your tools above and click "Scan Tools"'}
            {tab === 'upload' && 'Upload a config file and click "Scan"'}
            {tab === 'cli' && 'Run the CLI command in your terminal and results will stream in'}
          </p>
        </div>
      )}

      {/* Pipeline navigation — clicking "Define your rulebook" carries the
          accepted suggestions from the current tab's scan result. */}
      <PipelineNav
        next={{
          label: 'Define your rulebook',
          path: '/rulebook',
          onClick: handleApplyContracts,
        }}
      />
    </div>
  );
}
