import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
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
  useEffect(() => setText(liveText), [liveText]);

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
                <Button
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

      <div className="card">
        <div className="card-head">
          <h2>History</h2>
        </div>
        {versions.isLoading ? (
          <Loading />
        ) : !versions.data?.length ? (
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
            <Button variant="primary" busy={props.busy} onClick={props.onRollback}>
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
