import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap, type Condition, type Flag } from "../api/client";
import { useCanEdit } from "../api/auth";
import { useEnvContext } from "../api/hooks";
import { Button, Empty, ErrorBox, Field, Loading, Modal, confirmAction, useToast } from "../components/ui";
import { EnvHead } from "./EnvHead";

const OPS: { value: Condition["op"]; label: string; list?: boolean; num?: boolean }[] = [
  { value: "eq", label: "equals" },
  { value: "neq", label: "not equals" },
  { value: "in", label: "is one of", list: true },
  { value: "not_in", label: "is not one of", list: true },
  { value: "gte", label: "≥ (number)", num: true },
  { value: "lt", label: "< (number)", num: true },
  { value: "semver_gte", label: "version ≥" },
  { value: "semver_lt", label: "version <" },
];
const opMeta = (op: string) => OPS.find((o) => o.value === op) ?? OPS[0]!;

type RuleDraft = { attribute: string; op: Condition["op"]; value: string };
const toDraft = (c: Condition): RuleDraft => ({
  attribute: c.attribute,
  op: c.op,
  value: Array.isArray(c.value) ? c.value.join(", ") : String(c.value),
});
function fromDraft(d: RuleDraft): Condition {
  const m = opMeta(d.op);
  const value = m.list
    ? d.value.split(",").map((s) => s.trim()).filter(Boolean)
    : m.num
      ? Number(d.value)
      : d.value.trim();
  return { attribute: d.attribute.trim(), op: d.op, value };
}

function describe(c: Condition): string {
  const v = Array.isArray(c.value) ? `[${c.value.join(", ")}]` : String(c.value);
  return `${c.attribute} ${opMeta(c.op).label} ${v}`;
}

