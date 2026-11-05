import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap, type Schemas } from "../api/client";
import { useEnvContext } from "../api/hooks";
import { Button, CopyButton, Empty, ErrorBox, Field, Loading, Modal, confirmAction, formatDate, useToast } from "../components/ui";
import { EnvHead } from "./EnvHead";

const EVENTS = [
  ["player.created", "New player"],
  ["player.event", "Custom analytics events from clients"],
  ["experiment.exposure", "Player assigned to an experiment variant"],
  ["config.published", "Config version published"],
  ["leaderboard.score", "Score submitted"],
] as const;

export function WebhooksPage() {
  const { envId } = useEnvContext();
  const path = { environment_id: envId };
  const qc = useQueryClient();
  const toast = useToast();
  const [creating, setCreating] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);
  const [deliveries, setDeliveries] = useState<Schemas["WebhookOut"] | null>(null);
  const hooks = useQuery({
    queryKey: ["webhooks", envId],
    queryFn: () => unwrap(api.GET("/admin/environments/{environment_id}/webhooks", { params: { path } })),
  });
  const p = (id: string) => ({ ...path, webhook_id: id });
  const toggle = useMutation({
    mutationFn: (h: Schemas["WebhookOut"]) =>
      unwrap(api.PATCH("/admin/environments/{environment_id}/webhooks/{webhook_id}", { params: { path: p(h.id) }, body: { active: !h.active } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["webhooks", envId] }),
    onError: (e) => toast(e.message, true),
  });
  const test = useMutation({
    mutationFn: (id: string) => unwrap(api.POST("/admin/environments/{environment_id}/webhooks/{webhook_id}/test", { params: { path: p(id) } })),
    onSuccess: () => toast("Ping queued — check deliveries in a few seconds"),
    onError: (e) => toast(e.message, true),
  });
  const del = useMutation({
    mutationFn: (id: string) => unwrap(api.DELETE("/admin/environments/{environment_id}/webhooks/{webhook_id}", { params: { path: p(id) } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["webhooks", envId] });
      toast("Webhook deleted");
    },
  });

  return (
    <>
      <EnvHead
        title="Webhooks"
        description={<>Send events to your analytics or backend. Requests are signed: <code>X-LiveOps-Signature: t=…,v1=HMAC-SHA256(secret, "t.body")</code>.</>}
        actions={<Button write variant="primary" onClick={() => setCreating(true)}>Add webhook</Button>}
      />
      <div className="card">
        <ErrorBox error={hooks.error} />
        {hooks.isLoading ? (
          <Loading />
        ) : hooks.error ? null : !hooks.data?.length ? (
          <Empty title="No webhooks">Forward analytics events and experiment exposures to tools like Segment, BigQuery or your own service.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Endpoint</th><th>Events</th><th>Active</th><th /></tr></thead>
              <tbody>
                {hooks.data.map((h) => (
                  <tr key={h.id}>
                    <td className="mono small" style={{ wordBreak: "break-all" }}>{h.url}</td>
                    <td className="small">{h.events.map((e) => <span key={e} className="badge" style={{ marginRight: 4 }}>{e}</span>)}</td>
                    <td>
                      <label className="switch"><input type="checkbox" checked={h.active} onChange={() => toggle.mutate(h)} aria-label={`Active: ${h.url}`} /></label>
                    </td>
                    <td className="num">
                      <div className="row" style={{ justifyContent: "flex-end" }}>
                        <Button size="sm" onClick={() => setDeliveries(h)}>Deliveries</Button>
                        <Button write size="sm" onClick={() => test.mutate(h.id)}>Ping</Button>
                        <Button write size="sm" variant="danger" onClick={() => confirmAction(`Delete webhook to ${h.url}?`) && del.mutate(h.id)}>Delete</Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {creating && <CreateWebhook envId={envId} onClose={() => setCreating(false)} onCreated={(s) => { setCreating(false); setSecret(s); }} />}
      {secret && (
        <Modal title="Webhook created" onClose={() => setSecret(null)} footer={<Button variant="primary" onClick={() => setSecret(null)}>Done</Button>}>
          <div className="alert alert-warn">Copy the signing secret now — it won't be shown again.</div>
          <div className="secret">{secret}</div>
          <div><CopyButton text={secret} /></div>
        </Modal>
      )}
      {deliveries && <DeliveriesModal envId={envId} hook={deliveries} onClose={() => setDeliveries(null)} />}
    </>
  );
}

function CreateWebhook({ envId, onClose, onCreated }: { envId: string; onClose: () => void; onCreated: (secret: string) => void }) {
  const qc = useQueryClient();
  const [url, setUrl] = useState("https://");
  const [all, setAll] = useState(false);
  const [events, setEvents] = useState<string[]>(["player.event", "experiment.exposure"]);
  const m = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/admin/environments/{environment_id}/webhooks", { params: { path: { environment_id: envId } }, body: { url, events: all ? ["*"] : events } })),
    onSuccess: (h) => {
      qc.invalidateQueries({ queryKey: ["webhooks", envId] });
      onCreated(h.secret);
    },
  });
  const valid = /^https?:\/\/\S+\.\S+|^https?:\/\/localhost/.test(url) && (all || events.length > 0);
  return (
    <Modal title="Add webhook" onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" busy={m.isPending} disabled={!valid} onClick={() => m.mutate()}>Create</Button></>}>
      <ErrorBox error={m.error} />
      <Field label="Endpoint URL"><input className="input mono" value={url} onChange={(e) => setUrl(e.target.value)} /></Field>
      <label className="switch"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /><span>All events (including future ones)</span></label>
      {!all && (
        <div className="stack" style={{ gap: 6 }}>
          {EVENTS.map(([k, label]) => (
            <label key={k} className="row" style={{ gap: 8 }}>
              <input type="checkbox" checked={events.includes(k)} onChange={(e) => setEvents((ev) => (e.target.checked ? [...ev, k] : ev.filter((x) => x !== k)))} />
              <span className="mono small">{k}</span><span className="muted small">{label}</span>
            </label>
          ))}
        </div>
      )}
      <p className="muted small" style={{ margin: 0 }}>Failed deliveries are retried with exponential backoff for about an hour (8 attempts).</p>
    </Modal>
  );
}

function DeliveriesModal({ envId, hook, onClose }: { envId: string; hook: Schemas["WebhookOut"]; onClose: () => void }) {
  const qc = useQueryClient();
  const path = { environment_id: envId, webhook_id: hook.id };
  const q = useQuery({
    queryKey: ["deliveries", hook.id],
    queryFn: () => unwrap(api.GET("/admin/environments/{environment_id}/webhooks/{webhook_id}/deliveries", { params: { path } })),
    refetchInterval: 5000,
  });
  const retry = useMutation({
    mutationFn: (id: string) => unwrap(api.POST("/admin/environments/{environment_id}/webhooks/{webhook_id}/deliveries/{delivery_id}/retry", { params: { path: { ...path, delivery_id: id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["deliveries", hook.id] }),
  });
  const badge = (s: string) => (s === "delivered" ? "badge-ok" : s === "failed" ? "badge-danger" : "");
  return (
    <Modal title="Recent deliveries" onClose={onClose} footer={<Button onClick={onClose}>Close</Button>}>
      <div className="mono small muted" style={{ wordBreak: "break-all" }}>{hook.url}</div>
      {q.isLoading ? <Loading /> : <ErrorBox error={q.error} />}
      {q.data && !q.data.length && <Empty title="No deliveries yet" />}
      {!!q.data?.length && (
        <table>
          <thead><tr><th>Event</th><th>Status</th><th>When</th><th /></tr></thead>
          <tbody>
            {q.data.map((d) => (
              <tr key={d.id}>
                <td className="mono small">{d.event}</td>
                <td>
                  <span className={`badge ${badge(d.status)}`}>{d.status}</span>
                  {d.attempts > 1 && <span className="muted small"> ×{d.attempts}</span>}
                  {d.last_error && <div className="small" style={{ color: "var(--danger)" }}>{d.last_error}</div>}
                </td>
                <td className="muted small">{formatDate(d.created_at)}</td>
                <td className="num">{d.status === "failed" && <Button write size="sm" onClick={() => retry.mutate(d.id)}>Retry</Button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Modal>
  );
}
