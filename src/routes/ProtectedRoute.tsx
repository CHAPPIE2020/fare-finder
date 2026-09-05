import { useEffect, useState } from "react";
import { Navigate, Outlet } from "react-router-dom";
import type { User } from "@supabase/supabase-js";

import { supabase } from "@/integrations/supabase/client";

type AuthStatus =
  { state: "loading" } | { state: "authenticated"; user: User } | { state: "unauthenticated" };

export type AuthenticatedOutletContext = { user: User };

/** Client-side guard for authenticated routes (replaces the old `_authenticated` beforeLoad). */
export function ProtectedRoute() {
  const [status, setStatus] = useState<AuthStatus>({ state: "loading" });

  useEffect(() => {
    let active = true;

    supabase.auth.getUser().then(({ data, error }) => {
      if (!active) return;
      setStatus(
        error || !data.user
          ? { state: "unauthenticated" }
          : { state: "authenticated", user: data.user },
      );
    });

    const { data: subscription } = supabase.auth.onAuthStateChange((event, session) => {
      if (!active) return;
      if (event === "SIGNED_OUT") {
        setStatus({ state: "unauthenticated" });
      } else if (session?.user) {
        setStatus({ state: "authenticated", user: session.user });
      }
    });

    return () => {
      active = false;
      subscription.subscription.unsubscribe();
    };
  }, []);

  if (status.state === "loading") return null;
  if (status.state === "unauthenticated") return <Navigate to="/sign-in" replace />;

  return <Outlet context={{ user: status.user } satisfies AuthenticatedOutletContext} />;
}
