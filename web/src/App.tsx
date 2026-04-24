import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import ErrorBoundary from './components/ErrorBoundary';
import Layout from './components/Layout';
import Spinner from './components/Spinner';

// Primary pipeline pages
const ScanAgent = lazy(() => import('./pages/ScanAgent'));
const Rulebook = lazy(() => import('./pages/Rulebook'));
const Integrate = lazy(() => import('./pages/Integrate'));
const MonitorPage = lazy(() => import('./pages/MonitorPage'));

// Secondary pages
const Playground = lazy(() => import('./pages/Playground'));

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
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            {/* Home redirects to first pipeline step */}
            <Route path="/" element={<Navigate to="/scan" replace />} />

            {/* Primary pipeline */}
            <Route path="/scan" element={<Suspense fallback={<PageLoader />}><ScanAgent /></Suspense>} />
            <Route path="/rulebook" element={<Suspense fallback={<PageLoader />}><Rulebook /></Suspense>} />
            <Route path="/integrate" element={<Suspense fallback={<PageLoader />}><Integrate /></Suspense>} />
            <Route path="/monitor" element={<Suspense fallback={<PageLoader />}><MonitorPage /></Suspense>} />

            {/* Secondary */}
            <Route path="/playground" element={<Suspense fallback={<PageLoader />}><Playground /></Suspense>} />

            {/* Fallback: unknown routes go to first pipeline step */}
            <Route path="*" element={<Navigate to="/scan" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ErrorBoundary>
  );
}
