import { QueryClient, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { Outlet } from "react-router-dom";

import { supabase } from "@/integrations/supabase/client";

/**
 * App shell mounted above every route. Keeps the shared React Query cache in
 * sync with Supabase auth state, mirroring the previous TanStack Start root
 * route's `router.invalidate()` + `queryClient.invalidateQueries()` behavior.
 */
export function RootLayout() {
  const queryClient: QueryClient = useQueryClient();

  useEffect(() => {
    const { data } = supabase.auth.onAuthStateChange((event) => {
      if (event !== "SIGNED_IN" && event !== "SIGNED_OUT" && event !== "USER_UPDATED") return;
      if (event !== "SIGNED_OUT") queryClient.invalidateQueries();
    });
    return () => data.subscription.unsubscribe();
  }, [queryClient]);

  return <Outlet />;
}
