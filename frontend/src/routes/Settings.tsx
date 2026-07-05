import { FormEvent, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useAuth } from "../auth";

interface ImportSummary {
  created: string[];
  updated: string[];
  secrets: Record<string, { password: boolean; token: boolean }>;
  settings: { model: string | null; max_input_tokens: number; output_language: string };
  error: string | null;
}

export default function Settings() {
  return (
    <div>
      <div className="page-header">
        <div className="page-header__title">
          <h1>Settings</h1>
          <p>Analysis defaults, admin password, and one-time config import.</p>
        </div>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 20, maxWidth: 520 }}>
        <GeneralSettingsCard />
        <ChangePasswordCard />
        <ImportCard />
      </div>
    </div>
  );
}

function GeneralSettingsCard() {
  const qc = useQueryClient();
  const { data, isLoading, error: loadError } = useQuery({ queryKey: ["settings"], queryFn: api.settings });

  const [model, setModel] = useState("");
  const [maxInputTokens, setMaxInputTokens] = useState(60000);
  const [outputLanguage, setOutputLanguage] = useState<"en" | "he">("en");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveOk, setSaveOk] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!data) return;
    setModel(data.model ?? "");
    setMaxInputTokens(data.max_input_tokens);
    setOutputLanguage(data.output_language === "he" ? "he" : "en");
  }, [data]);

  async function handleSave(e: FormEvent) {
    e.preventDefault();
    setSaveError(null);
    setSaveOk(false);
    setSaving(true);
    try {
      await api.saveSettings({
        model: model.trim() === "" ? null : model.trim(),
        max_input_tokens: maxInputTokens,
        output_language: outputLanguage,
      });
      setSaveOk(true);
      void qc.invalidateQueries({ queryKey: ["settings"] });
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="panel" style={{ padding: 20 }}>
      <h2>Analysis defaults</h2>
      {isLoading ? (
        <p className="cell-muted">Loading…</p>
      ) : loadError ? (
        <p className="alert alert--error">{loadError instanceof Error ? loadError.message : "failed to load settings"}</p>
      ) : (
        <form onSubmit={(e) => void handleSave(e)}>
          <label className="field">
            <span className="field__label">Model</span>
            <input
              className="field__input field__input--mono"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="leave empty for auto"
            />
          </label>
          <label className="field">
            <span className="field__label">Max input tokens</span>
            <input
              type="number"
              min={1}
              className="field__input field__input--mono"
              value={maxInputTokens}
              onChange={(e) => setMaxInputTokens(Number(e.target.value))}
            />
          </label>
          <label className="field">
            <span className="field__label">Output language</span>
            <select
              className="field__select"
              value={outputLanguage}
              onChange={(e) => setOutputLanguage(e.target.value === "he" ? "he" : "en")}
            >
              <option value="en">English</option>
              <option value="he">Hebrew</option>
            </select>
          </label>

          {saveError && <p className="alert alert--error">{saveError}</p>}
          {saveOk && <p className="alert alert--ok">Settings saved.</p>}

          <button type="submit" className="btn btn--primary" disabled={saving}>
            {saving ? "Saving…" : "Save settings"}
          </button>
        </form>
      )}
    </section>
  );
}

function ChangePasswordCard() {
  const { refresh } = useAuth();
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api.changePassword(oldPassword, newPassword);
      setOldPassword("");
      setNewPassword("");
      setDone(true);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "change password failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel" style={{ padding: 20 }}>
      <h2>Change password</h2>
      {done ? (
        <p className="alert alert--ok">Password changed — please log in again.</p>
      ) : (
        <form onSubmit={(e) => void handleSubmit(e)}>
          <label className="field">
            <span className="field__label">Current password</span>
            <input
              type="password"
              className="field__input"
              value={oldPassword}
              onChange={(e) => setOldPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </label>
          <label className="field">
            <span className="field__label">New password</span>
            <input
              type="password"
              className="field__input"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              autoComplete="new-password"
              minLength={8}
              required
            />
          </label>
          {error && <p className="alert alert--error">{error}</p>}
          <button type="submit" className="btn btn--primary" disabled={busy}>
            {busy ? "Changing…" : "Change password"}
          </button>
        </form>
      )}
    </section>
  );
}

function ImportCard() {
  const qc = useQueryClient();
  const [result, setResult] = useState<ImportSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);

  async function handleImport() {
    setError(null);
    setResult(null);
    setImporting(true);
    try {
      const res = await api.runImport();
      setResult(res as unknown as ImportSummary);
      void qc.invalidateQueries({ queryKey: ["plants"] });
      void qc.invalidateQueries({ queryKey: ["settings"] });
    } catch (err) {
      const message = err instanceof Error ? err.message : "import failed";
      if (message === "config.yaml not found") {
        setError("No config.yaml found on the server.");
      } else {
        setError(message);
      }
    } finally {
      setImporting(false);
    }
  }

  return (
    <section className="panel" style={{ padding: 20 }}>
      <h2>Import from config.yaml</h2>
      <p className="cell-muted">
        One-time import of plants and settings from the server's existing <code className="mono">config.yaml</code> /{" "}
        <code className="mono">.env</code>. Re-running updates plants that already match by name.
      </p>
      <button type="button" className="btn btn--ghost" onClick={() => void handleImport()} disabled={importing}>
        {importing ? "Importing…" : "Run import"}
      </button>

      {error && (
        <p className="alert alert--error" style={{ marginTop: 14 }}>
          {error}
        </p>
      )}

      {result && (
        <div className="import-summary">
          <p>
            Created: {result.created.length ? result.created.join(", ") : "none"}
            <br />
            Updated: {result.updated.length ? result.updated.join(", ") : "none"}
          </p>
          {Object.keys(result.secrets).length > 0 && (
            <>
              <p style={{ marginBottom: 0 }}>Secrets resolved:</p>
              <ul>
                {Object.entries(result.secrets).map(([name, s]) => (
                  <li key={name}>
                    {name}: password {s.password ? "yes" : "no"}, token {s.token ? "yes" : "no"}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </section>
  );
}
