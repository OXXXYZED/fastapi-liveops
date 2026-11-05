import createClient, { type Middleware } from "openapi-fetch";
import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type Project = Schemas["ProjectOut"];
export type Environment = Schemas["EnvironmentOut"];
export type ApiKey = Schemas["ApiKeyOut"];
export type Flag = Schemas["FlagOut"];
export type Condition = Schemas["Condition"];
export type Leaderboard = Schemas["LeaderboardOut"];

const TOKEN_KEY = "liveops.admin_token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: token lives for this page only */
  }
  memoryToken = token;
}

let memoryToken: string | null = getToken();
let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public detail?: unknown,
  ) {
    super(message);
  }
}

const auth: Middleware = {
  onRequest({ request }) {
    if (memoryToken) request.headers.set("Authorization", `Bearer ${memoryToken}`);
    return request;
  },
  onResponse({ response }) {
    if (response.status === 401) onUnauthorized();
    return response;
  },
};

export const api = createClient<paths>({ baseUrl: "" });
api.use(auth);

function messageOf(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail
      .map((d) => {
        const e = d as { loc?: unknown[]; msg?: string };
        return `${(e.loc ?? []).slice(1).join(".")}: ${e.msg ?? "invalid"}`;
      })
      .join("; ");
  if (detail && typeof detail === "object" && "message" in detail)
    return String((detail as { message: unknown }).message);
  return "Request failed";
}

/** Unwraps an openapi-fetch result, throwing ApiError on failure. */
export async function unwrap<T>(
  p: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await p;
  if (!response.ok) {
    const detail = (error as { detail?: unknown } | undefined)?.detail ?? error;
    throw new ApiError(response.status, messageOf(detail), detail);
  }
  return data as T;
}