export function FlagsPage() {
  const { envId } = useEnvContext();
  const path = { environment_id: envId };
  const qc = useQueryClient();
  const toast = useToast();
  const [editing, setEditing] = useState<Flag | "new" | null>(null);
  const canEdit = useCanEdit();
  const flags = useQuery({
    queryKey: ["flags", envId],
    queryFn: () => unwrap(api.GET("/admin/environments/{environment_id}/flags", { params: { path } })),
  });
  const toggle = useMutation({
    mutationFn: (f: Flag) =>
      unwrap(
        api.PUT("/admin/environments/{environment_id}/flags/{key}", {
          params: { path: { environment_id: envId, key: f.key } },
          body: { ...f, enabled: !f.enabled },
        }),
      ),
    onSuccess: (f) => {
      qc.invalidateQueries({ queryKey: ["flags", envId] });
      toast(`${f.key} ${f.enabled ? "enabled" : "disabled"}`);
    },
    onError: (e) => toast(e.message, true),
  });
  const del = useMutation({
    mutationFn: (key: string) =>
      unwrap(api.DELETE("/admin/environments/{environment_id}/flags/{key}", { params: { path: { environment_id: envId, key } } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["flags", envId] });
      setEditing(null);
      toast("Flag deleted");
    },
    onError: (e) => toast(e.message, true),
  });

  return (
    <>
      <EnvHead
        title="Feature flags"
        description="Evaluated per player: enabled → all rules match → inside rollout %."
        actions={<Button write variant="primary" onClick={() => setEditing("new")}>New flag</Button>}
      />
      <div className="card">
        <ErrorBox error={flags.error} />
        {flags.isLoading ? (
          <Loading />
        ) : flags.error ? null : !flags.data?.length ? (
          <Empty title="No feature flags">Flags let you roll features out gradually or to specific segments.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Flag</th>
                  <th>Targeting</th>
                  <th className="num">Rollout</th>
                  <th>Enabled</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {flags.data.map((f) => (
                  <tr key={f.key}>
                    <td>
                      <div className="mono"><strong>{f.key}</strong></div>
                      {f.description && <div className="muted small">{f.description}</div>}
                    </td>
                    <td className="small">
                      {f.rules.length ? f.rules.map((r, i) => <div key={i} className="mono">{describe(r)}</div>) : <span className="muted">everyone</span>}
                    </td>
                    <td className="num">{f.rollout_percent}%</td>
                    <td>
                      <label className="switch">
                        <input
                          type="checkbox"
                          checked={f.enabled}
                          disabled={!canEdit || (toggle.isPending && toggle.variables?.key === f.key)}
                          onChange={() => toggle.mutate(f)}
                          aria-label={`Toggle ${f.key}`}
                        />
                      </label>
                    </td>
                    <td className="num">
                      <Button size="sm" onClick={() => setEditing(f)}>Edit</Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {editing && (
        <FlagEditor
          envId={envId}
          flag={editing === "new" ? null : editing}
          existing={flags.data?.map((f) => f.key) ?? []}
          onClose={() => setEditing(null)}
          onDelete={(key) => confirmAction(`Delete flag "${key}"? Clients will see it as off.`) && del.mutate(key)}
        />
      )}
    </>
  );
}

function FlagEditor({
  envId,
  flag,
  existing,
  onClose,
  onDelete,
}: {
  envId: string;
  flag: Flag | null;
  existing: string[];
  onClose: () => void;
  onDelete: (key: string) => void;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const [key, setKey] = useState(flag?.key ?? "");
  const [description, setDescription] = useState(flag?.description ?? "");
  const [enabled, setEnabled] = useState(flag?.enabled ?? false);
  const [rollout, setRollout] = useState(flag?.rollout_percent ?? 100);
  const [rules, setRules] = useState<RuleDraft[]>(flag?.rules.map(toDraft) ?? []);

  const keyError = !flag
    ? !/^[a-z0-9][a-z0-9_.-]{0,127}$/.test(key)
      ? key
        ? "Lowercase letters, digits, _ . -"
        : null
      : existing.includes(key)
        ? "A flag with this key already exists"
        : null
    : null;
  const rulesValid = rules.every((r) => r.attribute.trim() && r.value.trim() && (!opMeta(r.op).num || !isNaN(Number(r.value))));

  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PUT("/admin/environments/{environment_id}/flags/{key}", {
          params: { path: { environment_id: envId, key } },
          body: {
            description: description.trim() || null,
            enabled,
            rollout_percent: rollout,
            rules: rules.map(fromDraft),
          },
        }),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["flags", envId] });
      toast(flag ? "Flag saved" : "Flag created");
      onClose();
    },
  });

  const setRule = (i: number, patch: Partial<RuleDraft>) =>
    setRules((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  return (
    <Modal
      title={flag ? `Edit ${flag.key}` : "New flag"}
      onClose={onClose}
      footer={
        <>
          {flag && (
            <Button write variant="danger" onClick={() => onDelete(flag.key)} style={{ marginRight: "auto" }}>
              Delete
            </Button>
          )}
          <Button onClick={onClose}>Cancel</Button>
          <Button write
            variant="primary"
            busy={save.isPending}
            disabled={!key || !!keyError || !rulesValid}
            onClick={() => save.mutate()}
          >
            Save
          </Button>
        </>
      }
    >
      <ErrorBox error={save.error} />
      <Field label="Key" error={keyError} hint="Used in code: config.isEnabled(key)">
        <input className="input mono" value={key} disabled={!!flag} onChange={(e) => setKey(e.target.value)} />
      </Field>
      <Field label="Description">
        <input className="input" value={description} maxLength={500} onChange={(e) => setDescription(e.target.value)} />
      </Field>
      <label className="switch">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        <span>{enabled ? "Enabled" : "Disabled"}</span>
      </label>
      <Field label={`Rollout: ${rollout}% of matching players`} hint="Bucketing is stable per player.">
        <input type="range" min={0} max={100} value={rollout} onChange={(e) => setRollout(Number(e.target.value))} />
      </Field>
      <div className="stack" style={{ gap: 8 }}>
        <div className="row">
          <strong>Targeting rules</strong>
          <span className="muted small">all must match · attributes come from the client (platform, app_version…)</span>
        </div>
        {rules.map((r, i) => (
          <div key={i} className="rule-row">
            <input className="input mono" placeholder="attribute" value={r.attribute} onChange={(e) => setRule(i, { attribute: e.target.value })} aria-label="Attribute" />
            <select value={r.op} onChange={(e) => setRule(i, { op: e.target.value as Condition["op"] })} aria-label="Operator">
              {OPS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
            <input
              className="input mono"
              placeholder={opMeta(r.op).list ? "a, b, c" : opMeta(r.op).num ? "10" : r.op.startsWith("semver") ? "1.4.0" : "value"}
              value={r.value}
              onChange={(e) => setRule(i, { value: e.target.value })}
              aria-label="Value"
            />
            <Button size="sm" variant="ghost" onClick={() => setRules((rs) => rs.filter((_, j) => j !== i))} aria-label="Remove rule">✕</Button>
          </div>
        ))}
        <div>
          <Button size="sm" onClick={() => setRules((rs) => [...rs, { attribute: "platform", op: "eq", value: "" }])}>
            Add rule
          </Button>
        </div>
      </div>
    </Modal>
  );
}
