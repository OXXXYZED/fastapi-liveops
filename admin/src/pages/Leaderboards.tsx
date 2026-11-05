import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap, type Leaderboard, type Schemas } from "../api/client";
import { useEnvContext } from "../api/hooks";
import { Button, CopyButton, Empty, ErrorBox, Field, Loading, Modal, confirmAction, useToast } from "../components/ui";
import { EnvHead } from "./EnvHead";

export function LeaderboardsPage() {
  const { envId } = useEnvContext();
  const path = { environment_id: envId };
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const boards = useQuery({
    queryKey: ["boards", envId],
    queryFn: () => unwrap(api.GET("/admin/environments/{environment_id}/leaderboards", { params: { path } })),
  });
  const current = boards.data?.find((b) => b.key === selected) ?? boards.data?.[0];

  return (
    <>
      <EnvHead
        title="Leaderboards"
        actions={<Button write variant="primary" onClick={() => setCreating(true)}>New leaderboard</Button>}
      />
      <ErrorBox error={boards.error} />
      {boards.isLoading ? (
        <div className="card"><Loading /></div>
      ) : boards.error ? null : !boards.data?.length ? (
        <div className="card"><Empty title="No leaderboards">Create one, then submit scores from the SDK.</Empty></div>
      ) : (
        <>
          <div className="row" style={{ marginBottom: 12 }} role="tablist">
            {boards.data.map((b) => (
              <Button
                key={b.key}
                size="sm"
                role="tab"
                aria-selected={b.key === current?.key}
                variant={b.key === current?.key ? "primary" : undefined}
                onClick={() => setSelected(b.key)}
              >
                {b.name}
              </Button>
            ))}
          </div>
          {current && <BoardView key={current.key} envId={envId} board={current} onDeleted={() => setSelected(null)} />}
        </>
      )}
      {creating && <CreateBoard envId={envId} onClose={() => setCreating(false)} onCreated={(k) => { setSelected(k); setCreating(false); }} />}
    </>
  );
}

