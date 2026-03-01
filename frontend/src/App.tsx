import React from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { Layout } from './components/layout/Layout';
import { Home } from './pages/Home';
import { Automation } from './pages/Automation';
import { AlphaLab } from './pages/AlphaLab';

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/app/*" element={<Layout />} />
        <Route path="/trading" element={<Automation />} />
        <Route path="/alpha-lab" element={<AlphaLab />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
