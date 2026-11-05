import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useMe } from "../api/auth";
import { api, unwrap, type Schemas } from "../api/client";
import { Button, Empty, ErrorBox, Field, Loading, Modal, PageHead, confirmAction, formatDate, useToast } from "../components/ui";

type Role = Schemas["AdminRole"];
const ROLES: { value: Role; label: string }[] = [
  { value: "viewer", label: "Viewer — read-only" },
  { value: "editor", label: "Editor — manage live ops" },
  { value: "owner", label: "Owner — also users & project deletion" },
];

export function UsersPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const me = useMe();
  const [creating, setCreating] = useState(false);
  const users = useQuery({ queryKey: ["users"], queryFn: () => unwrap(api.GET("/admin/users")) });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Schemas["AdminUserUpdate"] }) =>
      unwrap(api.PATCH("/admin/users/{user_id}", { params: { path: { user_id: id } }, body })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["users"] });
      toast("User updated");
    },
    onError: (e) => toast(e.message, true),
  });
  const del = useMutation({
    mutationFn: (id: string) => unwrap(api.DELETE("/admin/users/{user_id}", { params: { path: { user_id: id } } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["users"] });
      toast("User deleted");
    },
    onError: (e) => toast(e.message, true),
  });

  return (
    <>
      <PageHead title="Admin users" description="Who can access this panel." actions={<Button variant="primary" onClick={() => setCreating(true)}>Invite user</Button>} />
      <div className="card">
        <ErrorBox error={users.error} />
        {users.isLoading ? (
          <Loading />
        ) : users.error ? null : !users.data?.length ? (
          <Empty title="No admin users">You're signed in with the static admin token. Create named accounts so actions are attributed in the audit log.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Email</th><th>Role</th><th>Last login</th><th>Status</th><th /></tr></thead>
              <tbody>
                {users.data.map((u) => (
                  <tr key={u.id}>
                    <td>{u.email} {u.id === me?.id && <span className="badge badge-accent">you</span>}</td>
                    <td>
                      <select
                        value={u.role}
                        aria-label={`Role of ${u.email}`}
                        onChange={(e) => update.mutate({ id: u.id, body: { role: e.target.value as Role } })}
                      >
                        {ROLES.map((r) => <option key={r.value} value={r.value}>{r.value}</option>)}
                      </select>
                    </td>
                    <td className="muted">{u.last_login_at ? formatDate(u.last_login_at) : "never"}</td>
                    <td>
                      <label className="switch">
                        <input
                          type="checkbox"
                          checked={!u.disabled}
                          aria-label={`Active: ${u.email}`}
                          onChange={() => update.mutate({ id: u.id, body: { disabled: !u.disabled } })}
                        />
                        <span className="small">{u.disabled ? "disabled" : "active"}</span>
                      </label>
                    </td>
                    <td className="num">
                      {u.id !== me?.id && (
                        <Button size="sm" variant="danger" onClick={() => confirmAction(`Delete ${u.email}?`) && del.mutate(u.id)}>
                          Delete
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
      {creating && <CreateUser onClose={() => setCreating(false)} />}
    </>
  );
}

function CreateUser({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("editor");
  const m = useMutation({
    mutationFn: () => unwrap(api.POST("/admin/users", { body: { email, password, role } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["users"] });
      onClose();
    },
  });
  return (
    <Modal
      title="Invite user"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={m.isPending} disabled={!email.includes("@") || password.length < 10} onClick={() => m.mutate()}>Create</Button>
        </>
      }
    >
      <ErrorBox error={m.error} />
      <Field label="Email"><input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
      <Field label="Initial password" hint="At least 10 characters. Share it securely; the user can't change it yet — an owner can reset it.">
        <input className="input" type="text" autoComplete="off" value={password} onChange={(e) => setPassword(e.target.value)} />
      </Field>
      <Field label="Role">
        <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
          {ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
        </select>
      </Field>
    </Modal>
  );
}
