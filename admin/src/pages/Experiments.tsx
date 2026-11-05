import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap, type Schemas } from "../api/client";
import { useEnvContext } from "../api/hooks";
import { Button, Empty, ErrorBox, Field, Loading, Modal, confirmAction, formatDate, useToast } from "../components/ui";
import { EnvHead } from "./EnvHead";

type Exp = Schemas["ExperimentOut"];
type VariantDraft = { key: string; weight: string; overrides: string };

const STATUS_BADGE: Record<string, string> = { draft: "", running: "badge-ok", stopped: "badge-danger" };

export function ExperimentsPage() {
  const { envId } = useEnvContext();
  const path = { environment_id: envId };
  const qc = useQueryClient();
  const toast = useToast();
  const [editing, setEditing] = useState<Exp | "new" | null>(null);
  const [results, setResults] = useState<string | null>(null);
  const list = useQuery({
    queryKey: ["experiments", envId],
    queryFn: () => unwrap(api.GET("/admin/environments/{environment_id}/experiments", { params: { path } })),
  });
  const action = useMutation({
    mutationFn: ({ key, op }: { key: string; op: "start" | "stop" }) =>
      op === "start"
        ? unwrap(api.POST("/admin/environments/{environment_id}/experiments/{key}/start", { params: { path: { ...path, key } } }))
        : unwrap(api.POST("/admin/environments/{environment_id}/experiments/{key}/stop", { params: { path: { ...path, key } } })),
    onSuccess: (e) => {
      qc.invalidateQueries({ queryKey: ["experiments", envId] });
      toast(`${e.key} ${e.status}`);
    },
    onError: (e) => toast(e.message, true),
  });

  return (
    <>
      <EnvHead
        title="Experiments"
        description="A/B tests: players are assigned a sticky variant whose overrides are merged into remote config."
        actions={<Button write variant="primary" onClick={() => setEditing("new")}>New experiment</Button>}
      />
      <div className="card">
        <ErrorBox error={list.error} />
        {list.isLoading ? (
          <Loading />
        ) : list.error ? null : !list.data?.length ? (
          <Empty title="No experiments">Test prices, difficulty or UI variants on a share of players.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Experiment</th><th>Variants</th><th className="num">Traffic</th><th>Status</th><th /></tr></thead>
              <tbody>
                {list.data.map((e) => {
                  const total = e.variants.reduce((s, v) => s + v.weight, 0);
                  return (
                    <tr key={e.key}>
                      <td>
                        <div className="mono"><strong>{e.key}</strong></div>
                        {e.description && <div className="muted small">{e.description}</div>}
                      </td>
                      <td className="small">
                        {e.variants.map((v) => (
                          <div key={v.key}><span className="mono">{v.key}</span> <span className="muted">{Math.round((v.weight / total) * 100)}%</span></div>
                        ))}
                      </td>
                      <td className="num">{e.traffic_percent}%</td>
                      <td>
                        <span className={`badge ${STATUS_BADGE[e.status]}`}>{e.status}</span>
                        {e.started_at && <div className="muted small">since {formatDate(e.started_at)}</div>}
                      </td>
                      <td className="num">
                        <div className="row" style={{ justifyContent: "flex-end" }}>
                          {e.status !== "draft" && <Button size="sm" onClick={() => setResults(e.key)}>Results</Button>}
                          {e.status === "draft" && (
                            <Button write size="sm" variant="primary" onClick={() => confirmAction(`Start "${e.key}"? Players will be assigned variants.`) && action.mutate({ key: e.key, op: "start" })}>
                              Start
                            </Button>
                          )}
                          {e.status === "running" && (
                            <Button write size="sm" variant="danger" onClick={() => confirmAction(`Stop "${e.key}"? Everyone returns to the base config.`) && action.mutate({ key: e.key, op: "stop" })}>
                              Stop
                            </Button>
                          )}
                          {e.status !== "stopped" && <Button size="sm" onClick={() => setEditing(e)}>Edit</Button>}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {editing && <ExperimentEditor envId={envId} exp={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
      {results && <ResultsModal envId={envId} expKey={results} onClose={() => setResults(null)} />}
    </>
  );
}

function ExperimentEditor({ envId, exp, onClose }: { envId: string; exp: Exp | null; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [key, setKey] = useState(exp?.key ?? "");
  const [description, setDescription] = useState(exp?.description ?? "");
  const [traffic, setTraffic] = useState(exp?.traffic_percent ?? 100);
  const [variants, setVariants] = useState<VariantDraft[]>(
    exp?.variants.map((v) => ({ key: v.key, weight: String(v.weight), overrides: JSON.stringify(v.overrides ?? {}, null, 2) })) ?? [
      { key: "control", weight: "50", overrides: "{}" },
      { key: "treatment", weight: "50", overrides: "{}" },
    ],
  );
  const started = !!exp && exp.status !== "draft";
  const parsed = variants.map((v) => {
    try {
      const o: unknown = JSON.parse(v.overrides || "{}");
      return o && typeof o === "object" && !Array.isArray(o) ? (o as Record<string, unknown>) : null;
    } catch {
      return null;
    }
  });
  const valid =
    /^[a-z0-9][a-z0-9_.-]{0,127}$/.test(key) &&
    variants.length >= 2 &&
    variants.every((v, i) => /^[a-z0-9][a-z0-9_-]{0,63}$/.test(v.key) && Number(v.weight) >= 1 && parsed[i]) &&
    new Set(variants.map((v) => v.key)).size === variants.length;

  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PUT("/admin/environments/{environment_id}/experiments/{key}", {
          params: { path: { environment_id: envId, key } },
          body: {
            description: description.trim() || null,
            traffic_percent: traffic,
            rules: exp?.rules ?? [],
            variants: variants.map((v, i) => ({ key: v.key, weight: Number(v.weight), overrides: parsed[i]! })),
          },
        }),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["experiments", envId] });
      toast(exp ? "Experiment saved" : "Experiment created");
      onClose();
    },
  });
  const set = (i: number, patch: Partial<VariantDraft>) => setVariants((vs) => vs.map((v, j) => (j === i ? { ...v, ...patch } : v)));

  return (
    <Modal
      title={exp ? `Edit ${exp.key}` : "New experiment"}
      onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button><Button write variant="primary" busy={save.isPending} disabled={!valid} onClick={() => save.mutate()}>Save</Button></>}
    >
      <ErrorBox error={save.error} />
      <Field label="Key" hint="config.GetVariant(key) in the SDK">
        <input className="input mono" value={key} disabled={!!exp} onChange={(e) => setKey(e.target.value.toLowerCase())} />
      </Field>
      <Field label="Hypothesis / description">
        <input className="input" value={description} maxLength={500} onChange={(e) => setDescription(e.target.value)} />
      </Field>
      <Field label={`Traffic: ${traffic}% of players enrolled`} hint="Players outside the traffic get no variant and the base config.">
        <input type="range" min={0} max={100} value={traffic} onChange={(e) => setTraffic(Number(e.target.value))} />
      </Field>
      {started && <div className="alert alert-warn">Running experiment: variant keys are fixed. Changing weights only affects newly enrolled players.</div>}
      {variants.map((v, i) => (
        <div key={i} className="card card-pad stack" style={{ gap: 8 }}>
          <div className="row">
            <input className="input mono" style={{ flex: 1 }} value={v.key} disabled={started} onChange={(e) => set(i, { key: e.target.value.toLowerCase() })} aria-label="Variant key" />
            <input className="input" style={{ width: 90 }} inputMode="numeric" value={v.weight} onChange={(e) => set(i, { weight: e.target.value.replace(/\D/g, "") })} aria-label="Weight" />
            {!started && variants.length > 2 && <Button size="sm" variant="ghost" onClick={() => setVariants((vs) => vs.filter((_, j) => j !== i))} aria-label="Remove variant">✕</Button>}
          </div>
          <Field label="Config overrides (JSON, deep-merged)" error={parsed[i] ? null : "Must be a JSON object"}>
            <textarea rows={3} value={v.overrides} onChange={(e) => set(i, { overrides: e.target.value })} aria-label={`Overrides for ${v.key}`} />
          </Field>
        </div>
      ))}
      {!started && variants.length < 10 && (
        <div><Button size="sm" onClick={() => setVariants((vs) => [...vs, { key: `variant_${vs.length}`, weight: "50", overrides: "{}" }])}>Add variant</Button></div>
      )}
    </Modal>
  );
}

function ResultsModal({ envId, expKey, onClose }: { envId: string; expKey: string; onClose: () => void }) {
  const q = useQuery({
    queryKey: ["exp-results", envId, expKey],
    queryFn: () => unwrap(api.GET("/admin/environments/{environment_id}/experiments/{key}/results", { params: { path: { environment_id: envId, key: expKey } } })),
  });
  return (
    <Modal title={`Results: ${expKey}`} onClose={onClose} footer={<Button onClick={onClose}>Close</Button>}>
      {q.isLoading ? <Loading /> : <ErrorBox error={q.error} />}
      {q.data && (
        <>
          <table>
            <thead><tr><th>Variant</th><th className="num">Players exposed</th><th className="num">Share</th></tr></thead>
            <tbody>
              {Object.entries(q.data.exposures).map(([k, n]) => (
                <tr key={k}><td className="mono">{k}</td><td className="num">{n.toLocaleString()}</td><td className="num">{q.data.total ? Math.round((n / q.data.total) * 100) : 0}%</td></tr>
              ))}
            </tbody>
          </table>
          <p className="muted small" style={{ margin: 0 }}>
            Conversion metrics live in your analytics tool: send <code>experiment.exposure</code> and <code>player.event</code> webhooks there.
          </p>
        </>
      )}
    </Modal>
  );
}
