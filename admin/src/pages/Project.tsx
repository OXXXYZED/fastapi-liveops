import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { useEnvContext } from "../api/hooks";
import {
  Button,
  ErrorBox,
  Field,
  Loading,
  Modal,
  PageHead,
  confirmAction,
  formatDate,
  useToast,
} from "../components/ui";

export function ProjectPage() {
  const { project, projectId, isLoading } = useEnvContext();
  const [adding, setAdding] = useState(false);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const toast = useToast();
  const del = useMutation({
    mutationFn: () =>
      unwrap(api.DELETE("/admin/projects/{project_id}", { params: { path: { project_id: projectId } } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      toast("Project deleted");
      navigate("/");
    },
  });

  if (isLoading) return <div className="card"><Loading /></div>;
  if (!project) return <div className="alert">Project not found.</div>;

  return (
    <>
      <PageHead
        title={project.name}
        crumbs={<Link to="/">Projects</Link>}
        description={<span className="mono">{project.slug}</span>}
        actions={
          <>
            <Button onClick={() => setAdding(true)}>Add environment</Button>
            <Button
              variant="danger"
              busy={del.isPending}
              onClick={() =>
                confirmAction(`Delete "${project.name}" with all players, configs and scores? This cannot be undone.`) &&
                del.mutate()
              }
            >
              Delete project
            </Button>
          </>
        }
      />
      <ErrorBox error={del.error} />
      <div className="grid">
        {project.environments.map((e) => (
          <Link
            key={e.id}
            to={`/projects/${project.id}/env/${e.id}/config`}
            className="card card-pad"
            style={{ color: "inherit", textDecoration: "none" }}
          >
            <div className="row">
              <h2>{e.name}</h2>
              {e.name === "prod" && <span className="badge badge-danger">production</span>}
            </div>
            <div className="muted small">Created {formatDate(e.created_at)}</div>
            <div className="muted small mono" style={{ marginTop: 8 }}>{e.id}</div>
          </Link>
        ))}
      </div>
      {adding && <AddEnvironment projectId={project.id} onClose={() => setAdding(false)} />}
    </>
  );
}

function AddEnvironment({ projectId, onClose }: { projectId: string; onClose: () => void }) {
  const [name, setName] = useState("");
  const qc = useQueryClient();
  const m = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/admin/projects/{project_id}/environments", {
          params: { path: { project_id: projectId } },
          body: { name },
        }),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      qc.invalidateQueries({ queryKey: ["projects"] });
      onClose();
    },
  });
  return (
    <Modal
      title="Add environment"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            busy={m.isPending}
            disabled={!/^[a-z0-9][a-z0-9-]{0,63}$/.test(name)}
            onClick={() => m.mutate()}
          >
            Add
          </Button>
        </>
      }
    >
      <ErrorBox error={m.error} />
      <Field label="Name" hint="e.g. staging, qa">
        <input className="input mono" value={name} onChange={(e) => setName(e.target.value.toLowerCase())} />
      </Field>
    </Modal>
  );
}
