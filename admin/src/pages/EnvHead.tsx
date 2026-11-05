import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useEnvContext } from "../api/hooks";
import { PageHead } from "../components/ui";

export function EnvHead(props: { title: string; description?: ReactNode; actions?: ReactNode }) {
  const { project, env } = useEnvContext();
  return (
    <>
      <PageHead
        {...props}
        crumbs={
          <>
            <Link to="/">Projects</Link> / {project ? <Link to={`/projects/${project.id}`}>{project.name}</Link> : "…"} /{" "}
            <strong>{env?.name ?? "…"}</strong>
          </>
        }
      />
      {env?.name === "prod" && (
        <div className="alert alert-warn" style={{ marginBottom: 16 }}>
          You are editing <strong>production</strong>. Changes reach players immediately.
        </div>
      )}
    </>
  );
}
