import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api, unwrap, type Environment, type Project } from "./client";

export function useProjects() {
  return useQuery({
    queryKey: ["projects"],
    queryFn: () => unwrap(api.GET("/admin/projects")),
  });
}

/** Current project + environment from the route. */
export function useEnvContext(): {
  projectId: string;
  envId: string;
  project?: Project;
  env?: Environment;
  isLoading: boolean;
} {
  const { projectId = "", envId = "" } = useParams();
  const q = useQuery({
    queryKey: ["project", projectId],
    queryFn: () =>
      unwrap(api.GET("/admin/projects/{project_id}", { params: { path: { project_id: projectId } } })),
    enabled: !!projectId,
  });
  return {
    projectId,
    envId,
    project: q.data,
    env: q.data?.environments.find((e) => e.id === envId),
    isLoading: q.isLoading,
  };
}
