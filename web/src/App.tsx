import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';

import ErrorBoundary from './components/ErrorBoundary';
import Layout from './components/Layout';
import Spinner from './components/Spinner';
import { AppProvider } from './context/AppContext';

const MonitorPage = lazy(() => import('./pages/MonitorPage'));
const Rulebook = lazy(() => import('./pages/Rulebook'));

function PageLoader() {
  return (
    <div className="flex items-center justify-center h-64">
      <Spinner size="lg" />
    </div>
  );
}

export default function App() {
  return (
    <ErrorBoundary>
      <AppProvider>
        <BrowserRouter>
          <Routes>
            <Route element={<Layout />}>
              <Route path="/" element={<Navigate to="/monitor" replace />} />
              <Route
                path="/monitor"
                element={
                  <Suspense fallback={<PageLoader />}>
                    <MonitorPage />
                  </Suspense>
                }
              />
              <Route
                path="/rulebook"
                element={
                  <Suspense fallback={<PageLoader />}>
                    <Rulebook />
                  </Suspense>
                }
              />
              <Route path="*" element={<Navigate to="/monitor" replace />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </AppProvider>
    </ErrorBoundary>
  );
}
