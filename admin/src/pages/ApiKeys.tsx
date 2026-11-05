import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap, type Schemas } from "../api/client";
import { useEnvContext } from "../api/hooks";
import {
  Button,
  CopyButton,
  Empty,
  ErrorBox,
  Field,
  Loading,
  Modal,
  confirmAction,
  formatDate,
  useToast,
} from "../components/ui";
import { EnvHead } from "./EnvHead";

export function ApiKeysPage() {
  const { envId } = useEnvContext();
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<Schemas["ApiKeyCreated"] | null>(null);
  const qc = useQueryClient();
  const toast = useToast();
  const path = { environment_id: envId };
  const keys = useQuery({
    queryKey: ["keys", envId],
    queryFn: () => unwrap(api.GET("/admin/environments/{environment_id}/api-keys", { params: { path } })),
  });
  const revoke = useMutation({
    mutationFn: (id: string) => unwrap(api.DELETE("/admin/api-keys/{key_id}", { params: { path: { key_id: id } } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["keys", envId] });
      toast("Key revoked");
    },
    onError: (e) => toast(e.message, true),
  });

  return (
    <>
      <EnvHead
        title="API keys"
        description={
          <>
            <strong>client</strong> keys ship in game builds; <strong>server</strong> keys are secret.
          </>
        }
        actions={<Button variant="primary" onClick={() => setCreating(true)}>New key</Button>}
      />
      <div className="card">
        <ErrorBox error={keys.error} />
        {keys.isLoading ? (
          <Loading />
        ) : !keys.data?.length ? (
          <Empty title="No API keys">Create a client key to connect your game.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Kind</th>
                  <th>Key</th>
                  <th>Created</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {keys.data.map((k) => (
                  <tr key={k.id}>
                    <td>{k.name}</td>
                    <td>
                      <span className={`badge ${k.kind === "server" ? "badge-accent" : ""}`}>{k.kind}</span>
                    </td>
                    <td className="mono">{k.prefix}…</td>
                    <td className="muted">{formatDate(k.created_at)}</td>
                    <td className="num">
                      {k.revoked_at ? (
                        <span className="badge badge-danger">revoked</span>
                      ) : (
                        <Button
                          size="sm"
                          variant="danger"
                          busy={revoke.isPending && revoke.variables === k.id}
                          onClick={() =>
                            confirmAction(`Revoke "${k.name}"? Builds using it will stop working.`) &&
                            revoke.mutate(k.id)
                          }
                        >
                          Revoke
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {creating && (
        <CreateKey
          envId={envId}
          onClose={() => setCreating(false)}
          onCreated={(k) => {
            setCreating(false);
            setCreated(k);
          }}
        />
      )}
      {created && (
        <Modal title="Key created" onClose={() => setCreated(null)} footer={<Button variant="primary" onClick={() => setCreated(null)}>Done</Button>}>
          <div className="alert alert-warn">Copy it now — it won't be shown again.</div>
          <div className="secret">{created.key}</div>
          <div><CopyButton text={created.key} /></div>
        </Modal>
      )}
    </>
  );
}

function CreateKey({
  envId,
  onClose,
  onCreated,
}: {
  envId: string;
  onClose: () => void;
  onCreated: (k: Schemas["ApiKeyCreated"]) => void;
}) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"client" | "server">("client");
  const qc = useQueryClient();
  const m = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/admin/environments/{environment_id}/api-keys", {
          params: { path: { environment_id: envId } },
          body: { name, kind },
        }),
      ),
    onSuccess: (k) => {
      qc.invalidateQueries({ queryKey: ["keys", envId] });
      onCreated(k);
    },
  });
  return (
    <Modal
      title="New API key"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={m.isPending} disabled={!name.trim()} onClick={() => m.mutate()}>
            Create
          </Button>
        </>
      }
    >
      <ErrorBox error={m.error} />
      <Field label="Name" hint="Where it's used, e.g. “Android 1.4 build”">
        <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label="Kind">
        <select value={kind} onChange={(e) => setKind(e.target.value as "client" | "server")}>
          <option value="client">client — shipped in game builds</option>
          <option value="server">server — secret, for game servers</option>
        </select>
      </Field>
    </Modal>
  );
}
