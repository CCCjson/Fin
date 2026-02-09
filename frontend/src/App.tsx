import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Layout } from './components/layout/Layout';
import { Dashboard } from './pages/Dashboard';
import { Market } from './pages/Market';
import { Trading } from './pages/Trading';
import { Signals } from './pages/Signals';
import { Backtest } from './pages/Backtest';
import { Realtime } from './pages/Realtime';
import { Reports } from './pages/Reports';
import { SignalTracking } from './pages/SignalTracking';

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="dashboard" element={<Dashboard />} />
          <Route path="realtime" element={<Realtime />} />
          <Route path="market" element={<Market />} />
          <Route path="trading" element={<Trading />} />
          <Route path="signals" element={<Signals />} />
          <Route path="tracking" element={<SignalTracking />} />
          <Route path="backtest" element={<Backtest />} />
          <Route path="reports" element={<Reports />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default App;
