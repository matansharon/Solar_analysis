import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, Plant } from "../api";

type Platform = Plant["platform"];
type AuthMode = Plant["auth_mode"];

const PLATFORMS: Platform[] = ["solaredge", "growatt", "sma"];

type TestState =
  | { status: "pending" }
  | { status: "ok"; error: string | null }
  | { status: "failed"; error: string | null }
  | { status: "busy" };

type FormTarget = { mode: "create" } | { mode: "edit"; plant: Plant };

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export default function Plants() {
  const qc = useQueryClient();
  const { data: plants, isLoading, error } = useQuery({ queryKey: ["plants"], queryFn: api.plants });

  const [formTarget, setFormTarget] = useState<FormTarget | null>(null);
  const [testState, setTestState] = useState<Record<number, TestState>>({});
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const toggleEnabled = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) => api.updatePlant(id, { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["plants"] }),
  });

  async function handleTest(id: number) {
    setTestState((s) => ({ ...s, [id]: { status: "pending" } }));
    try {
      const res = await api.testPlant(id);
      setTestState((s) => ({ ...s, [id]: { status: res.ok ? "ok" : "failed", error: res.error } }));
      void qc.invalidateQueries({ queryKey: ["plants"] });
    } catch (err) {
      const message = err instanceof Error ? err.message : "test failed";
      if (message === "busy") {
        setTestState((s) => ({ ...s, [id]: { status: "busy" } }));
      } else {
        setTestState((s) => ({ ...s, [id]: { status: "failed", error: message } }));
      }
    }
  }

  async function handleDelete(id: number) {
    setDeletingId(id);
    try {
      await api.deletePlant(id);
      void qc.invalidateQueries({ queryKey: ["plants"] });
    } finally {
      setDeletingId(null);
      setConfirmDeleteId(null);
    }
  }

  return (
    <div>
      <div className="page-header">
        <div className="page-header__title">
          <h1>Plants</h1>
          <p>Monitored solar installations and portal credentials.</p>
        </div>
        <button type="button" className="btn btn--primary" onClick={() => setFormTarget({ mode: "create" })}>
          + Add plant
        </button>
      </div>

      <div className="panel">
        {isLoading ? (
          <div className="empty-state">Loading plants…</div>
        ) : error ? (
          <div className="empty-state">{error instanceof Error ? error.message : "failed to load plants"}</div>
        ) : !plants || plants.length === 0 ? (
          <div className="empty-state">No plants configured yet. Add one, or import from config.yaml in Settings.</div>
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Platform</th>
                  <th>Auth</th>
                  <th>Enabled</th>
                  <th>Last test</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {plants.map((p) => (
                  <PlantRow
                    key={p.id}
                    plant={p}
                    testState={testState[p.id]}
                    confirmingDelete={confirmDeleteId === p.id}
                    deleting={deletingId === p.id}
                    onToggleEnabled={() => toggleEnabled.mutate({ id: p.id, enabled: !p.enabled })}
                    onTest={() => void handleTest(p.id)}
                    onEdit={() => setFormTarget({ mode: "edit", plant: p })}
                    onDeleteRequest={() => setConfirmDeleteId(p.id)}
                    onDeleteCancel={() => setConfirmDeleteId(null)}
                    onDeleteConfirm={() => void handleDelete(p.id)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {formTarget && (
        <PlantFormModal
          target={formTarget}
          onClose={() => setFormTarget(null)}
          onSaved={() => {
            setFormTarget(null);
            void qc.invalidateQueries({ queryKey: ["plants"] });
          }}
        />
      )}
    </div>
  );
}

function TestCell({ plant, state }: { plant: Plant; state: TestState | undefined }) {
  if (state?.status === "pending") {
    return (
      <span className="status">
        <span className="spinner" aria-hidden="true" /> testing…
      </span>
    );
  }
  if (state?.status === "busy") {
    return <span className="status status--busy">busy — a run or test is in progress</span>;
  }
  if (state?.status === "ok") {
    return (
      <span className="status status--ok">
        <span className="status-icon">✓</span> ok
      </span>
    );
  }
  if (state?.status === "failed") {
    return (
      <div className="test-cell">
        <span className="status status--failed">
          <span className="status-icon">✗</span> failed
        </span>
        {state.error && <div className="test-error">{state.error}</div>}
      </div>
    );
  }
  if (!plant.last_test_at) {
    return <span className="status status--never">never tested</span>;
  }
  if (plant.last_test_ok) {
    return (
      <span className="status status--ok">
        <span className="status-icon">✓</span> ok{" "}
        <span className="cell-timestamp">{formatTimestamp(plant.last_test_at)}</span>
      </span>
    );
  }
  return (
    <div className="test-cell">
      <span className="status status--failed">
        <span className="status-icon">✗</span> failed{" "}
        <span className="cell-timestamp">{formatTimestamp(plant.last_test_at)}</span>
      </span>
      {plant.last_test_error && <div className="test-error">{plant.last_test_error}</div>}
    </div>
  );
}

function PlantRow({
  plant,
  testState,
  confirmingDelete,
  deleting,
  onToggleEnabled,
  onTest,
  onEdit,
  onDeleteRequest,
  onDeleteCancel,
  onDeleteConfirm,
}: {
  plant: Plant;
  testState: TestState | undefined;
  confirmingDelete: boolean;
  deleting: boolean;
  onToggleEnabled: () => void;
  onTest: () => void;
  onEdit: () => void;
  onDeleteRequest: () => void;
  onDeleteCancel: () => void;
  onDeleteConfirm: () => void;
}) {
  return (
    <tr>
      <td>{plant.name}</td>
      <td>
        <span className={`badge-platform badge-platform--${plant.platform}`}>{plant.platform}</span>
      </td>
      <td className="cell-muted">{plant.auth_mode}</td>
      <td>
        <button
          type="button"
          role="switch"
          aria-checked={plant.enabled}
          className={`toggle${plant.enabled ? " is-on" : ""}`}
          onClick={onToggleEnabled}
          title={plant.enabled ? "Disable plant" : "Enable plant"}
        />
      </td>
      <td>
        <TestCell plant={plant} state={testState} />
      </td>
      <td>
        {confirmingDelete ? (
          <span className="btn-row">
            <span className="cell-muted">Delete &ldquo;{plant.name}&rdquo;?</span>
            <button type="button" className="btn btn--danger btn--small" onClick={onDeleteConfirm} disabled={deleting}>
              {deleting ? "Deleting…" : "Confirm"}
            </button>
            <button type="button" className="btn btn--ghost btn--small" onClick={onDeleteCancel} disabled={deleting}>
              Cancel
            </button>
          </span>
        ) : (
          <span className="btn-row">
            <button type="button" className="btn btn--ghost btn--small" onClick={onEdit}>
              Edit
            </button>
            <button
              type="button"
              className="btn btn--ghost btn--small"
              onClick={onTest}
              disabled={testState?.status === "pending"}
            >
              {testState?.status === "pending" ? "Testing…" : "Test"}
            </button>
            <button type="button" className="btn btn--danger btn--small" onClick={onDeleteRequest}>
              Delete
            </button>
          </span>
        )}
      </td>
    </tr>
  );
}

interface FormValues {
  name: string;
  platform: Platform;
  authMode: AuthMode;
  username: string;
  password: string;
  token: string;
  tariff: string;
  currency: string;
  enabled: boolean;
}

function initialValues(target: FormTarget): FormValues {
  if (target.mode === "edit") {
    const p = target.plant;
    return {
      name: p.name,
      platform: p.platform,
      authMode: p.auth_mode,
      username: p.username ?? "",
      password: "",
      token: "",
      tariff: p.tariff_per_kwh != null ? String(p.tariff_per_kwh) : "",
      currency: p.currency ?? "",
      enabled: p.enabled,
    };
  }
  return {
    name: "",
    platform: "solaredge",
    authMode: "password",
    username: "",
    password: "",
    token: "",
    tariff: "",
    currency: "",
    enabled: true,
  };
}

/** Mirrors solaranalysis/web/routes/plants.py::validate_plant so bad input
 * is caught before it round-trips to the server. */
function validatePlant(values: FormValues, existing: Plant | null): string | null {
  const isCreate = existing === null;
  const platform = values.platform;
  if (values.authMode === "token" && platform !== "growatt") {
    return "token mode is only valid for growatt";
  }
  const authMode: AuthMode = platform !== "growatt" ? "password" : values.authMode;
  if (!PLATFORMS.includes(platform)) {
    return "platform must be one of ['growatt', 'solaredge', 'sma']";
  }
  const name = values.name.trim();
  if (isCreate && !name) return "name is required";
  if (!isCreate && !name) return "name cannot be empty";
  if (isCreate) {
    if (authMode === "password" && !(values.username.trim() && values.password.trim())) {
      return "password mode requires username and password";
    }
    if (authMode === "token" && !values.token.trim()) {
      return "token mode requires a token";
    }
  } else {
    const hasPassword = existing.has_password || Boolean(values.password.trim());
    const hasToken = existing.has_token || Boolean(values.token.trim());
    const username = values.username.trim() || existing.username;
    if (authMode === "password" && !(username && hasPassword)) {
      return "password mode requires username and a stored/new password";
    }
    if (authMode === "token" && platform === "growatt" && !hasToken) {
      return "token mode requires a stored/new token";
    }
  }
  return null;
}

type PlantPayload = Partial<Plant> & { password?: string; token?: string };

function buildPayload(values: FormValues): PlantPayload {
  const payload: PlantPayload = {
    name: values.name.trim(),
    platform: values.platform,
    auth_mode: values.platform === "growatt" ? values.authMode : "password",
    username: values.username.trim() || null,
    tariff_per_kwh: values.tariff.trim() === "" ? null : Number(values.tariff),
    currency: values.currency.trim() || null,
    enabled: values.enabled,
  };
  // Blank password/token means "keep current" on edit; omit the key entirely
  // so the server's exclude_unset semantics leave the stored secret alone.
  if (values.password.trim()) payload.password = values.password;
  if (values.token.trim()) payload.token = values.token;
  return payload;
}

function PlantFormModal({
  target,
  onClose,
  onSaved,
}: {
  target: FormTarget;
  onClose: () => void;
  onSaved: () => void;
}) {
  const existing = target.mode === "edit" ? target.plant : null;
  const [values, setValues] = useState<FormValues>(() => initialValues(target));
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  function patch(partial: Partial<FormValues>) {
    setValues((v) => ({ ...v, ...partial }));
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const validationError = validatePlant(values, existing);
    if (validationError) {
      setError(validationError);
      return;
    }
    setError(null);
    setSaving(true);
    try {
      const payload = buildPayload(values);
      if (existing) {
        await api.updatePlant(existing.id, payload);
      } else {
        await api.createPlant(payload);
      }
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "save failed");
    } finally {
      setSaving(false);
    }
  }

  const showPassword = values.platform !== "growatt" || values.authMode === "password";
  const showToken = values.platform === "growatt" && values.authMode === "token";

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal__header">
          <h2>{existing ? `Edit ${existing.name}` : "Add plant"}</h2>
          <button type="button" className="modal__close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <form onSubmit={(e) => void handleSubmit(e)}>
          <label className="field">
            <span className="field__label">Name</span>
            <input className="field__input" value={values.name} onChange={(e) => patch({ name: e.target.value })} required />
          </label>

          <div className="field-row">
            <label className="field">
              <span className="field__label">Platform</span>
              <select
                className="field__select"
                value={values.platform}
                onChange={(e) => {
                  const platform = e.target.value as Platform;
                  patch({ platform, authMode: platform === "growatt" ? values.authMode : "password" });
                }}
              >
                {PLATFORMS.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="field__label">Auth mode</span>
              <select
                className="field__select"
                value={values.platform === "growatt" ? values.authMode : "password"}
                onChange={(e) => patch({ authMode: e.target.value as AuthMode })}
                disabled={values.platform !== "growatt"}
              >
                <option value="password">password</option>
                {values.platform === "growatt" && <option value="token">token</option>}
              </select>
            </label>
          </div>

          <label className="field">
            <span className="field__label">Username</span>
            <input className="field__input" value={values.username} onChange={(e) => patch({ username: e.target.value })} />
          </label>

          {showPassword && (
            <label className="field">
              <span className="field__label">Password</span>
              <input
                type="password"
                className="field__input"
                value={values.password}
                onChange={(e) => patch({ password: e.target.value })}
                placeholder={existing ? "leave blank to keep current" : ""}
                autoComplete="new-password"
              />
            </label>
          )}

          {showToken && (
            <label className="field">
              <span className="field__label">Token</span>
              <input
                className="field__input field__input--mono"
                value={values.token}
                onChange={(e) => patch({ token: e.target.value })}
                placeholder={existing ? "leave blank to keep current" : ""}
              />
            </label>
          )}

          <div className="field-row">
            <label className="field">
              <span className="field__label">Tariff / kWh</span>
              <input
                type="number"
                step="any"
                className="field__input field__input--mono"
                value={values.tariff}
                onChange={(e) => patch({ tariff: e.target.value })}
              />
            </label>
            <label className="field">
              <span className="field__label">Currency</span>
              <input
                className="field__input"
                value={values.currency}
                onChange={(e) => patch({ currency: e.target.value })}
                placeholder="e.g. ILS"
              />
            </label>
          </div>

          <label className="checkbox-field">
            <input type="checkbox" checked={values.enabled} onChange={(e) => patch({ enabled: e.target.checked })} />
            Enabled
          </label>

          {error && (
            <p className="alert alert--error" role="alert">
              {error}
            </p>
          )}

          <div className="btn-row">
            <button type="submit" className="btn btn--primary" disabled={saving}>
              {saving ? "Saving…" : existing ? "Save changes" : "Create plant"}
            </button>
            <button type="button" className="btn btn--ghost" onClick={onClose} disabled={saving}>
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
