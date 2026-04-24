import { useState, useEffect, useCallback, useRef } from 'react';
import { useLocation } from 'react-router-dom';
import {
  listContracts,
  parseContracts,
  commitContracts,
  deleteContract,
  deleteGuarantee,
  getDiscoverySuggestions,
} from '../api/client';
import type { Contract, ContractParseResult, SuggestedContract } from '../types';
import Spinner from '../components/Spinner';
import ContractCard from '../components/ContractCard';
import PatternPicker from '../components/PatternPicker';
import PipelineNav from '../components/PipelineNav';
import PageError from '../components/PageError';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function groupByAgent(contracts: Contract[]): Map<string, Contract[]> {
  const map = new Map<string, Contract[]>();
  for (const c of contracts) {
    const list = map.get(c.agent_id) ?? [];
    list.push(c);
    map.set(c.agent_id, list);
  }
  return map;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
export default function Rulebook() {
  const location = useLocation();

  // --- Editor state ---
  // Initial values come from router state set by ScanAgent's "Apply contracts"
  // or "Define rulebook" handoff, so users land on Rulebook with the suggested
  // contracts already prefilled.
  type HandoffState = {
    nlText?: string;
    suggestedContracts?: SuggestedContract[];
    agentId?: string;
  };
  const handoff = (location.state as HandoffState | null) ?? {};
  const initialNlText = handoff.nlText
    ?? (handoff.suggestedContracts?.length
      ? handoff.suggestedContracts.map(s => s.nlText).join('\n')
      : '');
  const [agentId, setAgentId] = useState(handoff.agentId || 'bot');
  const [nlText, setNlText] = useState<string>(initialNlText);
  const [parseResult, setParseResult] = useState<ContractParseResult | null>(null);
  const [isParsing, setIsParsing] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // --- Suggestions panel state ---
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);
  const [suggestions, setSuggestions] = useState<SuggestedContract[]>([]);
  const [suggestionsLoading, setSuggestionsLoading] = useState(false);
  const suggestionsFetchedRef = useRef(false);

  // Load discovery suggestions lazily the first time the panel opens.
  // The ref guard prevents the effect from re-firing when the response is
  // empty (which would otherwise leave `suggestions.length === 0` forever).
  useEffect(() => {
    if (!suggestionsOpen || suggestionsFetchedRef.current) return;
    suggestionsFetchedRef.current = true;
    setSuggestionsLoading(true);
    getDiscoverySuggestions()
      .then(setSuggestions)
      .catch(() => setSuggestions([]))
      .finally(() => setSuggestionsLoading(false));
  }, [suggestionsOpen]);

  // --- Pattern library section state ---
  const [patternOpen, setPatternOpen] = useState(false);

  // --- Active contracts section state ---
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [loadingContracts, setLoadingContracts] = useState(true);
  const [contractsError, setContractsError] = useState<string | null>(null);
  const [contractsOpen, setContractsOpen] = useState(false);

  // ---------------------------------------------------------------------------
  // Load contracts on mount
  // ---------------------------------------------------------------------------
  const fetchContracts = useCallback(async () => {
    setLoadingContracts(true);
    setContractsError(null);
    try {
      const data = await listContracts();
      setContracts(data);
      setContractsOpen(data.length > 0);
    } catch (err) {
      setContractsError((err as Error).message ?? 'Failed to load contracts');
    } finally {
      setLoadingContracts(false);
    }
  }, []);

  useEffect(() => {
    fetchContracts();
  }, [fetchContracts]);

  // ---------------------------------------------------------------------------
  // Real-time parsing with 400 ms debounce
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (!nlText.trim()) {
      setParseResult(null);
      return;
    }
    debounceRef.current = setTimeout(async () => {
      setIsParsing(true);
      try {
        const result = await parseContracts(nlText);
        setParseResult(result);
      } catch {
        setParseResult(null);
      } finally {
        setIsParsing(false);
      }
    }, 400);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [nlText]);

  // ---------------------------------------------------------------------------
  // Commit contracts
  // ---------------------------------------------------------------------------
  const handleCommit = async () => {
    if (!nlText.trim() || !agentId.trim()) return;
    setIsSaving(true);
    setSaveError(null);
    setSaveSuccess(false);
    try {
      await commitContracts(agentId.trim(), nlText.trim());
      setSaveSuccess(true);
      await fetchContracts();
      setTimeout(() => setSaveSuccess(false), 3000);
    } catch (err) {
      setSaveError((err as Error).message ?? 'Failed to commit contracts');
    } finally {
      setIsSaving(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Suggestion actions
  // ---------------------------------------------------------------------------
  const handleAcceptSuggestion = (s: SuggestedContract) => {
    setNlText(prev => (prev ? `${prev}\n${s.nlText}` : s.nlText));
    setSuggestions(prev => prev.filter(d => d.id !== s.id));
  };

  const handleRejectSuggestion = (id: string) => {
    setSuggestions(prev => prev.filter(d => d.id !== id));
  };

  const handleEditSuggestion = (s: SuggestedContract) => {
    setNlText(prev => (prev ? `${prev}\n${s.nlText}` : s.nlText));
    setSuggestions(prev => prev.filter(d => d.id !== s.id));
  };

  const handleDeleteAgentContracts = async (targetAgentId: string) => {
    if (!window.confirm(`Delete all constraints for "${targetAgentId}"?`)) return;
    try {
      await deleteContract(targetAgentId);
      await fetchContracts();
    } catch (err) {
      setContractsError((err as Error).message ?? 'Failed to delete constraints');
    }
  };

  const handleDeleteSingleGuarantee = async (
    targetAgentId: string,
    flatIndex: number,
  ) => {
    // Fast click-and-gone: no confirm dialog. "Delete all" at the agent
    // level still confirms because that's destructive at wider scope.
    try {
      await deleteGuarantee(targetAgentId, flatIndex);
      await fetchContracts();
    } catch (err) {
      setContractsError((err as Error).message ?? 'Failed to delete constraint');
    }
  };

  /**
   * Load an agent's active constraints into the top editor for refinement.
   * Scrolls the editor into view. Commit will add new constraints on top;
   * the user can click "Delete all" first if they want to replace.
   */
  const editorRef = useRef<HTMLTextAreaElement | null>(null);
  const handleLoadIntoEditor = (targetAgentId: string, agentContracts: Contract[]) => {
    const lines: string[] = [];
    for (const c of agentContracts) {
      for (const g of c.guarantees) lines.push(g.desc);
    }
    setAgentId(targetAgentId);
    setNlText(lines.join('\n'));
    // Scroll the editor into view on next tick after state commits
    setTimeout(() => {
      editorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      editorRef.current?.focus();
    }, 50);
  };

  // ---------------------------------------------------------------------------
  // Grouped active contracts
  // ---------------------------------------------------------------------------
  const grouped = groupByAgent(contracts);

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------
  return (
    <div className="max-w-4xl mx-auto px-4 sm:px-6 py-8">
      {/* Page header */}
      <div className="mb-8">
        <h1 className="text-3xl font-display text-stone-900 dark:text-white mb-1">Contract Library</h1>
        <p className="text-sm text-zinc-600 dark:text-zinc-300 max-w-2xl">
          Your agents' behavioral contracts in natural language. Each line compiles to an LTL/FOL
          formula and is enforced at runtime. Onboard or upload in Scan to pre-populate this page;
          or hand-write from the pattern library below.
        </p>
      </div>

      {/* ================================================================
          SECTION 1 — Your Constraints (main editor)
      ================================================================ */}
      <section className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5 mb-6">
        <p className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium mb-4">Your Constraints</p>

        {/* Agent ID */}
        <div className="mb-4">
          <label className="block text-xs text-zinc-600 dark:text-zinc-300 mb-1.5">Agent ID</label>
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={agentId}
              onChange={e => setAgentId(e.target.value)}
              placeholder="bot"
              className="w-48 bg-surface-50 dark:bg-surface-800 border border-surface-200 dark:border-surface-800 rounded-lg px-3 py-1.5 text-sm text-zinc-700 dark:text-zinc-300 focus:outline-none focus:border-surface-600"
            />
            {agentId.trim() && (
              <span className="text-xs font-mono text-violet-400 bg-violet-500/10 px-2 py-0.5 rounded">
                {agentId.trim()}
              </span>
            )}
          </div>
        </div>

        {/* NL constraints textarea */}
        <div className="mb-4">
          <label className="block text-xs text-zinc-600 dark:text-zinc-300 mb-1.5">
            Natural language constraints <span className="text-[10px]">(one per line)</span>
          </label>
          <textarea
            ref={editorRef}
            value={nlText}
            onChange={e => setNlText(e.target.value)}
            rows={6}
            placeholder={'tool `send_email` at most 3 times per minute\ntool `confirm` must precede `delete`'}
            className="w-full bg-surface-50 dark:bg-surface-800 border border-surface-200 dark:border-surface-800 rounded-lg px-3 py-2 text-sm font-mono text-zinc-700 dark:text-zinc-300 focus:outline-none focus:border-surface-600 resize-y"
          />
        </div>

        {/* Parse feedback */}
        {(isParsing || parseResult) && (
          <div className="mb-4 flex flex-col gap-1.5">
            {isParsing && (
              <div className="flex items-center gap-2 text-xs text-zinc-600 dark:text-zinc-300">
                <Spinner />
                <span>Parsing…</span>
              </div>
            )}
            {!isParsing && parseResult && parseResult.constraints.map((c, i) => (
              <div key={i} className="flex items-start gap-2 text-xs">
                {c.ok ? (
                  <>
                    <svg className="w-3.5 h-3.5 mt-0.5 shrink-0 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                    </svg>
                    <span className="text-zinc-600 dark:text-zinc-300 font-mono truncate">{c.original_nl}</span>
                    <span className="shrink-0 text-violet-400 bg-violet-500/10 px-1.5 py-0.5 rounded font-mono">{c.pattern_name}</span>
                  </>
                ) : (
                  <>
                    <svg className="w-3.5 h-3.5 mt-0.5 shrink-0 text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M6 18L18 6M6 6l12 12" />
                    </svg>
                    <span className="text-red-400 font-mono truncate">{c.original_nl}</span>
                    {c.error && <span className="shrink-0 text-red-400/70">{c.error}</span>}
                  </>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Add from suggestions toggle */}
        <div className="mb-4">
          <button
            onClick={() => setSuggestionsOpen(o => !o)}
            className="flex items-center gap-1.5 text-xs text-zinc-600 dark:text-zinc-300 hover:text-stone-900 dark:hover:text-zinc-100 transition-colors"
          >
            <span>Add from suggestions</span>
            <span className="text-zinc-600 dark:text-zinc-300">({suggestions.length})</span>
            <svg
              className={`w-3.5 h-3.5 transition-transform ${suggestionsOpen ? 'rotate-180' : ''}`}
              fill="none" viewBox="0 0 24 24" stroke="currentColor"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </button>

          {suggestionsOpen && (
            <div className="mt-3">
              {suggestionsLoading ? (
                <div className="flex items-center justify-center py-4">
                  <Spinner />
                </div>
              ) : suggestions.length === 0 ? (
                <p className="text-sm text-zinc-600 dark:text-zinc-300 text-center py-4">
                  No suggestions yet. Run some traces through the Monitor first.
                </p>
              ) : (
                <div className="flex flex-col gap-2">
                  {suggestions.map(s => (
                    <div key={s.id} className="flex flex-col gap-1">
                      <ContractCard
                        nlText={s.nlText}
                        patternName={s.patternName}
                        status="proposed"
                        onAccept={() => handleAcceptSuggestion(s)}
                        onReject={() => handleRejectSuggestion(s.id)}
                        onEdit={() => handleEditSuggestion(s)}
                      />
                      <p className="text-[10px] text-zinc-600 dark:text-zinc-300 px-1">
                        Confidence {Math.round(s.confidence * 100)}% — {s.reason}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Save feedback */}
        {saveError && <p className="mb-3 text-xs text-red-400">{saveError}</p>}
        {saveSuccess && <p className="mb-3 text-xs text-emerald-400">Constraints committed successfully.</p>}

        {/* Commit button */}
        <button
          onClick={handleCommit}
          disabled={isSaving || !nlText.trim() || !agentId.trim()}
          className="px-5 py-1.5 bg-brand hover:bg-brand-400 text-black text-sm font-semibold rounded-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {isSaving ? 'Committing…' : 'Commit Constraints'}
        </button>
      </section>

      {/* ================================================================
          SECTION 2 — Pattern Library (collapsible)
      ================================================================ */}
      <section className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5 mb-6">
        <button
          className="flex items-center justify-between w-full text-left"
          onClick={() => setPatternOpen(o => !o)}
        >
          <p className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium">Pattern Library</p>
          <div className="flex items-center gap-2">
            {!patternOpen && (
              <span className="text-xs text-zinc-600 dark:text-zinc-300">Browse 17 patterns →</span>
            )}
            <svg
              className={`w-4 h-4 text-zinc-600 dark:text-zinc-300 transition-transform ${patternOpen ? 'rotate-180' : ''}`}
              fill="none" viewBox="0 0 24 24" stroke="currentColor"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </div>
        </button>

        {patternOpen && (
          <div className="mt-4">
            <PatternPicker
              onAdd={(nl) => setNlText(prev => (prev ? `${prev}\n${nl}` : nl))}
            />
          </div>
        )}
      </section>

      {/* ================================================================
          SECTION 3 — Active Constraints (collapsible)
      ================================================================ */}
      <section className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5 mb-6">
        <button
          className="flex items-center justify-between w-full text-left mb-1"
          onClick={() => setContractsOpen(o => !o)}
        >
          <p className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium">Active Constraints</p>
          <div className="flex items-center gap-2">
            {!loadingContracts && (() => {
              const totalGuarantees = contracts.reduce((s, c) => s + c.guarantees.length, 0);
              return (
                <span className="text-xs text-zinc-600 dark:text-zinc-300">
                  {totalGuarantees} constraint{totalGuarantees !== 1 ? 's' : ''}
                </span>
              );
            })()}
            <svg
              className={`w-4 h-4 text-zinc-600 dark:text-zinc-300 transition-transform ${contractsOpen ? 'rotate-180' : ''}`}
              fill="none" viewBox="0 0 24 24" stroke="currentColor"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </div>
        </button>

        {contractsOpen && (
          <div className="mt-4">
            {loadingContracts && (
              <div className="flex items-center justify-center h-20">
                <Spinner />
              </div>
            )}

            {contractsError && !loadingContracts && (
              <PageError message={contractsError} onRetry={fetchContracts} />
            )}

            {!loadingContracts && !contractsError && grouped.size === 0 && (
              <p className="text-sm text-zinc-600 dark:text-zinc-300 text-center py-6">
                No constraints defined yet. Use the editor above to add one.
              </p>
            )}

            {!loadingContracts && !contractsError && grouped.size > 0 && (
              <div className="flex flex-col gap-6">
                {Array.from(grouped.entries()).map(([agentIdKey, agentContracts]) => (
                  <div key={agentIdKey}>
                    <div className="flex items-center gap-2 mb-3">
                      <span className="text-xs font-mono text-violet-400 bg-violet-500/10 px-2 py-0.5 rounded">
                        {agentIdKey}
                      </span>
                      <span className="text-[10px] text-zinc-600 dark:text-zinc-300">
                        {agentContracts.reduce((sum, c) => sum + c.guarantees.length, 0)} constraint{agentContracts.reduce((sum, c) => sum + c.guarantees.length, 0) !== 1 ? 's' : ''}
                      </span>
                      <button
                        onClick={() => handleLoadIntoEditor(agentIdKey, agentContracts)}
                        className="ml-auto px-2 py-0.5 text-[10px] text-brand hover:bg-brand/10 rounded transition-colors"
                      >
                        Load into editor
                      </button>
                      <button
                        onClick={() => handleDeleteAgentContracts(agentIdKey)}
                        className="px-2 py-0.5 text-[10px] text-red-400 hover:bg-red-500/10 rounded transition-colors"
                      >
                        Delete all
                      </button>
                    </div>
                    <div className="flex flex-col gap-2">
                      {(() => {
                        // Flat index across all guarantees for this agent
                        let flat = 0;
                        return agentContracts.flatMap((c, ci) =>
                          c.guarantees.map((g, gi) => {
                            const currentFlat = flat++;
                            return (
                              <ContractCard
                                key={`${ci}-${gi}`}
                                nlText={g.desc}
                                patternName={g.pattern_name}
                                status={g.type === 'hard' ? 'verified' : 'proposed'}
                                onDelete={() =>
                                  handleDeleteSingleGuarantee(agentIdKey, currentFlat)
                                }
                              />
                            );
                          })
                        );
                      })()}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </section>

      {/* ================================================================
          Bottom navigation
      ================================================================ */}
      <PipelineNav
        prev={{ label: 'Back to Scan', path: '/scan' }}
        next={{ label: 'Generate SDK', path: '/integrate' }}
      />
    </div>
  );
}
