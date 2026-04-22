import { useState, useEffect, useRef, useCallback } from 'react';
import {
  listDemos,
  getDemoScenarios,
  getDemoContracts,
  simulateAction,
  resetPlayground,
  seedDemo,
  getSpans,
  reVerifyContract,
  getLiveStatus,
  runLiveDemo,
} from '../api/client';
import type { DemoInfo, DemoScenario, DemoStep, SpanNode, ReVerifyResponse, TraceStep } from '../types';
import Spinner from '../components/Spinner';
import TraceTimeline from '../components/TraceTimeline';

/* ------------------------------------------------------------------ */
/*  Convert demo data -> unified TraceStep[]                           */
/* ------------------------------------------------------------------ */

function demoStepsToTraceSteps(scenario: DemoScenario): TraceStep[] {
  return scenario.steps
    .filter(s => s.type === 'tool_call')
    .map((s: DemoStep) => ({
      event_type: (s.event_type ?? 'tool_call') as TraceStep['event_type'],
      label: s.action!,
      source: s.source,
      target: s.target,
      isViolation: !!s.expect_blocked,
    }));
}

function spansToTraceSteps(spans: SpanNode[], scenario?: DemoScenario): TraceStep[] {
  const toolSteps = scenario?.steps.filter(s => s.type === 'tool_call') ?? [];
  return spans.map((span, i) => {
    const ds = toolSteps[i];
    const isViol = span.blocked || span.status === 'violated' ||
      (span.children ?? []).some(c => c.span_type === 'sponsio.contract_check' && c.status === 'violated');
    return {
      event_type: (ds?.event_type ?? 'tool_call') as TraceStep['event_type'],
      label: span.action ?? ds?.action ?? '?',
      source: ds?.source,
      target: ds?.target,
      isViolation: isViol,
    };
  });
}

/* ------------------------------------------------------------------ */
/*  Main Demos page                                                    */
/* ------------------------------------------------------------------ */

