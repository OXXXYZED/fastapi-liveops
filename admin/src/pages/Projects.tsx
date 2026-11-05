import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { useProjects } from "../api/hooks";
import { Button, Empty, ErrorBox, Field, Loading, Modal, PageHead, formatDate } from "../components/ui";

const slugify = (s: string) =>
  s.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 64);

export function ProjectsPage() {
  const projects = useProjects();
  const [creating, setCreating] = useState(false);

  return (
    <>
      <PageHead
        title="Projects"
        description="Each project is a game with its own environments."
        actions={
          <Button write variant="primary" onClick={() => setCreating(true)}>
            New project
          </Button>
        }
      />
      <ErrorBox error={projects.error} />
      {projects.isLoading ? (
        <div className="card">
          <Loading />
        </div>
      ) : projects.data?.length === 0 ? (
        <div className="card">
          <Empty title="No projects yet">
            <p>Create your first project to get API keys for your game.</p>
            <Button write variant="primary" onClick={() => setCreating(true)}>
              New project
            </Button>
          </Empty>
        </div>
      ) : (
        <div className="grid">
          {projects.data?.map((p) => (
            <Link key={p.id} to={`/projects/${p.id}`} className="card card-pad" style={{ color: "inherit", textDecoration: "none" }}>
              <h2>{p.name}</h2>
              <div className="muted mono">{p.slug}</div>
              <div className="row" style={{ marginTop: 12 }}>
                {p.environments.map((e) => (
                  <span key={e.id} className="badge">
                    {e.name}
                  </span>
                ))}
                <span className="spacer" />
                <span className="muted small">{formatDate(p.created_at)}</span>
              </div>
            </Link>
          ))}
        </div>
      )}
      {creating && <CreateProject onClose={() => setCreating(false)} />}
    </>
  );
}

function CreateProject({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const m = useMutation({
    mutationFn: () => unwrap(api.POST("/admin/projects", { body: { name, slug } })),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      navigate(`/projects/${p.id}`);
    },
  });
  const valid = name.trim() && /^[a-z0-9][a-z0-9-]{0,63}$/.test(slug);

  return (
    <Modal
      title="New project"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={m.isPending} disabled={!valid} onClick={() => m.mutate()}>
            Create
          </Button>
        </>
      }
    >
      <ErrorBox error={m.error} />
      <Field label="Name">
        <input
          className="input"
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            if (!slugTouched) setSlug(slugify(e.target.value));
          }}
          placeholder="My Game"
        />
      </Field>
      <Field label="Slug" hint="Lowercase letters, digits and dashes. Can't be changed later.">
        <input
          className="input mono"
          value={slug}
          onChange={(e) => {
            setSlugTouched(true);
            setSlug(e.target.value);
          }}
        />
      </Field>
      <p className="muted small" style={{ margin: 0 }}>
        Environments <code>dev</code> and <code>prod</code> are created automatically.
      </p>
    </Modal>
  );
}
