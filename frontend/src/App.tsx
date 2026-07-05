import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider, useAuth } from "./auth";
import { Nav } from "./nav";
import LoginOrSetup from "./routes/LoginOrSetup";
import Dashboard from "./routes/Dashboard";
import Plants from "./routes/Plants";
import Runs from "./routes/Runs";
import RunDetail from "./routes/RunDetail";
import Schedules from "./routes/Schedules";
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
          <Route path="/" element={<Dashboard />} />
          <Route path="/plants" element={<Plants />} />
          <Route path="/runs" element={<Runs />} />
          <Route path="/runs/:id" element={<RunDetail />} />
          <Route path="/schedules" element={<Schedules />} />
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