export default function Playground() {
  const [demos, setDemos] = useState<DemoInfo[]>([]);
  const [activeDemo, setActiveDemo] = useState('');
  const [loadingDemo, setLoadingDemo] = useState(false);
  const [scenarios, setScenarios] = useState<DemoScenario[]>([]);
  const [activeScenario, setActiveScenario] = useState(0);
  const [contractLines, setContractLines] = useState<string[]>([]);
  const [spans, setSpans] = useState<SpanNode[]>([]);
  const [running, setRunning] = useState(false);
  const [done, setDone] = useState(false);
  const abortRef = useRef(false);
  const [mode, setMode] = useState<'mock' | 'live'>('mock');
  const [liveReady, setLiveReady] = useState<boolean | null>(null);
  const [liveModel, setLiveModel] = useState('');
  const [liveError, setLiveError] = useState('');
  const [depsInstalled, setDepsInstalled] = useState(true);
  const [liveWithout, setLiveWithout] = useState<TraceStep[]>([]);
  const [liveWith, setLiveWith] = useState<TraceStep[]>([]);
  const [liveSpans, setLiveSpans] = useState<SpanNode[]>([]);

  useEffect(() => { listDemos().then(setDemos).catch(() => {}); }, []);
  useEffect(() => {
    getLiveStatus().then(s => { setLiveReady(s.ready); setLiveModel(s.model); setDepsInstalled(s.dependencies_installed); }).catch(() => {});
  }, []);

  const refreshTrace = useCallback(async () => {
    const s = await getSpans();
    setSpans(s);
  }, []);

  const loadDemo = useCallback(async (demoId: string) => {
    abortRef.current = true;
    setActiveDemo(demoId); setLoadingDemo(true);
    setSpans([]); setDone(false); setRunning(false); setActiveScenario(0); setLiveError('');
    try {
      await seedDemo(demoId); await resetPlayground();
      const [sc, ct] = await Promise.all([getDemoScenarios(), getDemoContracts()]);
      setScenarios(sc);
      setContractLines((ct.hard ?? []).map((c: Record<string, unknown>) => c.nl as string).filter(Boolean));
    } catch { /* silent */ }
    finally { setLoadingDemo(false); abortRef.current = false; }
  }, []);

  const scenario = scenarios[activeScenario];
  const delay = (ms: number) => new Promise<void>(r => setTimeout(r, ms));

  const withoutSteps = scenario ? demoStepsToTraceSteps(scenario) : [];
  const withSteps = spansToTraceSteps(spans, scenario);

  const runScenario = async () => {
    if (!scenario || running) return;
    setRunning(true); setDone(false); abortRef.current = false;
    setSpans([]); setLiveError('');
    setLiveWithout([]); setLiveWith([]); setLiveSpans([]);
    await seedDemo(activeDemo); await resetPlayground();

    if (mode === 'live') {
      try {
        const res = await runLiveDemo(activeDemo);
        const resAny = res as Record<string, unknown>;
        const without = (resAny.without as Array<Record<string, unknown>> ?? []).map(s => ({
          event_type: (s.event_type ?? 'tool_call') as TraceStep['event_type'],
          label: s.label as string,
          source: s.source as string | undefined,
          target: s.target as string | undefined,
          isViolation: s.isViolation as boolean,
        }));
        const with_ = (resAny.with_ as Array<Record<string, unknown>> ?? []).map(s => ({
          event_type: (s.event_type ?? 'tool_call') as TraceStep['event_type'],
          label: s.label as string,
          source: s.source as string | undefined,
          target: s.target as string | undefined,
          isViolation: s.isViolation as boolean,
        }));
        setLiveWithout(without);
        setLiveWith(with_);
        setLiveSpans((resAny.spans as SpanNode[]) ?? []);
      } catch (e) {
        setLiveError(e instanceof Error ? e.message : 'Live demo failed');
      }
      setDone(true); setRunning(false);
      return;
    }

    for (const step of scenario.steps) {
      if (abortRef.current) break;
      if (step.type === 'tool_call') {
        try { await simulateAction({ agent_id: step.agent_id!, action: step.action! }); } catch { /* optional demo */ }
        await refreshTrace(); await delay(500);
      } else { await delay(300); }
    }
    await refreshTrace(); setDone(true); setRunning(false);
  };

  const activeSpans = mode === 'live' ? liveSpans : spans;
  const violationCount = mode === 'live'
    ? liveWith.filter(s => s.isViolation).length
    : activeSpans.filter(s =>
        s.blocked || s.status === 'violated' ||
        (s.children ?? []).some(c => c.span_type === 'sponsio.contract_check' && c.status === 'violated')
      ).length;

  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <h1 className="text-3xl font-display text-stone-900 dark:text-white">Playground</h1>
        <div className="flex items-center gap-1 bg-surface-100 dark:bg-surface-800 rounded-lg p-0.5">
          <button onClick={() => setMode('mock')}
            className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${mode === 'mock' ? 'bg-white dark:bg-surface-900 text-stone-900 dark:text-white shadow-sm' : 'text-muted hover:text-stone-900 dark:hover:text-white'}`}>
            Mock
          </button>
          <button onClick={() => setMode('live')}
            className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${mode === 'live' ? 'bg-white dark:bg-surface-900 text-stone-900 dark:text-white shadow-sm' : 'text-muted hover:text-stone-900 dark:hover:text-white'}`}>
            Real LLM
          </button>
        </div>
      </div>
      <p className="text-muted text-sm mb-1">
        {mode === 'mock'
          ? 'Interactive demonstration of Sponsio\'s runtime enforcement. See contracts in action.'
          : 'Real LLM agent (LangGraph + Gemini) with Sponsio enforcement.'}
      </p>
      {mode === 'live' && liveReady && <p className="text-xs text-muted mb-4 flex items-center gap-1.5"><span className="w-1.5 h-1.5 rounded-full bg-brand inline-block" /> Model: {liveModel}</p>}
      {mode === 'live' && !liveReady && (
        <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 px-4 py-3 mb-4">
          <p className="text-sm font-medium text-amber-400 mb-1">Setup required</p>
          {!depsInstalled && (
            <p className="text-xs text-amber-400/70 mb-1">
              1. Install deps: <code className="bg-surface-100 dark:bg-surface-800 px-1 rounded">pip install langgraph langchain-google-genai</code>
            </p>
          )}
          <p className="text-xs text-amber-400/70">
            {depsInstalled ? 'Add' : '2. Add'} your API key to <code className="bg-surface-100 dark:bg-surface-800 px-1 rounded">.env</code> and restart the API server:
          </p>
          <pre className="mt-1.5 text-[11px] bg-surface-100 dark:bg-surface-800 rounded px-2 py-1 text-amber-400 font-mono">GOOGLE_API_KEY=your-key-here</pre>
        </div>
      )}
      {mode === 'mock' && <div className="mb-4" />}

      {/* Demo picker */}
      <div className="flex gap-2 mb-4 flex-wrap">
        {demos.map(d => (
          <button key={d.id} onClick={() => loadDemo(d.id)} disabled={loadingDemo || running}
            className={`px-4 py-2.5 rounded-xl text-left transition-all border ${activeDemo === d.id ? 'bg-brand/5 border-brand/30' : 'border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 hover:border-brand/20 disabled:opacity-40'}`}>
            <p className={`text-sm font-medium ${activeDemo === d.id ? 'text-stone-900 dark:text-white' : 'text-stone-900 dark:text-zinc-100'}`}>{d.title}</p>
            <p className="text-[10px] text-muted">{d.subtitle}</p>
          </button>
        ))}
        {loadingDemo && <Spinner size="sm" />}
      </div>

      {/* Contracts display */}
      {contractLines.length > 0 && (
        <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 px-4 py-3 mb-3">
          <span className="text-xs font-medium text-muted uppercase tracking-wider">Contracts: </span>
          <span className="text-xs text-stone-700 dark:text-zinc-300 font-mono">{contractLines.join(' | ')}</span>
        </div>
      )}

      {/* Scenario tabs + run */}
      {scenario && (
        <div className="flex items-center gap-2 mb-3">
          {scenarios.length > 1 && scenarios.map((s, i) => (
            <button key={s.id} onClick={() => { setActiveScenario(i); setSpans([]); setDone(false); }} disabled={running}
              className={`px-3 py-1.5 rounded text-xs transition-colors ${activeScenario === i ? 'bg-surface-200 dark:bg-surface-800 text-stone-900 dark:text-white' : 'text-muted hover:text-stone-900 dark:hover:text-white disabled:opacity-40'}`}>
              {s.title}
            </button>
          ))}
          <div className="flex-1" />
          {!running && !done && <button onClick={runScenario} className="px-5 py-1.5 bg-brand hover:bg-brand-400 text-surface-950 text-sm font-semibold rounded-lg transition-colors">{mode === 'live' ? 'Run (Real LLM)' : 'Run'}</button>}
          {running && <div className="flex items-center gap-1.5 text-xs text-muted"><span className="w-2 h-2 rounded-full bg-brand animate-pulse" /> {mode === 'live' ? 'Running real LLM agent...' : 'Running...'}</div>}
        </div>
      )}

      {/* Live error */}
      {liveError && (
        <div className="rounded-xl border border-red-500/30 bg-red-500/5 px-4 py-3 mb-3">
          <p className="text-sm text-red-400">{liveError}</p>
        </div>
      )}

      {/* Loading state for live LLM run */}
      {mode === 'live' && running && (
        <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-10 flex flex-col items-center justify-center min-h-[300px] mb-4">
          <Spinner size="sm" />
          <p className="text-muted text-sm mt-3">Running LLM agent twice — without and with ContractGuard...</p>
          <p className="text-zinc-600 dark:text-zinc-300 text-xs mt-1">This may take 15-30 seconds</p>
        </div>
      )}

      {/* Side-by-side trace comparison */}
      {scenario && ((mode === 'mock' && (running || done || spans.length > 0)) || (mode === 'live' && done)) && (
        <>
          <div className="grid grid-cols-2 gap-4">
            <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5 min-h-[400px]">
              <TraceTimeline
                steps={mode === 'live' ? liveWithout : withoutSteps}
                label="without"
                contractDesc={contractLines.join('\n')}
                animate={mode === 'mock' ? running : done}
                title="Without Sponsio"
              />
            </div>
            <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5 min-h-[400px]">
              <TraceTimeline
                steps={mode === 'live' ? liveWith : withSteps}
                label="with"
                spans={mode === 'live' ? liveSpans : spans}
                contractDesc={contractLines.join('\n')}
                animate={mode === 'live' ? done : false}
                title="With Sponsio"
              />
            </div>
          </div>
          {done && (
            <div className="mt-3 rounded-xl border border-emerald-500/30 bg-emerald-500/5 px-5 py-3 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <svg className="w-5 h-5 text-emerald-400 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                </svg>
                <p className="text-sm font-medium text-emerald-400">
                  {violationCount} violation{violationCount !== 1 ? 's' : ''} caught and blocked
                </p>
              </div>
              <button onClick={() => { setSpans([]); setDone(false); loadDemo(activeDemo); }}
                className="px-3 py-1 text-xs border border-surface-200 dark:border-surface-700 text-zinc-600 dark:text-zinc-300 rounded-lg hover:bg-surface-100 dark:bg-surface-800 transition-colors">
                Replay
              </button>
            </div>
          )}
        </>
      )}

      {/* Re-verify */}
      {done && <DemoReVerify />}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Re-verify section                                                  */
