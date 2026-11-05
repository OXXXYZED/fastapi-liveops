import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api, unwrap } from "../api/client";
import { useEnvContext } from "../api/hooks";
import { Button, Empty, ErrorBox, Field, Loading, Modal, confirmAction, formatDate, useToast } from "../components/ui";
import { EnvHead } from "./EnvHead";

const pretty = (v: unknown) => JSON.stringify(v, null, 2);

export function ConfigPage() {
  const { envId } = useEnvContext();
  const path = { environment_id: envId };
  const qc = useQueryClient();
  const toast = useToast();

  const live = useQuery({
    queryKey: ["config", envId],
    queryFn: async () => {
      try {
        return await unwrap(api.GET("/admin/environments/{environment_id}/config", { params: { path } }));
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null; // nothing published yet
        throw e;
      }
    },
  });
  const versions = useQuery({
    queryKey: ["config-versions", envId],
    queryFn: () => unwrap(api.GET("/admin/environments/{environment_id}/config/versions", { params: { path } })),
  });

  const liveVersion = live.data?.version ?? 0;
  const liveText = useMemo(() => pretty(live.data?.values ?? {}), [live.data]);
  const [text, setText] = useState(liveText);
  const [note, setNote] = useState("");
  const [viewing, setViewing] = useState<number | null>(null);
  const [scheduling, setScheduling] = useState(false);
  // Follow the server's live config, but never overwrite edits the user has started.
  const syncedText = useRef(liveText);
  useEffect(() => {
    setText((current) => (current === syncedText.current ? liveText : current));
    syncedText.current = liveText;
  }, [liveText]);

  const parsed = useMemo(() => {
    try {
      const v: unknown = JSON.parse(text);
      if (!v || typeof v !== "object" || Array.isArray(v)) return { error: "Config must be a JSON object" };
      return { value: v as Record<string, unknown> };
    } catch (e) {
      return { error: (e as Error).message };
    }
  }, [text]);
  const dirty = text !== liveText;

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["config", envId] });
    qc.invalidateQueries({ queryKey: ["config-versions", envId] });
  };
  const publish = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/admin/environments/{environment_id}/config", {
          params: { path },
          body: { values: parsed.value!, note: note.trim() || null, base_version: liveVersion },
        }),
      ),
    onSuccess: (v) => {
      setNote("");
      syncedText.current = text; // what's in the editor is now live
      refresh();
      toast(`Published v${v.version}`);
    },
  });
  const rollback = useMutation({
    mutationFn: (version: number) =>
      unwrap(
        api.POST("/admin/environments/{environment_id}/config/versions/{version}/rollback", {
          params: { path: { environment_id: envId, version } },
        }),
      ),
    onSuccess: (v) => {
      setViewing(null);
      refresh();
      toast(`Rolled back — now v${v.version}`);
    },
    onError: (e) => toast(e.message, true),
  });

  const conflict = publish.error instanceof ApiError && publish.error.status === 409;

  return (
    <>
      <EnvHead
        title="Remote config"
        description={
          live.data ? (
            <>
              Live version <strong>v{liveVersion}</strong> · published {formatDate(live.data.created_at)}
            </>
          ) : (
            "Nothing published yet."
          )
        }
      />
      <div className="card">
        <div className="card-head">
          <h2>Values</h2>
          <div className="row">
            {dirty && (
              <Button size="sm" variant="ghost" onClick={() => setText(liveText)}>
                Discard changes
              </Button>
            )}
            <Button size="sm" onClick={() => parsed.value && setText(pretty(parsed.value))} disabled={!parsed.value}>
              Format
            </Button>
          </div>
        </div>
        <div className="card-pad stack">
          {live.isLoading ? (
            <Loading rows={6} />
          ) : (
            <>
              <ErrorBox error={live.error} />
              <textarea
                aria-label="Config JSON"
                rows={Math.min(30, Math.max(10, text.split("\n").length + 1))}
                value={text}
                onChange={(e) => setText(e.target.value)}
                spellCheck={false}
              />
              {parsed.error && <div className="field-error">Invalid JSON: {parsed.error}</div>}
              {conflict ? (
                <div className="alert">
                  Someone published a newer version while you were editing.{" "}
                  <Button size="sm" onClick={() => { publish.reset(); refresh(); }}>
                    Reload latest
                  </Button>{" "}
                  (your edits will be replaced — copy them first if needed)
                </div>
              ) : (
                <ErrorBox error={publish.error} />
              )}
              <div className="row">
                <input
                  className="input"
                  style={{ flex: 1, minWidth: 200 }}
                  placeholder="Change note (optional)"
                  value={note}
                  maxLength={500}
                  onChange={(e) => setNote(e.target.value)}
                  aria-label="Change note"
                />
                <Button write disabled={!!parsed.error} onClick={() => setScheduling(true)}>
                  Schedule…
                </Button>
                <Button write
                  variant="primary"
                  busy={publish.isPending}
                  disabled={!dirty || !!parsed.error}
                  onClick={() => publish.mutate()}
                >
                  Publish v{liveVersion + 1}
                </Button>
              </div>
            </>
          )}
        </div>
      </div>

      <Schedules envId={envId} />

      <div className="card">
        <div className="card-head">
          <h2>History</h2>
        </div>
        {versions.isLoading ? (
          <Loading />
        ) : versions.error ? <ErrorBox error={versions.error} /> : !versions.data?.length ? (
          <Empty title="No versions yet" />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Version</th>
                  <th>Note</th>
                  <th>Published</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {versions.data.map((v) => (
                  <tr key={v.version}>
                    <td>
                      <strong>v{v.version}</strong>{" "}
                      {v.version === liveVersion && <span className="badge badge-ok">live</span>}
                    </td>
                    <td className="muted">{v.note ?? "—"}</td>
                    <td className="muted">{formatDate(v.created_at)}</td>
                    <td className="num">
                      <Button size="sm" onClick={() => setViewing(v.version)}>
                        View
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {scheduling && parsed.value && (
        <ScheduleModal envId={envId} values={parsed.value} note={note} onClose={() => setScheduling(false)} />
      )}
      {viewing !== null && (
        <VersionModal
          envId={envId}
          version={viewing}
          isLive={viewing === liveVersion}
          busy={rollback.isPending}
          onClose={() => setViewing(null)}
          onRollback={() =>
            confirmAction(`Publish a copy of v${viewing} as v${liveVersion + 1}?`) && rollback.mutate(viewing)
          }
        />
      )}
    </>
  );
}

function VersionModal(props: {
  envId: string;
  version: number;
  isLive: boolean;
  busy: boolean;
  onClose: () => void;
  onRollback: () => void;
}) {
  const q = useQuery({
    queryKey: ["config-version", props.envId, props.version],
    queryFn: () =>
      unwrap(
        api.GET("/admin/environments/{environment_id}/config/versions/{version}", {
          params: { path: { environment_id: props.envId, version: props.version } },
        }),
      ),
  });
  return (
    <Modal
      title={`Version ${props.version}`}
      onClose={props.onClose}
      footer={
        <>
          <Button onClick={props.onClose}>Close</Button>
          {!props.isLive && (
            <Button write variant="primary" busy={props.busy} onClick={props.onRollback}>
              Roll back to this version
            </Button>
          )}
        </>
      }
    >
      {q.isLoading ? <Loading /> : <ErrorBox error={q.error} />}
      {q.data && (
        <Field label={q.data.note ?? "No note"}>
          <textarea readOnly rows={14} value={pretty(q.data.values)} aria-label="Version JSON" />
        </Field>
      )}
    </Modal>
  );
}

function toLocalInput(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function ScheduleModal(props: { envId: string; values: Record<string, unknown>; note: string; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [at, setAt] = useState(() => toLocalInput(new Date(Date.now() + 3600_000)));
  const when = new Date(at);
  const valid = !isNaN(when.getTime()) && when.getTime() > Date.now();
  const m = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/admin/environments/{environment_id}/config/schedules", {
          params: { path: { environment_id: props.envId } },
          body: { values: props.values, note: props.note.trim() || null, publish_at: when.toISOString() },
        }),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["schedules", props.envId] });
      toast(`Scheduled for ${formatDate(when.toISOString())}`);
      props.onClose();
    },
  });
  return (
    <Modal
      title="Schedule publish"
      onClose={props.onClose}
      footer={<><Button onClick={props.onClose}>Cancel</Button><Button write variant="primary" busy={m.isPending} disabled={!valid} onClick={() => m.mutate()}>Schedule</Button></>}
    >
      <ErrorBox error={m.error} />
      <Field label="Publish at" hint={`Your local time (${Intl.DateTimeFormat().resolvedOptions().timeZone}). Publishes these exact values as a new version.`}>
        <input className="input" type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} />
      </Field>
    </Modal>
  );
}

