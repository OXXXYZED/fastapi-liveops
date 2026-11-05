import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, unwrap, type Schemas } from "../api/client";
import { useEnvContext } from "../api/hooks";
import { Button, ErrorBox, Field, Loading, useToast } from "../components/ui";
import { EnvHead } from "./EnvHead";

const lines = (s: string) => s.split(/[\n,]/).map((x) => x.trim()).filter(Boolean);

export function SignInPage() {
  const { envId } = useEnvContext();
  const qc = useQueryClient();
  const toast = useToast();
  const path = { environment_id: envId };
  const cfg = useQuery({
    queryKey: ["auth-providers", envId],
    queryFn: () => unwrap(api.GET("/admin/environments/{environment_id}/auth-providers", { params: { path } })),
  });
  const [google, setGoogle] = useState("");
  const [apple, setApple] = useState("");
  const [steamApp, setSteamApp] = useState("");
  const [steamKey, setSteamKey] = useState("");
  useEffect(() => {
    if (!cfg.data) return;
    setGoogle((cfg.data.google?.client_ids ?? []).join("\n"));
    setApple((cfg.data.apple?.audiences ?? []).join("\n"));
    setSteamApp(cfg.data.steam?.app_id?.toString() ?? "");
    setSteamKey(cfg.data.steam?.web_api_key ?? "");
  }, [cfg.data]);

  const save = useMutation({
    mutationFn: () => {
      const body: Schemas["AuthProviders"] = {
        google: { client_ids: lines(google) },
        apple: { audiences: lines(apple) },
        steam: { app_id: steamApp ? Number(steamApp) : null, web_api_key: steamKey || null },
      };
      return unwrap(api.PUT("/admin/environments/{environment_id}/auth-providers", { params: { path }, body }));
    },
    onSuccess: (d) => {
      qc.setQueryData(["auth-providers", envId], d);
      toast("Sign-in providers saved");
    },
  });

  return (
    <>
      <EnvHead
        title="Player sign-in"
        description="Let players sign in with Google, Apple or Steam to keep progress across devices. Anonymous / device login always works."
        actions={<Button write variant="primary" busy={save.isPending} disabled={!cfg.data} onClick={() => save.mutate()}>Save</Button>}
      />
      <ErrorBox error={cfg.error ?? save.error} />
      {cfg.isLoading ? (
        <div className="card"><Loading /></div>
      ) : (
        <div className="stack">
          <div className="card card-pad stack">
            <h2>Google</h2>
            <Field label="OAuth client IDs" hint="One per line — the audiences of ID tokens from your Android/iOS/web clients.">
              <textarea rows={3} value={google} onChange={(e) => setGoogle(e.target.value)} placeholder="1234-abc.apps.googleusercontent.com" />
            </Field>
          </div>
          <div className="card card-pad stack">
            <h2>Apple</h2>
            <Field label="Audiences" hint="Bundle IDs (native Sign in with Apple) or Services IDs (web), one per line.">
              <textarea rows={2} value={apple} onChange={(e) => setApple(e.target.value)} placeholder="com.example.game" />
            </Field>
          </div>
          <div className="card card-pad stack">
            <h2>Steam</h2>
            <div className="grid" style={{ gridTemplateColumns: "1fr 2fr" }}>
              <Field label="App ID">
                <input className="input" inputMode="numeric" value={steamApp} onChange={(e) => setSteamApp(e.target.value.replace(/\D/g, ""))} />
              </Field>
              <Field label="Publisher Web API key" hint="Stored server-side and never shown again.">
                <input className="input mono" value={steamKey} onChange={(e) => setSteamKey(e.target.value)} autoComplete="off" />
              </Field>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
