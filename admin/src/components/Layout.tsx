import { useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useMe } from "../api/auth";
import { useEnvContext, useProjects } from "../api/hooks";
import { Button } from "./ui";

const SECTIONS = [
  ["config", "Remote config"],
  ["flags", "Feature flags"],
  ["leaderboards", "Leaderboards"],
  ["players", "Players & saves"],
  ["keys", "API keys"],
] as const;

export function Layout({ onLogout }: { onLogout: () => void }) {
  const [open, setOpen] = useState(false);
  const me = useMe();
  const { projectId, envId, project } = useEnvContext();
  const projects = useProjects();
  const navigate = useNavigate();
  const location = useLocation();
  const section = location.pathname.split("/").pop() ?? "config";

  return (
    <div className="app">
      <div className="mobile-bar">
        <span className="brand">
          <span className="brand-dot" /> LiveOps
        </span>
        <Button size="sm" onClick={() => setOpen(true)} aria-label="Open menu">
          Menu
        </Button>
      </div>
      <aside className={`sidebar ${open ? "open" : ""}`} onClick={(e) => (e.target as HTMLElement).closest("a") && setOpen(false)}>
        <div className="row">
          <Link to="/" className="brand" style={{ color: "inherit", textDecoration: "none" }}>
            <span className="brand-dot" /> LiveOps
          </Link>
          <span className="spacer" />
          {open && (
            <Button size="sm" variant="ghost" onClick={() => setOpen(false)} aria-label="Close menu">
              ✕
            </Button>
          )}
        </div>

        {projectId && (
          <div className="stack" style={{ gap: 8 }}>
            <label className="field">
              <span className="nav-label" style={{ padding: 0 }}>Project</span>
              <select
                value={projectId}
                onChange={(e) => navigate(`/projects/${e.target.value}`)}
                aria-label="Project"
              >
                {projects.data?.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
            {project && (
              <label className="field">
                <span className="nav-label" style={{ padding: 0 }}>Environment</span>
                <select
                  value={envId}
                  onChange={(e) =>
                    navigate(`/projects/${projectId}/env/${e.target.value}/${envId ? section : "config"}`)
                  }
                  aria-label="Environment"
                >
                  {!envId && <option value="">Select…</option>}
                  {project.environments.map((e) => (
                    <option key={e.id} value={e.id}>
                      {e.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
        )}

        {envId && (
          <nav className="nav nav-group">
            {SECTIONS.map(([path, label]) => (
              <NavLink key={path} to={`/projects/${projectId}/env/${envId}/${path}`}>
                {label}
              </NavLink>
            ))}
          </nav>
        )}

        <nav className="nav nav-group">
          <NavLink to="/" end>
            All projects
          </NavLink>
          {me?.role === "owner" && (
            <>
              <NavLink to="/users">Admin users</NavLink>
              <NavLink to="/audit">Audit log</NavLink>
            </>
          )}
        </nav>

        <div className="sidebar-footer stack" style={{ gap: 6 }}>
          {me && (
            <div className="small" style={{ padding: "0 8px" }}>
              <div style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{me.email}</div>
              <span className={`badge ${me.role === "viewer" ? "" : "badge-accent"}`}>{me.role}</span>
            </div>
          )}
          <Button size="sm" variant="ghost" onClick={onLogout} style={{ justifyContent: "flex-start" }}>
            Sign out
          </Button>
        </div>
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