function BoardView({ envId, board, onDeleted }: { envId: string; board: Leaderboard; onDeleted: () => void }) {
  const [season, setSeason] = useState("current");
  const [offset, setOffset] = useState(0);
  const [antiCheat, setAntiCheat] = useState(false);
  const limit = 50;
  const qc = useQueryClient();
  const toast = useToast();
  const p = { environment_id: envId, key: board.key };
  const entries = useQuery({
    queryKey: ["entries", envId, board.key, season, offset],
    queryFn: () =>
      unwrap(
        api.GET("/admin/environments/{environment_id}/leaderboards/{key}/entries", {
          params: { path: p, query: { season, offset, limit } },
        }),
      ),
    placeholderData: (prev) => prev,
  });
  const remove = useMutation({
    mutationFn: (playerId: string) =>
      unwrap(
        api.DELETE("/admin/environments/{environment_id}/leaderboards/{key}/scores/{player_id}", {
          params: { path: { ...p, player_id: playerId }, query: { season: entries.data?.season ?? season } },
        }),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["entries", envId, board.key] });
      toast("Score removed");
    },
    onError: (e) => toast(e.message, true),
  });
  const del = useMutation({
    mutationFn: () => unwrap(api.DELETE("/admin/environments/{environment_id}/leaderboards/{key}", { params: { path: p } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["boards", envId] });
      onDeleted();
      toast("Leaderboard deleted");
    },
    onError: (e) => toast(e.message, true),
  });
  const total = entries.data?.total ?? 0;

  return (
    <div className="card">
      <div className="card-head">
        <div>
          <h2>{board.name} <span className="muted mono small">{board.key}</span></h2>
          <div className="row small muted" style={{ marginTop: 4 }}>
            <span className="badge">{board.sort_order === "desc" ? "higher is better" : "lower is better"}</span>
            <span className="badge">{board.update_mode}</span>
            <span className="badge">resets {board.reset_schedule}</span>
            {(board.min_score !== null || board.max_score !== null) && (
              <span className="badge">range {board.min_score ?? "−∞"} … {board.max_score ?? "∞"}</span>
            )}
            {board.max_submits_per_minute && <span className="badge">≤ {board.max_submits_per_minute} submits/min</span>}
            {board.validation_url && <span className="badge badge-accent">validated{board.validation_fail_open ? " (fail-open)" : ""}</span>}
          </div>
        </div>
        <div className="row">
          {board.reset_schedule !== "never" && (
            <select value={season} onChange={(e) => { setSeason(e.target.value); setOffset(0); }} aria-label="Season">
              <option value="current">Current season</option>
              <option value="previous">Previous season</option>
            </select>
          )}
          <Button size="sm" onClick={() => setAntiCheat(true)}>Anti-cheat</Button>
          <Button write
            variant="danger"
            size="sm"
            busy={del.isPending}
            onClick={() => confirmAction(`Delete "${board.name}" and all its scores?`) && del.mutate()}
          >
            Delete
          </Button>
        </div>
      </div>
      <ErrorBox error={entries.error} />
      {entries.isLoading ? (
        <Loading />
      ) : entries.error ? null : !entries.data?.entries.length ? (
        <Empty title="No scores">{entries.data && <span className="mono">season {entries.data.season}</span>}</Empty>
      ) : (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th className="num" style={{ width: 80 }}>Rank</th>
                  <th>Player</th>
                  <th className="num">Score</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {entries.data.entries.map((e: Schemas["EntryOut"]) => (
                  <tr key={e.player_id}>
                    <td className="num"><strong>{e.rank}</strong></td>
                    <td>{e.display_name ? <><strong>{e.display_name}</strong> <span className="mono small muted">{e.player_id.slice(0, 8)}</span></> : <span className="mono">{e.player_id}</span>}</td>
                    <td className="num">{e.score.toLocaleString()}</td>
                    <td className="num">
                      <Button write
                        size="sm"
                        variant="danger"
                        onClick={() => confirmAction("Remove this player's score from the season?") && remove.mutate(e.player_id)}
                      >
                        Remove
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="card-head" style={{ borderTop: "1px solid var(--border)", borderBottom: "none" }}>
            <span className="muted small">
              {offset + 1}–{Math.min(offset + limit, total)} of {total} · season <span className="mono">{entries.data.season}</span>
            </span>
            <div className="row">
              <Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>Previous</Button>
              <Button size="sm" disabled={offset + limit >= total} onClick={() => setOffset(offset + limit)}>Next</Button>
            </div>
          </div>
        </>
      )}
      {antiCheat && <AntiCheatModal envId={envId} board={board} onClose={() => setAntiCheat(false)} />}
    </div>
  );
}

function AntiCheatModal({ envId, board, onClose }: { envId: string; board: Leaderboard; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [form, setForm] = useState({
    min: board.min_score?.toString() ?? "",
    max: board.max_score?.toString() ?? "",
    rate: board.max_submits_per_minute?.toString() ?? "",
    url: board.validation_url ?? "",
    failOpen: board.validation_fail_open,
  });
  const [secret, setSecret] = useState<string | null>(null);
  const set = (p: Partial<typeof form>) => setForm((f) => ({ ...f, ...p }));
  const num = (s: string) => (s.trim() === "" ? null : Number(s));
  const path = { environment_id: envId, key: board.key };
  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PATCH("/admin/environments/{environment_id}/leaderboards/{key}", {
          params: { path },
          body: {
            min_score: num(form.min),
            max_score: num(form.max),
            max_submits_per_minute: num(form.rate),
            validation_url: form.url.trim() || null,
            validation_fail_open: form.failOpen,
          },
        }),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["boards", envId] });
      toast("Anti-cheat settings saved");
      onClose();
    },
  });
  const rotate = useMutation({
    mutationFn: () => unwrap(api.POST("/admin/environments/{environment_id}/leaderboards/{key}/validation-secret", { params: { path } })),
    onSuccess: (r) => setSecret(r.secret),
    onError: (e) => toast(e.message, true),
  });
  const ints = [form.min, form.max, form.rate].every((s) => s.trim() === "" || Number.isInteger(Number(s)));
  const urlOk = form.url.trim() === "" || /^https?:\/\/\S+/.test(form.url.trim());
  return (
    <Modal
      title={`Anti-cheat: ${board.name}`}
      onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button><Button write variant="primary" busy={save.isPending} disabled={!ints || !urlOk} onClick={() => save.mutate()}>Save</Button></>}
    >
      <ErrorBox error={save.error} />
      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr 1fr" }}>
        <Field label="Min score"><input className="input" inputMode="numeric" value={form.min} onChange={(e) => set({ min: e.target.value })} /></Field>
        <Field label="Max score"><input className="input" inputMode="numeric" value={form.max} onChange={(e) => set({ max: e.target.value })} /></Field>
        <Field label="Submits / min / player"><input className="input" inputMode="numeric" value={form.rate} onChange={(e) => set({ rate: e.target.value })} /></Field>
      </div>
      <Field
        label="Validation hook URL"
        hint={<>Called before each score is stored with a signed JSON body; reply <code>{"{\"allow\": true}"}</code>, optionally with an adjusted <code>score</code>, or <code>{"{\"allow\": false, \"reason\": …}"}</code>.</>}
        error={urlOk ? null : "Must start with http:// or https://"}
      >
        <input className="input mono" placeholder="https://anticheat.example.com/validate" value={form.url} onChange={(e) => set({ url: e.target.value })} />
      </Field>
      <label className="switch">
        <input type="checkbox" checked={form.failOpen} onChange={(e) => set({ failOpen: e.target.checked })} />
        <span>Accept scores when the validator is down (fail-open)</span>
      </label>
      {board.validation_url && (
        <div className="stack" style={{ gap: 8 }}>
          {secret ? (
            <>
              <div className="alert alert-warn">New signing secret — update your validator now; the old one no longer works.</div>
              <div className="secret">{secret}</div>
              <div><CopyButton text={secret} /></div>
            </>
          ) : (
            <div>
              <Button write size="sm" busy={rotate.isPending} onClick={() => confirmAction("Generate a new signing secret? The current one stops working immediately.") && rotate.mutate()}>
                Rotate signing secret
              </Button>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}

function CreateBoard({ envId, onClose, onCreated }: { envId: string; onClose: () => void; onCreated: (key: string) => void }) {
  const qc = useQueryClient();
  const [form, setForm] = useState({
    key: "",
    name: "",
    sort_order: "desc" as Leaderboard["sort_order"],
    update_mode: "best" as Leaderboard["update_mode"],
    reset_schedule: "never" as Leaderboard["reset_schedule"],
    min_score: "",
    max_score: "",
  });
  const set = (patch: Partial<typeof form>) => setForm((f) => ({ ...f, ...patch }));
  const num = (s: string) => (s.trim() === "" ? null : Number(s));
  const m = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/admin/environments/{environment_id}/leaderboards", {
          params: { path: { environment_id: envId } },
          body: { ...form, min_score: num(form.min_score), max_score: num(form.max_score), validation_fail_open: false },
        }),
      ),
    onSuccess: (b) => {
      qc.invalidateQueries({ queryKey: ["boards", envId] });
      onCreated(b.key);
    },
  });
  const valid =
    form.name.trim() &&
    /^[a-z0-9][a-z0-9_.-]{0,127}$/.test(form.key) &&
    [form.min_score, form.max_score].every((s) => s.trim() === "" || Number.isInteger(Number(s)));

  return (
    <Modal
      title="New leaderboard"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={m.isPending} disabled={!valid} onClick={() => m.mutate()}>Create</Button>
        </>
      }
    >
      <ErrorBox error={m.error} />
      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <Field label="Name"><input className="input" value={form.name} onChange={(e) => set({ name: e.target.value })} /></Field>
        <Field label="Key" hint="Used by the SDK"><input className="input mono" value={form.key} onChange={(e) => set({ key: e.target.value.toLowerCase() })} /></Field>
        <Field label="Ranking">
          <select value={form.sort_order} onChange={(e) => set({ sort_order: e.target.value as Leaderboard["sort_order"] })}>
            <option value="desc">Higher is better</option>
            <option value="asc">Lower is better (times)</option>
          </select>
        </Field>
        <Field label="On new score">
          <select value={form.update_mode} onChange={(e) => set({ update_mode: e.target.value as Leaderboard["update_mode"] })}>
            <option value="best">Keep best</option>
            <option value="latest">Keep latest</option>
            <option value="sum">Add up</option>
          </select>
        </Field>
        <Field label="Reset">
          <select value={form.reset_schedule} onChange={(e) => set({ reset_schedule: e.target.value as Leaderboard["reset_schedule"] })}>
            <option value="never">Never</option>
            <option value="daily">Daily (UTC)</option>
            <option value="weekly">Weekly (ISO, UTC)</option>
            <option value="monthly">Monthly (UTC)</option>
          </select>
        </Field>
        <div />
        <Field label="Min score" hint="Optional anti-cheat bound"><input className="input" inputMode="numeric" value={form.min_score} onChange={(e) => set({ min_score: e.target.value })} /></Field>
        <Field label="Max score"><input className="input" inputMode="numeric" value={form.max_score} onChange={(e) => set({ max_score: e.target.value })} /></Field>
      </div>
      <p className="muted small" style={{ margin: 0 }}>Ranking, update mode and reset can't be changed after creation.</p>
    </Modal>
  );
}
