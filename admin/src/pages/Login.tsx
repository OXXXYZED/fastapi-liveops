import { useState } from "react";
import { api, setToken, unwrap } from "../api/client";
import { Button, ErrorBox, Field } from "../components/ui";

export function LoginPage({ onLogin }: { onLogin: () => void }) {
  const [mode, setMode] = useState<"password" | "token">("password");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [token, setTokenValue] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "password") {
        const s = await unwrap(api.POST("/admin/auth/login", { body: { email, password } }));
        setToken(s.access_token);
      } else {
        setToken(token.trim());
        await unwrap(api.GET("/admin/auth/me"));
      }
      onLogin();
    } catch (err) {
      setToken(null);
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const canSubmit = mode === "password" ? email.trim() && password : token.trim();

  return (
    <div className="login">
      <form className="card" onSubmit={submit}>
        <div className="card-pad stack">
          <div className="brand" style={{ padding: 0 }}>
            <span className="brand-dot" /> LiveOps Admin
          </div>
          <ErrorBox error={error} />
          {mode === "password" ? (
            <>
              <Field label="Email">
                <input className="input" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
              </Field>
              <Field label="Password">
                <input className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
              </Field>
            </>
          ) : (
            <Field label="Admin token" hint={<>The server's <code>LIVEOPS_ADMIN_TOKEN</code> — full owner access.</>}>
              <input className="input" type="password" value={token} onChange={(e) => setTokenValue(e.target.value)} required autoFocus />
            </Field>
          )}
          <Button variant="primary" type="submit" busy={busy} disabled={!canSubmit}>
            Sign in
          </Button>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => {
              setError(null);
              setMode(mode === "password" ? "token" : "password");
            }}
          >
            {mode === "password" ? "Use admin token instead" : "Sign in with email"}
          </button>
        </div>
      </form>
    </div>
  );
}
