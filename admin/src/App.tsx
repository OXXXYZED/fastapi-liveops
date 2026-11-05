import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { getToken, setToken, setUnauthorizedHandler } from "./api/client";
import { Layout } from "./components/Layout";
import { ApiKeysPage } from "./pages/ApiKeys";
import { ConfigPage } from "./pages/Config";
import { FlagsPage } from "./pages/Flags";
import { LeaderboardsPage } from "./pages/Leaderboards";
import { LoginPage } from "./pages/Login";
import { PlayersPage } from "./pages/Players";
import { ProjectPage } from "./pages/Project";
import { ProjectsPage } from "./pages/Projects";

export function App() {
  const [authed, setAuthed] = useState(() => !!getToken());
  const qc = useQueryClient();

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setToken(null);
      qc.clear();
      setAuthed(false);
    });
  }, [qc]);

  if (!authed) return <LoginPage onLogin={() => setAuthed(true)} />;

  const logout = () => {
    setToken(null);
    qc.clear();
    setAuthed(false);
  };

  return (
    <Routes>
      <Route element={<Layout onLogout={logout} />}>
        <Route index element={<ProjectsPage />} />
        <Route path="projects/:projectId" element={<ProjectPage />} />
        <Route path="projects/:projectId/env/:envId">
          <Route index element={<Navigate to="config" replace />} />
          <Route path="config" element={<ConfigPage />} />
          <Route path="flags" element={<FlagsPage />} />
          <Route path="leaderboards" element={<LeaderboardsPage />} />
          <Route path="players" element={<PlayersPage />} />
          <Route path="keys" element={<ApiKeysPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