/* ------------------------------------------------------------------ */

function DemoReVerify() {
  const [nlText, setNlText] = useState('');
  const [result, setResult] = useState<ReVerifyResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const handleVerify = async () => {
    if (!nlText.trim()) return;
    setLoading(true);
    try { setResult(await reVerifyContract(nlText.trim())); }
    catch { /* silent */ }
    finally { setLoading(false); }
  };

  const failCount = result ? result.results.filter(r => !r.passed).length : 0;

  return (
    <div className="mt-4 rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5">
      <h3 className="text-sm font-semibold text-stone-900 dark:text-white mb-2">Test Another Contract</h3>
      <div className="flex gap-2">
        <textarea value={nlText} onChange={e => setNlText(e.target.value)}
          placeholder='e.g. tool `issue_refund` at most 2 times'
          rows={2}
          className="flex-1 bg-surface-50 dark:bg-surface-800 border border-surface-200 dark:border-surface-800 rounded-lg px-3 py-1.5 text-sm font-mono text-stone-900 dark:text-zinc-100 placeholder-muted focus:outline-none focus:border-brand resize-none" />
        <button onClick={handleVerify} disabled={!nlText.trim() || loading}
          className="px-4 py-1.5 bg-brand hover:bg-brand-400 text-surface-950 text-xs font-semibold rounded-lg transition-colors disabled:opacity-40 shrink-0 self-start">
          {loading ? '...' : 'Verify'}
        </button>
      </div>
      {result && (
        <div className={`mt-3 rounded-lg border px-3 py-2 ${
          result.overall_passed ? 'border-emerald-500/30 bg-emerald-500/5' : 'border-red-500/30 bg-red-500/5'
        }`}>
          <div className="flex items-center gap-2">
            <span className={`text-sm font-medium ${result.overall_passed ? 'text-emerald-400' : 'text-red-400'}`}>
              {result.overall_passed ? 'All steps pass' : `${failCount} violation${failCount !== 1 ? 's' : ''} found`}
            </span>
            <span className="text-xs text-muted">{result.contract_desc}</span>
            <div className="flex gap-0.5 ml-auto">
              {result.results.map((r, i) => (
                <span key={i} className={`w-2 h-2 rounded-full ${r.passed ? 'bg-emerald-500' : 'bg-red-500'}`} title={`Step ${r.timestep}: ${r.event_summary}`} />
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
