import { FormEvent, useState } from "react";
import { api, AuthError } from "../api";
import { useAuth } from "../auth";

export default function LoginOrSetup() {
  const { setupRequired, refresh } = useAuth();
  const [token, setToken] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSetup(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api.setup(token.trim(), password);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "setup failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleLogin(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api.login(password);
      await refresh();
    } catch (err) {
      if (err instanceof AuthError) {
        setError("Incorrect password.");
      } else if (err instanceof Error && err.message === "too many attempts") {
        setError("Too many attempts, wait a minute and try again.");
      } else {
        setError(err instanceof Error ? err.message : "login failed");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-screen">
      <div className="auth-card">
        <div className="auth-card__brand">
          SOLAR<b>OPS</b>
        </div>

        {setupRequired ? (
          <>
            <h1>First-time setup</h1>
            <p className="auth-card__hint">
              Enter the setup token printed in the server console, and choose an admin password.
            </p>
            <form onSubmit={(e) => void handleSetup(e)}>
              <label className="field">
                <span className="field__label">Setup token</span>
                <input
                  className="field__input field__input--mono"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  autoComplete="one-time-code"
                  autoFocus
                  required
                />
              </label>
              <label className="field">
                <span className="field__label">New password</span>
                <input
                  type="password"
                  className="field__input"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="new-password"
                  minLength={8}
                  required
                />
                <span className="field__hint">At least 8 characters.</span>
              </label>
              {error && (
                <p className="auth-card__error" role="alert">
                  {error}
                </p>
              )}
              <button type="submit" className="btn btn--primary btn--block" disabled={busy}>
                {busy ? "Setting up…" : "Complete setup"}
              </button>
            </form>
          </>
        ) : (
          <>
            <h1>Sign in</h1>
            <p className="auth-card__hint">Enter the shared admin password.</p>
            <form onSubmit={(e) => void handleLogin(e)}>
              <label className="field">
                <span className="field__label">Password</span>
                <input
                  type="password"
                  className="field__input"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="current-password"
                  autoFocus
                  required
                />
              </label>
              {error && (
                <p className="auth-card__error" role="alert">
                  {error}
                </p>
              )}
              <button type="submit" className="btn btn--primary btn--block" disabled={busy}>
                {busy ? "Signing in…" : "Sign in"}
              </button>
            </form>
          </>
        )}
      </div>
    </div>
  );
}
