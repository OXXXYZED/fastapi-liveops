import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, type ReactNode } from "react";
import { api, unwrap, type Schemas } from "./client";

type Me = Schemas["AdminMe"];
const MeCtx = createContext<Me | undefined>(undefined);

export function MeProvider({ children }: { children: ReactNode }) {
  const me = useQuery({ queryKey: ["me"], queryFn: () => unwrap(api.GET("/admin/auth/me")), staleTime: 60_000 });
  return <MeCtx.Provider value={me.data}>{children}</MeCtx.Provider>;
}

export const useMe = () => useContext(MeCtx);
export const useCanEdit = () => {
  const me = useMe();
  return !!me && me.role !== "viewer";
};
export const useIsOwner = () => useMe()?.role === "owner";
