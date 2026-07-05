import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider, useAuth } from "./auth";

const qc = new QueryClient();

function Shell() {
  const { authenticated, setupRequired } = useAuth();
  if (!authenticated) {
    // LoginOrSetup is built in a later task; placeholder keeps the build green.
    return <p>{setupRequired ? "Setup required" : "Please log in"}</p>;
  }
  return (
    <Routes>
      <Route path="/" element={<div>Dashboard</div>} />
      <Route path="/plants" element={<div>Plants</div>} />
      <Route path="/runs" element={<div>Runs</div>} />
      <Route path="/runs/:id" element={<div>Run detail</div>} />
      <Route path="/schedules" element={<div>Schedules</div>} />
      <Route path="/settings" element={<div>Settings</div>} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
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
