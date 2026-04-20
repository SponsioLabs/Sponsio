import type { TraceEvent, MonitorEvent } from '../types';

interface Props {
  events: TraceEvent[];
  violations: MonitorEvent[];
}

export default function AgentSwimlanes({ events, violations }: Props) {
  // Group events by agent
  const agents = Array.from(new Set(events.map(e => e.agent)));

  if (agents.length < 2) return null; // Only show for multi-agent

  const violatedActions = new Set(violations.map(v => v.action));

  return (
    <div className="rounded-xl border border-surface-200 dark:border-surface-800 bg-white dark:bg-surface-900 p-5">
      <p className="text-[10px] text-zinc-600 dark:text-zinc-300 uppercase tracking-widest font-medium mb-4">Agent Swimlanes</p>
      <div className="space-y-0">
        {agents.map(agent => {
          const agentEvents = events.filter(e => e.agent === agent);
          const agentViolations = violations.filter(v => v.agent_id === agent);
          return (
            <div key={agent} className="flex items-stretch border-b border-surface-100 dark:border-surface-800 last:border-0">
              {/* Agent label */}
              <div className="w-32 shrink-0 py-3 pr-3 border-r border-surface-200 dark:border-surface-800">
                <p className="text-xs font-mono text-stone-900 dark:text-zinc-100 truncate" title={agent}>{agent}</p>
                <p className="text-[10px] text-zinc-600 dark:text-zinc-300">{agentEvents.length} events</p>
                {agentViolations.length > 0 && (
                  <span className="text-[10px] text-red-400 bg-red-500/10 px-1.5 py-0.5 rounded mt-1 inline-block">
                    {agentViolations.length} violations
                  </span>
                )}
              </div>
              {/* Event lane */}
              <div className="flex-1 py-3 pl-3 flex items-center gap-1 overflow-x-auto">
                {agentEvents.map((ev, i) => {
                  const label = ev.tool ?? ev.key ?? ev.event_type;
                  const isViolation = violatedActions.has(label);
                  return (
                    <div key={i} className="flex items-center gap-1 shrink-0">
                      <div
                        title={`${label} (${ev.event_type})`}
                        className={`px-2 py-1 rounded text-[10px] font-mono border ${
                          isViolation
                            ? 'border-red-500/30 bg-red-500/10 text-red-400'
                            : ev.event_type === 'tool_call'
                              ? 'border-sky-500/20 bg-sky-500/5 text-sky-400'
                              : ev.event_type === 'data_read'
                                ? 'border-green-500/20 bg-green-500/5 text-green-400'
                                : 'border-surface-200 dark:border-surface-700 text-zinc-600 dark:text-zinc-300'
                        }`}
                      >
                        {label}
                      </div>
                      {i < agentEvents.length - 1 && (
                        <svg className="w-3 h-3 text-zinc-600 dark:text-zinc-300 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                        </svg>
                      )}
                    </div>
                  );
                })}
                {agentEvents.length === 0 && (
                  <span className="text-[10px] text-zinc-600 dark:text-zinc-300">No events</span>
                )}
              </div>
            </div>
          );
        })}
      </div>
      {/* Cross-agent data flows */}
      {events.some(e => e.event_type === 'data_write') && events.some(e => e.event_type === 'data_read') && (
        <div className="mt-3 pt-3 border-t border-surface-200 dark:border-surface-800">
          <p className="text-[10px] text-zinc-600 dark:text-zinc-300 mb-2">Data flows between agents detected. Hover events above for details.</p>
        </div>
      )}
    </div>
  );
}
