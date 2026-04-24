import { useState } from 'react';
import { reVerifyContract } from '../api/client';
import type { ReVerifyResponse } from '../types';
import FileUpload from '../components/FileUpload';
import PipelineNav from '../components/PipelineNav';
import HighlightedCode from '../components/HighlightedCode';

// ─── Integration snippets ─────────────────────────────────────────────────────

// Code snippets for each supported framework — Python and TypeScript.
const SNIPPETS = {
  // ── Python ──
  LangGraph: `import sponsio
from langgraph.prebuilt import create_react_agent

guard = sponsio.Sponsio(framework="langgraph", config="sponsio.yaml")
agent = create_react_agent(model, guard.wrap(tools))`,

  'Claude Agent SDK': `import sponsio
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

guard = sponsio.Sponsio(framework="claude_agent", config="sponsio.yaml")
options = ClaudeAgentOptions(hooks=guard.hooks())
# Zero tool wrapping — hooks deny violations natively`,

  'OpenAI SDK': `import sponsio

# Monkey-patches openai.chat.completions.create
sponsio.Sponsio(framework="openai", config="sponsio.yaml")

from openai import OpenAI
client = OpenAI()
# Blocked tool_calls removed from response automatically`,

  'Vercel AI SDK': `import sponsio

guard = sponsio.Sponsio(framework="vercel_ai", config="sponsio.yaml")
# guard.wrap() returns an ai.Middleware
async for msg in agent.run(model, messages, middleware=[guard.wrap()]):
    print(msg)`,

  'Agents SDK': `import sponsio
from agents import Agent

guard = sponsio.Sponsio(framework="agents_sdk", config="sponsio.yaml")
agent = Agent(name="bot", tools=guard.wrap(tools))`,

  CrewAI: `import sponsio
from crewai import Crew

guard = sponsio.Sponsio(framework="crewai", config="sponsio.yaml")
crew = Crew(agents=[...], tasks=[...], tools=guard.wrap(tools))`,

  MCP: `from sponsio.integrations.mcp import MCPContractProxy, scan_mcp_tools

system = scan_mcp_tools(await client.list_tools(), agent_id="bot")
proxy = MCPContractProxy(mcp_client=client, system=system)
result = await proxy.call_tool("process_refund", {"order_id": "123"})`,

  // ── TypeScript — same Sponsio class, native TS SDK ──
  'LangGraph (TS)': `import { Sponsio } from "@sponsio/sdk"
import { wrapTools } from "@sponsio/sdk/langchain"
import { ToolNode } from "@langchain/langgraph/prebuilt"

const guard = new Sponsio({
  agentId: "bot",
  contracts: ["tool \`check_policy\` must precede \`issue_refund\`"],
})
const toolNode = new ToolNode(wrapTools(tools, guard))`,

  'Claude Agent (TS)': `import { Sponsio } from "@sponsio/sdk"
import { sponsioHooks } from "@sponsio/sdk/claude-agent"

const guard = new Sponsio({
  agentId: "bot",
  contracts: ["tool \`check_policy\` must precede \`issue_refund\`"],
})
const options = { hooks: sponsioHooks(guard) }
// Zero wrapping — hooks deny violations natively`,

  'OpenAI (TS)': `import { Sponsio } from "@sponsio/sdk"
import { wrapOpenAI } from "@sponsio/sdk/openai"

const guard = new Sponsio({
  agentId: "bot",
  contracts: ["tool \`check_policy\` must precede \`issue_refund\`"],
})
const client = wrapOpenAI(new OpenAI(), guard)
// Blocked tool_calls removed from response`,

  'Vercel AI (TS)': `import { Sponsio } from "@sponsio/sdk"
import { sponsioMiddleware } from "@sponsio/sdk/vercel-ai"
import { wrapLanguageModel } from "ai"

const guard = new Sponsio({
  agentId: "bot",
  contracts: ["tool \`check_policy\` must precede \`issue_refund\`"],
})
const model = wrapLanguageModel({ model: openai("gpt-4"), middleware: sponsioMiddleware(guard) })`,
} as const;

type Framework = keyof typeof SNIPPETS;

// ─── Helpers ──────────────────────────────────────────────────────────────────

function copyText(text: string, setCopied: (v: boolean) => void) {
  navigator.clipboard.writeText(text).then(() => {
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  });
}

