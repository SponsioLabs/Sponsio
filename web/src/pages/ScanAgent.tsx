import { useState, useCallback, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { getLatestScan, scanYamlUrl, clearScanHistory } from '../api/client';
import type { ScoreResponse, SuggestedContract } from '../types';
import PipelineNav from '../components/PipelineNav';

// ─── Types ────────────────────────────────────────────────────────────────────

type Tab = 'prompt' | 'cli' | 'history';

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
      try { localStorage.setItem('sponsio_scan_history', JSON.stringify(next)); } catch { /* quota / private mode */ }
      return next;
    });
  }, []);

  const clearScans = useCallback(() => {
    setHistory([]);
    try { localStorage.removeItem('sponsio_scan_history'); } catch { /* quota / private mode */ }
  }, []);

  return [history, addScan, clearScans];
}

// ─── Constants ────────────────────────────────────────────────────────────────

// ─── Helpers ─────────────────────────────────────────────────────────────────

// ─── Sub-components ──────────────────────────────────────────────────────────

interface ToggleSwitchProps {
  checked: boolean;
  onChange: (v: boolean) => void;
}

function ToggleSwitch({ checked, onChange }: ToggleSwitchProps) {
  return (
    <button
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
        checked ? 'bg-brand' : 'bg-surface-300 dark:bg-surface-700'
      }`}
    >
      <span
        className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
          checked ? 'translate-x-5' : 'translate-x-1'
        }`}
      />
    </button>
  );
}

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

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function ScanAgent() {
  const navigate = useNavigate();

  // Tab
  const [tab, setTab] = useState<Tab>('prompt');
  const [promptLang, setPromptLang] = useState<'python' | 'typescript'>('python');
  const [promptCopied, setPromptCopied] = useState(false);

  // Paste-tab state

  // Per-tab result state. Each input surface (paste form, upload, CLI push)
  // owns its own result so switching tabs doesn't bleed one tab's score into
  // another. The CLI tab result is restored via polling on mount (see below);
  // paste and upload are session-local form state.
  const [cliResult, setCliResult] = useState<ScoreResponse | null>(null);

  // Derived: the result for the currently active tab (null on history tab).
  // Prompt + CLI share a single backing store — both flows end in
  // `sponsio onboard --push` / `sponsio scan --push` → one dashboard
  // entry. Prompt tab is purely a guide with no input of its own, so
  // `cliResult` is the right place for the landed yaml.
  const result: ScoreResponse | null =
    tab === 'cli' || tab === 'prompt' ? cliResult : null;

  // Onboard / scan pushes persist YAML on the backend; the Download
  // button is wired to those two tabs.
  const resultHasYaml = tab === 'cli' || tab === 'prompt';

  const [loading, setLoading] = useState(false);

  // Per-tab suggestion state — same isolation rationale.
  type SuggestionStateMap = Record<string, boolean>;
  const [cliSuggestionStates, setCliSuggestionStates] = useState<SuggestionStateMap>({});
  const suggestionStates: SuggestionStateMap =
    tab === 'cli' || tab === 'prompt' ? cliSuggestionStates : {};
  const setSuggestionStates = (
    update: SuggestionStateMap | ((prev: SuggestionStateMap) => SuggestionStateMap),
  ) => {
    if (tab === 'cli' || tab === 'prompt') setCliSuggestionStates(update);
  };

  // Scan history
  const [scanHistory, , clearLocalScanHistory] = useScanHistory();
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
      setCliResult(null);
      setCliSuggestionStates({});
      setLoading(false);
      setClearingHistory(false);
      // Land on Prompt tab — History is now empty; the onboarding flow is
      // the natural next step for a first-time user.
      setTab('prompt');
    }
  };

  // Derived suggestions: straight from the backend's
  // `suggested_contracts` (CLI / Prompt onboard pushes). The page no
  // longer accepts hand-typed tools, so there's no heuristic-only
  // path any more.
  const suggestions: SuggestedContract[] = result
    ? result.deductions.map((d, i) => ({
        id: `ded-${i}-${d.check_id}`,
        nlText: d.suggested_contract,
        patternName: d.check_id.toLowerCase().replace(/_/g, ' '),
        confidence: 0.9,
        reason: d.description,
      }))
    : [];

  // ── CLI / Prompt tabs: poll for the latest scan pushed via `sponsio
  // scan --push`. Prompt tab is a thin guide that lives on top of the
  // same result store — a user who pasted the prompt into their coding
  // agent stays on Prompt, but the scan lands here regardless. It also
  // runs a single immediate fetch on mount so returning to either tab
  // restores the last pushed scan without waiting for the 3s interval.
  useEffect(() => {
    if (tab !== 'cli' && tab !== 'prompt') return;
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

  // ── Apply contracts ──
  const handleApplyContracts = () => {
    const accepted = suggestions.filter(s => suggestionStates[s.id] !== false);
    navigate('/rulebook', { state: { suggestedContracts: accepted } });
  };

  return (
    <div>
      {/* Page header */}
      <h1 className="text-3xl font-display text-stone-900 dark:text-white mb-1">Scan &amp; Onboard</h1>
      <p className="text-muted text-sm mb-6 max-w-2xl">
        Point your AI coding assistant at the repo, or run <code className="font-mono text-xs bg-surface-100 dark:bg-surface-800 px-1 rounded">sponsio onboard .</code>
        in your terminal — the generated <code className="font-mono text-xs bg-surface-100 dark:bg-surface-800 px-1 rounded">sponsio.yaml</code> lands
        here so you can review the inferred contracts before loading them into the Contract Library.
      </p>

      {/* ── Tab bar ── */}
      <div className="flex gap-1 border-b border-surface-200 dark:border-surface-800">
        {(['prompt', 'cli', 'history'] as Tab[]).map(t => {
          const labels: Record<Tab, string> = {
            prompt: 'Prompt',
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
        {tab === 'prompt' && 'Copy the prompt into Cursor, Claude Code, or Codex — your agent runs `sponsio onboard . --push` and the result streams into this page + the Contract Library.'}
        {tab === 'cli' && 'Three lines in your terminal. The generated sponsio.yaml lands here + in the Contract Library on the next step.'}
        {tab === 'history' && 'Every prompt / CLI push so far.'}
      </p>

      {/* ── Tab: Prompt (copy into Cursor / Claude Code / Codex) ── */}
      {tab === 'prompt' && (
        <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5 mb-6">
          <div className="flex items-center gap-2 mb-4">
            <p className="text-[10px] text-muted uppercase tracking-widest font-medium flex-1">Paste this into your coding agent</p>
            <div className="flex bg-surface-100 dark:bg-surface-800 rounded-lg p-0.5">
              {(['python', 'typescript'] as const).map(lang => (
                <button
                  key={lang}
                  onClick={() => setPromptLang(lang)}
                  className={`px-3 py-0.5 text-xs font-medium rounded transition-colors ${
                    promptLang === lang
                      ? 'bg-white dark:bg-surface-700 text-stone-900 dark:text-zinc-100 shadow-sm'
                      : 'text-zinc-600 dark:text-zinc-300 hover:text-stone-900 dark:hover:text-zinc-100'
                  }`}
                >
                  {lang === 'python' ? 'Python' : 'TypeScript'}
                </button>
              ))}
            </div>
          </div>

          {(() => {
            const prompt = promptLang === 'python' ? `Set up Sponsio (https://pypi.org/project/sponsio/) in this project.

    pip install sponsio
    sponsio onboard . --push


\`onboard\` detects the agent framework, writes sponsio.yaml in observe
mode, derives starter contracts, prints a 2-line patch to paste into
the agent entry file, and — with \`--push\` — surfaces the yaml in the
dashboard at http://localhost:3000/scan.

Nothing is blocked on day 1 (observe mode); every would-have-blocked
decision lands in ~/.sponsio/sessions/<agent_id>/*.jsonl.

After running, show me sponsio.yaml, the patch you applied, and any
\`sponsio doctor\` warnings.` : `Set up Sponsio (https://www.npmjs.com/package/@sponsio/sdk) in this TypeScript project.

    npm install @sponsio/sdk yaml
    npm install -D @sponsio/scan-ts
    npx sponsio-scan-ts onboard . --push

\`onboard\` static-scans my tools, writes sponsio.yaml in observe mode,
prints an integration snippet for the agent entry point, and — with
\`--push\` — surfaces the yaml in the local dashboard.

Nothing is blocked on day 1. The TS SDK honours SPONSIO_MODE and
logs would-have-blocked decisions to ~/.sponsio/sessions/<agent_id>/*.jsonl.

Show me sponsio.yaml and the patch you applied.`;
            return (
              <>
                <pre className="bg-surface-50 dark:bg-surface-950 border border-surface-200 dark:border-surface-800 rounded-lg p-4 text-xs font-mono text-stone-700 dark:text-zinc-300 whitespace-pre-wrap overflow-auto max-h-[420px]">
{prompt}
                </pre>
                <div className="flex items-center justify-between mt-3">
                  <p className="text-[11px] text-muted">
                    {result
                      ? 'A scan from `sponsio scan --push` has landed. Review it below, or switch to the CLI tab to see the latest push.'
                      : 'Once your coding agent runs `sponsio scan --push`, the result streams into this page automatically — switch to the CLI tab to see it land.'}
                  </p>
                  <button
                    onClick={async () => {
                      try {
                        await navigator.clipboard.writeText(prompt);
                        setPromptCopied(true);
                        setTimeout(() => setPromptCopied(false), 1800);
                      } catch {
                        /* clipboard denied */
                      }
                    }}
                    className="px-3 py-1.5 bg-brand hover:bg-brand-400 text-surface-950 text-xs font-semibold rounded-lg transition-colors"
                  >
                    {promptCopied ? '✓ Copied' : 'Copy prompt'}
                  </button>
                </div>
              </>
            );
          })()}
        </div>
      )}

      {/* ── Tab: CLI ── If the user is on this page, `sponsio serve` is
          already up — just the two lines that generate and surface
          the yaml. ── */}
      {tab === 'cli' && (
        <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5 mb-6 space-y-4">
          <p className="text-[10px] text-muted uppercase tracking-widest font-medium">Run in your terminal</p>
          <pre className="bg-surface-50 dark:bg-surface-950 border border-surface-200 dark:border-surface-800 rounded-lg px-4 py-3 font-mono text-xs text-stone-700 dark:text-zinc-300 overflow-x-auto whitespace-pre">
{`  pip install sponsio
  sponsio onboard . --push`}
          </pre>
          <p className="text-xs text-muted">
            <code className="font-mono bg-surface-100 dark:bg-surface-800 px-1 rounded">onboard --push</code> detects your
            framework, writes <code className="font-mono bg-surface-100 dark:bg-surface-800 px-1 rounded">sponsio.yaml</code> in
            observe mode, and surfaces it here + in the Contract Library. TypeScript project?
            Use <code className="font-mono bg-surface-100 dark:bg-surface-800 px-1 rounded">npx sponsio-scan-ts onboard . --push</code> (same flags).
          </p>
        </div>
      )}

      {/* CLI / Prompt tabs — hint about where the result lands */}
      {(tab === 'cli' || tab === 'prompt') && !result && (
        <div className="rounded-xl border border-dashed border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-6 mb-6 text-center">
          <p className="text-sm text-muted">
            Run <code className="font-mono bg-surface-100 dark:bg-surface-800 px-1 rounded">sponsio scan src/ --push</code>{' '}
            in your terminal — the result will appear in the panel below within a few seconds.
          </p>
        </div>
      )}
      {(tab === 'cli' || tab === 'prompt') && result && (
        <div className="rounded-xl border border-brand/30 bg-brand/5 p-3 mb-6">
          <p className="text-xs text-muted">
            <span className="font-medium text-stone-900 dark:text-zinc-100">Latest scan push</span>{' '}
            — auto-refreshing every 3s. Run <code className="font-mono bg-surface-100 dark:bg-surface-800 px-1 rounded">sponsio scan src/ --push</code> to replace it.
          </p>
        </div>
      )}

      {/* ═══════════════════════════════════════════════════
          Results Panel (shown once result is available)
      ═══════════════════════════════════════════════════ */}
      {result && (
        <div className="space-y-5">
          {/* ── Scan summary header ── lightweight banner: agent id,
              contract count, download + next-step button. No grade /
              score ring / risk percentages — those implied a
              rubric we don't explain. */}
          <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5 flex items-center gap-4 flex-wrap">
            <div className="flex-1 min-w-0">
              <p className="text-[10px] text-muted uppercase tracking-widest font-medium mb-1">Scan result</p>
              <p className="text-sm">
                <span className="font-mono text-stone-900 dark:text-zinc-100">{result.agent_name}</span>
                <span className="text-muted"> · {suggestions.length} suggested contract{suggestions.length !== 1 ? 's' : ''}</span>
              </p>
            </div>
            {resultHasYaml && result.id > 0 && (
              <a
                href={scanYamlUrl(result.id)}
                download={`sponsio-${result.id}.yaml`}
                className="px-3 py-1.5 text-xs rounded-lg border border-surface-200 dark:border-surface-800 text-muted hover:bg-surface-100 dark:hover:bg-surface-800 transition-colors font-mono"
              >
                Download sponsio.yaml
              </a>
            )}
          </div>

          {/* ── Suggested Contracts ── */}
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
              <p className="text-sm text-zinc-600 dark:text-zinc-300 mb-3">No scans yet. Run <code className="font-mono text-xs bg-surface-100 dark:bg-surface-800 px-1 rounded">sponsio onboard . --push</code> to see results here.</p>
              <button onClick={() => setTab('prompt')} className="text-sm text-zinc-600 dark:text-zinc-300 hover:text-stone-900 dark:hover:text-zinc-100 transition-colors">
                See how &rarr;
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
                      <span className="text-xs text-zinc-600 dark:text-zinc-300">{new Date(entry.scanned_at).toLocaleDateString()}</span>
                      <span className="text-xs text-zinc-600 dark:text-zinc-300">{entry.suggested_contracts.length} contract{entry.suggested_contracts.length !== 1 ? 's' : ''}</span>
                      <span className="text-zinc-600 dark:text-zinc-300 text-xs">{expandedHistoryIdx === idx ? '\u25BE' : '\u25B8'}</span>
                    </div>
                  </button>
                  {expandedHistoryIdx === idx && (
                    <div className="px-4 pb-4 border-t border-surface-200 dark:border-surface-800 pt-4">
                      <p className="text-xs text-zinc-600 dark:text-zinc-300 mb-3">Scanned {new Date(entry.scanned_at).toLocaleString()}</p>
                      {entry.suggested_contracts.length > 0 ? (
                        <>
                          <p className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium mb-2">Suggested Contracts</p>
                          {entry.suggested_contracts.map((c, ci) => (
                            <p key={ci} className="text-xs font-mono text-zinc-600 dark:text-zinc-300 mb-1">{c}</p>
                          ))}
                        </>
                      ) : (
                        <p className="text-xs text-zinc-600 dark:text-zinc-300">No contracts inferred.</p>
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
            {tab === 'prompt' && 'Copy the prompt above into your coding agent — it will run `sponsio onboard . --push` and the result streams in.'}
            {tab === 'cli' && 'Run the three-line block above in your terminal — results stream in.'}
          </p>
        </div>
      )}

      {/* Pipeline navigation — clicking "Load into Contract Library" carries
          the accepted suggestions from the current tab's scan result. */}
      <PipelineNav
        next={{
          label: 'Load into Contract Library',
          path: '/rulebook',
          onClick: handleApplyContracts,
        }}
      />
    </div>
  );
}
