import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from 'react';
import type { SystemInfo, SuggestedContract } from '../types';

// ─── Pipeline form state (persisted across navigation) ────────────────────

export interface ScanFormState {
  agentName: string;
  displayName: string;
  isPublic: boolean;
  activeTab: 'paste' | 'upload' | 'cli' | 'history';
  toolsJson: string;  // JSON-serialised ToolInput[] for the paste tab
}

export interface RulebookFormState {
  agentId: string;
  nlText: string;
}

interface AppState {
  // System info (shared across pages)
  system: SystemInfo | null;
  refreshSystem: () => Promise<void>;

  // Pending suggestions (Scan → Rulebook bridge)
  pendingSuggestions: SuggestedContract[];
  setPendingSuggestions: (s: SuggestedContract[]) => void;
  clearPendingSuggestions: () => void;

  // Pipeline form state (survives back/forward navigation)
  scanForm: ScanFormState;
  setScanForm: (s: Partial<ScanFormState>) => void;
  rulebookForm: RulebookFormState;
  setRulebookForm: (s: Partial<RulebookFormState>) => void;

  // API connection health
  apiHealthy: boolean;

  // Toast notifications
  toasts: Toast[];
  addToast: (message: string, type?: Toast['type']) => void;
  dismissToast: (id: string) => void;
}

interface Toast {
  id: string;
  message: string;
  type: 'info' | 'success' | 'error' | 'warning';
  timestamp: number;
}

const AppContext = createContext<AppState | null>(null);

export function useAppContext() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error('useAppContext must be inside AppProvider');
  return ctx;
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [system, setSystem] = useState<SystemInfo | null>(null);
  const [apiHealthy, setApiHealthy] = useState(true);
  const [pendingSuggestions, setPendingSuggestionsState] = useState<SuggestedContract[]>(() => {
    try {
      const stored = localStorage.getItem('sponsio_pending_suggestions');
      return stored ? JSON.parse(stored) : [];
    } catch { return []; }
  });
  const [toasts, setToasts] = useState<Toast[]>([]);

  // Pipeline form state (persisted to localStorage so it survives reloads too)
  const [scanForm, setScanFormState] = useState<ScanFormState>(() => {
    try {
      const stored = localStorage.getItem('sponsio_scan_form');
      return stored ? JSON.parse(stored) : { agentName: '', displayName: '', isPublic: true, activeTab: 'paste', toolsJson: ''};
    } catch { return { agentName: '', displayName: '', isPublic: true, activeTab: 'paste', toolsJson: ''}; }
  });

  const [rulebookForm, setRulebookFormState] = useState<RulebookFormState>(() => {
    try {
      const stored = localStorage.getItem('sponsio_rulebook_form');
      return stored ? JSON.parse(stored) : { agentId: 'bot', nlText: '' };
    } catch { return { agentId: 'bot', nlText: '' }; }
  });

  const setScanForm = useCallback((partial: Partial<ScanFormState>) => {
    setScanFormState(prev => {
      const next = { ...prev, ...partial };
      try { localStorage.setItem('sponsio_scan_form', JSON.stringify(next)); } catch {}
      return next;
    });
  }, []);

  const setRulebookForm = useCallback((partial: Partial<RulebookFormState>) => {
    setRulebookFormState(prev => {
      const next = { ...prev, ...partial };
      try { localStorage.setItem('sponsio_rulebook_form', JSON.stringify(next)); } catch {}
      return next;
    });
  }, []);

  const refreshSystem = useCallback(async () => {
    try {
      const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api';
      const res = await fetch(`${BASE}/system`);
      if (res.ok) {
        setSystem(await res.json());
        setApiHealthy(true);
      } else {
        setApiHealthy(false);
      }
    } catch {
      setApiHealthy(false);
    }
  }, []);

  const setPendingSuggestions = useCallback((s: SuggestedContract[]) => {
    setPendingSuggestionsState(s);
    try { localStorage.setItem('sponsio_pending_suggestions', JSON.stringify(s)); } catch {}
  }, []);

  const clearPendingSuggestions = useCallback(() => {
    setPendingSuggestionsState([]);
    try { localStorage.removeItem('sponsio_pending_suggestions'); } catch {}
  }, []);

  const addToast = useCallback((message: string, type: Toast['type'] = 'info') => {
    const id = `toast-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
    setToasts(prev => [...prev, { id, message, type, timestamp: Date.now() }]);
    // Auto-dismiss after 5 seconds
    setTimeout(() => {
      setToasts(prev => prev.filter(t => t.id !== id));
    }, 5000);
  }, []);

  const dismissToast = useCallback((id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  // Initial system fetch + periodic health check
  useEffect(() => {
    refreshSystem();
    const id = setInterval(refreshSystem, 30000);
    return () => clearInterval(id);
  }, [refreshSystem]);

  return (
    <AppContext.Provider value={{
      system, refreshSystem,
      pendingSuggestions, setPendingSuggestions, clearPendingSuggestions,
      scanForm, setScanForm,
      rulebookForm, setRulebookForm,
      apiHealthy,
      toasts, addToast, dismissToast,
    }}>
      {children}
    </AppContext.Provider>
  );
}

// Toast container component
export function ToastContainer() {
  const { toasts, dismissToast } = useAppContext();
  if (toasts.length === 0) return null;

  const colors = {
    info: 'border-sky-500/30 bg-sky-500/5 text-sky-400',
    success: 'border-emerald-500/30 bg-emerald-500/5 text-emerald-400',
    error: 'border-red-500/30 bg-red-500/5 text-red-400',
    warning: 'border-amber-500/30 bg-amber-500/5 text-amber-400',
  };

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm">
      {toasts.map(toast => (
        <div
          key={toast.id}
          className={`rounded-lg border px-4 py-3 shadow-lg flex items-start gap-2 animate-in slide-in-from-bottom ${colors[toast.type]}`}
        >
          <p className="text-sm flex-1">{toast.message}</p>
          <button onClick={() => dismissToast(toast.id)} className="opacity-50 hover:opacity-100 transition-opacity shrink-0">
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      ))}
    </div>
  );
}
