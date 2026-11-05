import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap } from "../api/client";
import { useEnvContext } from "../api/hooks";
import { Button, Empty, ErrorBox, Field, Loading, Modal, confirmAction, formatDate, useToast } from "../components/ui";
import { EnvHead } from "./EnvHead";

export function PlayersPage() {
  const { envId } = useEnvContext();
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const limit = 50;
  const players = useQuery({
    queryKey: ["players", envId, search, offset],
    queryFn: () =>
      unwrap(
        api.GET("/admin/environments/{environment_id}/players", {
          params: { path: { environment_id: envId }, query: { q: search || undefined, offset, limit } },
        }),
      ),
    placeholderData: (prev) => prev,
  });
  const total = players.data?.total ?? 0;

  return (
    <>
      <EnvHead title="Players & saves" description="Look up a player by id or device id to inspect cloud saves." />
      <form
        className="row"
        style={{ marginBottom: 12 }}
        onSubmit={(e) => {
          e.preventDefault();
          setOffset(0);
          setSearch(q.trim());
        }}
      >
        <input className="input" style={{ flex: 1, minWidth: 220 }} placeholder="Player id or device id" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search players" />
        <Button type="submit">Search</Button>
        {search && <Button variant="ghost" onClick={() => { setQ(""); setSearch(""); setOffset(0); }}>Clear</Button>}
      </form>
      <div className="card">
        <ErrorBox error={players.error} />
        {players.isLoading ? (
          <Loading />
        ) : players.error ? null : !players.data?.players.length ? (
          <Empty title={search ? "No player found" : "No players yet"} />
        ) : (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>Player id</th><th>Device id</th><th>Created</th><th>Last seen</th><th /></tr>
                </thead>
                <tbody>
                  {players.data.players.map((p) => (
                    <tr key={p.id}>
                      <td className="mono">{p.id}</td>
                      <td className="mono muted">{p.device_id ?? "—"}</td>
                      <td className="muted">{formatDate(p.created_at)}</td>
                      <td className="muted">{formatDate(p.last_seen_at)}</td>
                      <td className="num"><Button size="sm" onClick={() => setSelected(p.id)}>Saves</Button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="card-head" style={{ borderTop: "1px solid var(--border)", borderBottom: "none" }}>
              <span className="muted small">{offset + 1}–{Math.min(offset + limit, total)} of {total}</span>
              <div className="row">
                <Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>Previous</Button>
                <Button size="sm" disabled={offset + limit >= total} onClick={() => setOffset(offset + limit)}>Next</Button>
              </div>
            </div>
          </>
        )}
      </div>
      {selected && <SavesModal envId={envId} playerId={selected} onClose={() => setSelected(null)} />}
    </>
  );
}

function SavesModal({ envId, playerId, onClose }: { envId: string; playerId: string; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [slot, setSlot] = useState<string | null>(null);
  const path = { environment_id: envId, player_id: playerId };
  const saves = useQuery({
    queryKey: ["saves", envId, playerId],
    queryFn: () => unwrap(api.GET("/admin/environments/{environment_id}/players/{player_id}/saves", { params: { path } })),
  });
  const data = useQuery({
    queryKey: ["save", envId, playerId, slot],
    queryFn: () =>
      unwrap(api.GET("/admin/environments/{environment_id}/players/{player_id}/saves/{slot}", { params: { path: { ...path, slot: slot! } } })),
    enabled: !!slot,
  });
  const del = useMutation({
    mutationFn: (s: string) =>
      unwrap(api.DELETE("/admin/environments/{environment_id}/players/{player_id}/saves/{slot}", { params: { path: { ...path, slot: s } } })),
    onSuccess: () => {
      setSlot(null);
      qc.invalidateQueries({ queryKey: ["saves", envId, playerId] });
      toast("Save deleted");
    },
    onError: (e) => toast(e.message, true),
  });

  return (
    <Modal title="Cloud saves" onClose={onClose} footer={<Button onClick={onClose}>Close</Button>}>
      <div className="mono small muted">{playerId}</div>
      <ErrorBox error={saves.error} />
      {saves.isLoading ? (
        <Loading />
      ) : saves.error ? null : !saves.data?.length ? (
        <Empty title="No saves" />
      ) : (
        <table>
          <thead><tr><th>Slot</th><th className="num">Version</th><th className="num">Size</th><th>Updated</th><th /></tr></thead>
          <tbody>
            {saves.data.map((s) => (
              <tr key={s.slot}>
                <td className="mono">{s.slot}</td>
                <td className="num">{s.version}</td>
                <td className="num">{(s.size_bytes / 1024).toFixed(1)} KB</td>
                <td className="muted small">{formatDate(s.updated_at)}</td>
                <td className="num"><Button size="sm" onClick={() => setSlot(s.slot)}>View</Button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {slot && (
        <div className="stack">
          {data.isLoading ? <Loading /> : <ErrorBox error={data.error} />}
          {data.data && (
            <Field label={`Slot “${slot}” · v${data.data.version}`}>
              <textarea readOnly rows={12} value={JSON.stringify(data.data.data, null, 2)} aria-label="Save data" />
            </Field>
          )}
          <div className="row">
            <Button write
              variant="danger"
              size="sm"
              busy={del.isPending}
              onClick={() => confirmAction(`Delete save slot "${slot}"? The player will lose this progress.`) && del.mutate(slot)}
            >
              Delete slot
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