function Schedules({ envId }: { envId: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({
    queryKey: ["schedules", envId],
    queryFn: () => unwrap(api.GET("/admin/environments/{environment_id}/config/schedules", { params: { path: { environment_id: envId } } })),
    refetchInterval: 30_000,
  });
  const cancel = useMutation({
    mutationFn: (id: string) =>
      unwrap(api.DELETE("/admin/environments/{environment_id}/config/schedules/{schedule_id}", { params: { path: { environment_id: envId, schedule_id: id } } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["schedules", envId] });
      toast("Schedule cancelled");
    },
    onError: (e) => toast(e.message, true),
  });
  const upcoming = q.data?.filter((s) => s.status === "pending" || s.status === "failed") ?? [];
  if (!upcoming.length) return null;
  return (
    <div className="card">
      <div className="card-head"><h2>Scheduled</h2></div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>When</th><th>Note</th><th>By</th><th>Status</th><th /></tr></thead>
          <tbody>
            {upcoming.map((s) => (
              <tr key={s.id}>
                <td><strong>{formatDate(s.publish_at)}</strong></td>
                <td className="muted">{s.note ?? "—"}</td>
                <td className="muted small">{s.created_by}</td>
                <td>
                  <span className={`badge ${s.status === "failed" ? "badge-danger" : "badge-accent"}`}>{s.status}</span>
                  {s.error && <div className="small" style={{ color: "var(--danger)" }}>{s.error}</div>}
                </td>
                <td className="num">
                  {s.status === "pending" && (
                    <Button write size="sm" variant="danger" onClick={() => confirmAction("Cancel this scheduled publish?") && cancel.mutate(s.id)}>Cancel</Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
