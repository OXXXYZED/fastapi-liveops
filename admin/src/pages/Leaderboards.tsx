import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap, type Leaderboard, type Schemas } from "../api/client";
import { useEnvContext } from "../api/hooks";
import { Button, Empty, ErrorBox, Field, Loading, Modal, confirmAction, useToast } from "../components/ui";
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
        actions={<Button variant="primary" onClick={() => setCreating(true)}>New leaderboard</Button>}
      />
      <ErrorBox error={boards.error} />
      {boards.isLoading ? (
        <div className="card"><Loading /></div>
      ) : !boards.data?.length ? (
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
          </div>
        </div>
        <div className="row">
          {board.reset_schedule !== "never" && (
            <select value={season} onChange={(e) => { setSeason(e.target.value); setOffset(0); }} aria-label="Season">
              <option value="current">Current season</option>
              <option value="previous">Previous season</option>
            </select>
          )}
          <Button
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
      ) : !entries.data?.entries.length ? (
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
                    <td className="mono">{e.player_id}</td>
                    <td className="num">{e.score.toLocaleString()}</td>
                    <td className="num">
                      <Button
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
    </div>
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
          body: { ...form, min_score: num(form.min_score), max_score: num(form.max_score) },
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
