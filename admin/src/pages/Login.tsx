import { useState } from "react";
import { api, setToken, unwrap } from "../api/client";
import { Button, ErrorBox, Field } from "../components/ui";

export function LoginPage({ onLogin }: { onLogin: () => void }) {
  const [token, setValue] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setToken(token.trim());
    try {
      await unwrap(api.GET("/admin/projects"));
      onLogin();
    } catch (err) {
      setToken(null);
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <form className="card" onSubmit={submit}>
        <div className="card-pad stack">
          <div className="brand" style={{ padding: 0 }}>
            <span className="brand-dot" /> LiveOps Admin
          </div>
          <p className="muted" style={{ margin: 0 }}>
            Sign in with the server's admin token (<code>LIVEOPS_ADMIN_TOKEN</code>).
          </p>
          <ErrorBox error={error} />
          <Field label="Admin token">
            <input
              className="input"
              type="password"
              autoComplete="current-password"
              value={token}
              onChange={(e) => setValue(e.target.value)}
              required
              autoFocus
            />
          </Field>
          <Button variant="primary" type="submit" busy={busy} disabled={!token.trim()}>
            Sign in
          </Button>
        </div>
      </form>
    </div>
  );
}