/** Map a framework key to a Shiki-supported language id. */
function langFor(fw: Framework): 'python' | 'typescript' {
  return fw.endsWith('(TS)') ? 'typescript' : 'python';
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function Integrate() {
  // Snippet select
  const [activeFramework, setActiveFramework] = useState<Framework>('LangGraph');
  const [snippetCopied, setSnippetCopied] = useState(false);

  // Verify section
  const [verifyOpen, setVerifyOpen] = useState(false);
  const [traceJson, setTraceJson] = useState('');
  const [verifying, setVerifying] = useState(false);
  const [verifyResults, setVerifyResults] = useState<ReVerifyResponse[]>([]);
  const [verifyError, setVerifyError] = useState('');

  async function handleVerify() {
    const text = traceJson.trim();
    if (!text) return;
    setVerifying(true);
    setVerifyError('');
    setVerifyResults([]);
    try {
      const result = await reVerifyContract(text);
      setVerifyResults([result]);
    } catch (e: unknown) {
      setVerifyError(e instanceof Error ? e.message : 'Verification failed');
    } finally {
      setVerifying(false);
    }
  }

  function handleTraceFile(file: File) {
    const reader = new FileReader();
    reader.onload = (e) => {
      const text = e.target?.result;
      if (typeof text === 'string') setTraceJson(text);
    };
    reader.readAsText(file);
  }

  return (
    <div className="max-w-4xl">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-display text-stone-900 dark:text-white mb-1">Integrate</h1>
        <p className="text-zinc-600 dark:text-zinc-300 text-sm max-w-2xl">
          Your <code className="font-mono text-xs bg-surface-100 dark:bg-surface-800 px-1 rounded">sponsio.yaml</code> is
          already on disk from <code className="font-mono text-xs bg-surface-100 dark:bg-surface-800 px-1 rounded">sponsio onboard</code>.
          Drop a 2–3 line snippet into your agent entry file — pick your framework below. Supports Python and TypeScript.
        </p>
      </div>

      {/* ── Integration Snippet ───────────────────────────────────────────────── */}
      <section className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5 mb-6">
        <h2 className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium mb-4">Integration Snippet</h2>

        {/* Framework select — grouped by language */}
        <div className="mb-4">
          <select
            value={activeFramework}
            onChange={(e) => {
              setActiveFramework(e.target.value as Framework);
              setSnippetCopied(false);
            }}
            className="bg-white dark:bg-surface-800 text-stone-900 dark:text-zinc-100 text-sm rounded-lg px-3 py-1.5 border border-surface-200 dark:border-surface-700 focus:outline-none focus:ring-2 focus:ring-surface-300 dark:focus:ring-surface-600 cursor-pointer"
          >
            <optgroup label="Python">
              <option value="LangGraph">LangGraph</option>
              <option value="Claude Agent SDK">Claude Agent SDK</option>
              <option value="OpenAI SDK">OpenAI SDK</option>
              <option value="Vercel AI SDK">Vercel AI SDK</option>
              <option value="Agents SDK">OpenAI Agents SDK</option>
              <option value="CrewAI">CrewAI</option>
              <option value="MCP">MCP</option>
            </optgroup>
            <optgroup label="TypeScript">
              <option value="LangGraph (TS)">LangGraph / LangChain.js</option>
              <option value="Claude Agent (TS)">Claude Agent SDK</option>
              <option value="OpenAI (TS)">OpenAI SDK</option>
              <option value="Vercel AI (TS)">Vercel AI SDK</option>
            </optgroup>
          </select>
        </div>

        {/* Snippet */}
        <div className="relative">
          <HighlightedCode
            code={SNIPPETS[activeFramework]}
            lang={langFor(activeFramework)}
          />
          <div className="absolute top-2 right-2">
            <button
              onClick={() => copyText(SNIPPETS[activeFramework], setSnippetCopied)}
              className="px-2.5 py-1 text-xs rounded-lg border border-surface-200 dark:border-surface-700 bg-surface-100 dark:bg-surface-800 text-zinc-600 dark:text-zinc-300 hover:text-stone-900 dark:hover:text-zinc-100 hover:border-surface-500 transition-colors font-mono"
            >
              {snippetCopied ? 'Copied!' : 'Copy'}
            </button>
          </div>
        </div>
      </section>

      {/* ── 3. Verify (collapsible) ───────────────────────────────────────────── */}
      <section className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5 mb-6">
        {/* Collapsible header */}
        <button
          onClick={() => setVerifyOpen((o) => !o)}
          className="flex items-center justify-between w-full text-left group"
        >
          <h2 className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium group-hover:text-zinc-600 dark:text-zinc-300 transition-colors">
            Test Before Deploying
          </h2>
          <svg
            className={`w-4 h-4 text-zinc-600 dark:text-zinc-300 transition-transform duration-200 ${verifyOpen ? 'rotate-180' : ''}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        </button>

        {verifyOpen && (
          <div className="mt-5">
            <p className="text-xs text-zinc-600 dark:text-zinc-300 mb-5">Upload a trace file or paste JSON to verify contracts against a real trace.</p>

            <FileUpload
              accept=".json"
              label="Drop a trace file here or click to browse"
              sublabel="Accepts .json trace exports"
              onFile={handleTraceFile}
            />

            <div className="mt-4">
              <label className="block text-xs text-zinc-600 dark:text-zinc-300 mb-1.5">Or paste contract NL / trace JSON</label>
              <textarea
                value={traceJson}
                onChange={(e) => setTraceJson(e.target.value)}
                rows={5}
                placeholder="Paste NL contract text or JSON trace here…"
                className="w-full bg-surface-50 dark:bg-surface-800 border border-surface-200 dark:border-surface-700 rounded-xl px-4 py-3 font-mono text-sm text-stone-900 dark:text-zinc-100 resize-y placeholder:text-zinc-600 dark:text-zinc-300 focus:outline-none focus:ring-2 focus:ring-surface-600"
              />
            </div>

            <div className="mt-4 flex items-center gap-3">
              <button
                onClick={handleVerify}
                disabled={!traceJson.trim() || verifying}
                className="flex items-center gap-2 px-4 py-2 bg-brand text-black text-sm font-semibold rounded-lg hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed transition-opacity"
              >
                {verifying && (
                  <span className="w-3 h-3 border-2 border-black/40 border-t-black rounded-full animate-spin" />
                )}
                Verify
              </button>
              {verifyError && <p className="text-xs text-red-400">{verifyError}</p>}
            </div>

            {/* Results */}
            {verifyResults.length > 0 && (
              <div className="mt-5 space-y-4">
                <p className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium">Results</p>
                {verifyResults.map((r, ri) => (
                  <div
                    key={ri}
                    className={`rounded-xl border px-4 py-3 ${
                      r.overall_passed
                        ? 'border-emerald-500/30 bg-emerald-500/10'
                        : 'border-red-500/30 bg-red-500/10'
                    }`}
                  >
                    <div className="flex items-center gap-3 mb-3">
                      <span
                        className={`w-2.5 h-2.5 rounded-full shrink-0 ${
                          r.overall_passed ? 'bg-emerald-500' : 'bg-red-500'
                        }`}
                      />
                      <p className="text-sm font-mono text-stone-900 dark:text-zinc-100 flex-1 truncate">
                        {r.contract_desc}
                      </p>
                      <span
                        className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${
                          r.overall_passed
                            ? 'bg-emerald-500/10 text-emerald-400'
                            : 'bg-red-500/10 text-red-400'
                        }`}
                      >
                        {r.overall_passed ? 'PASS' : 'FAIL'}
                      </span>
                      {r.pattern_name && (
                        <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-surface-700 text-zinc-600 dark:text-zinc-300">
                          {r.pattern_name}
                        </span>
                      )}
                    </div>

                    {/* Step dots */}
                    {r.results.length > 0 && (
                      <div className="flex flex-wrap gap-1.5">
                        {r.results.map((step, si) => (
                          <div
                            key={si}
                            title={`Step ${step.timestep}: ${step.event_summary}`}
                            className={`flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-mono border ${
                              step.passed
                                ? 'border-emerald-500/30 bg-emerald-500/5 text-emerald-400'
                                : 'border-red-500/30 bg-red-500/5 text-red-400'
                            }`}
                          >
                            <span
                              className={`w-1.5 h-1.5 rounded-full ${
                                step.passed ? 'bg-emerald-500' : 'bg-red-500'
                              }`}
                            />
                            t{step.timestep}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </section>

      {/* Bottom nav */}
      <PipelineNav
        prev={{ label: 'Back to Contract Library', path: '/rulebook' }}
        next={{ label: 'Start Monitoring', path: '/monitor' }}
      />
    </div>
  );
}
