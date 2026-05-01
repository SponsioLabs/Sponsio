/**
 * Top-level app context — capability flags + global UI state for the
 * OSS local dashboard. Intentionally tiny: page-local state stays in
 * the page component.
 */

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react';

import { getCapabilities } from '../api/client';
import type { Capabilities } from '../types';

interface AppState {
  capabilities: Capabilities | null;
  capabilitiesError: string | null;
}

const Ctx = createContext<AppState | null>(null);

export function AppProvider({ children }: { children: ReactNode }) {
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [capabilitiesError, setCapabilitiesError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getCapabilities()
      .then((c) => {
        if (!cancelled) setCapabilities(c);
      })
      .catch((err: Error) => {
        if (!cancelled) setCapabilitiesError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <Ctx.Provider value={{ capabilities, capabilitiesError }}>{children}</Ctx.Provider>
  );
}

export function useApp(): AppState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useApp must be used inside <AppProvider>');
  return ctx;
}

export function useFeature(name: string): boolean {
  const { capabilities } = useApp();
  return capabilities?.features?.[name] === true;
}
