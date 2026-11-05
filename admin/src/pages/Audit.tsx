import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap } from "../api/client";
import { Button, Empty, ErrorBox, Loading, PageHead, formatDate } from "../components/ui";

export function AuditPage() {
  const [offset, setOffset] = useState(0);
  const limit = 50;
  const log = useQuery({
    queryKey: ["audit", offset],
    queryFn: () => unwrap(api.GET("/admin/audit-log", { params: { query: { offset, limit } } })),
    placeholderData: (prev) => prev,
  });
  const total = log.data?.total ?? 0;
  return (
    <>
      <PageHead title="Audit log" description="Every successful change made through the admin API." />
      <div className="card">
        <ErrorBox error={log.error} />
        {log.isLoading ? (
          <Loading />
        ) : log.error ? null : !log.data?.entries.length ? (
          <Empty title="No changes recorded yet" />
        ) : (
          <>
            <div className="table-wrap">
              <table>
                <thead><tr><th>When</th><th>Who</th><th>Action</th></tr></thead>
                <tbody>
                  {log.data.entries.map((e) => (
                    <tr key={e.id}>
                      <td className="muted small" style={{ whiteSpace: "nowrap" }}>{formatDate(e.at)}</td>
                      <td>{e.actor}</td>
                      <td className="mono small"><span className="badge">{e.method}</span> {e.path}</td>
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
    </>
  );
}
