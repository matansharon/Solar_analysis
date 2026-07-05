import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider, useAuth } from "./auth";
import { Nav } from "./nav";
import LoginOrSetup from "./routes/LoginOrSetup";
import Plants from "./routes/Plants";
import Settings from "./routes/Settings";

const qc = new QueryClient();

function Shell() {
  const { authenticated } = useAuth();
  if (!authenticated) {
    return <LoginOrSetup />;
  }
  return (
    <div className="app-shell">
      <Nav />
      <main className="app-main">
        <Routes>
          <Route path="/" element={<div className="placeholder-panel">Dashboard — coming soon</div>} />
          <Route path="/plants" element={<Plants />} />
          <Route path="/runs" element={<div className="placeholder-panel">Runs — coming soon</div>} />
          <Route path="/runs/:id" element={<div className="placeholder-panel">Run detail — coming soon</div>} />
          <Route path="/schedules" element={<div className="placeholder-panel">Schedules — coming soon</div>} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <AuthProvider>
        <BrowserRouter><Shell /></BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
}
