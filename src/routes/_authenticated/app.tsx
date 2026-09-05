import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { supabase } from "@/integrations/supabase/client";

export const Route = createFileRoute("/_authenticated/app")({
  head: () => ({
    meta: [
      { title: "Dashboard — Flight Price Notifier" },
      { name: "description", content: "你的航線追蹤儀表板。Your route-watching dashboard." },
      { property: "og:title", content: "Dashboard — Flight Price Notifier" },
      { property: "og:description", content: "Your route-watching dashboard." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
      { name: "robots", content: "noindex" },
    ],
  }),
  component: AppShell,
});

function AppShell() {
  const { user } = Route.useRouteContext();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  async function handleSignOut() {
    await queryClient.cancelQueries();
    queryClient.clear();
    await supabase.auth.signOut();
    navigate({ to: "/auth", replace: true });
  }

  return (
    <div className="min-h-screen">
      <header className="border-b border-border/60 bg-background/80 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-5 py-4">
          <span className="text-sm font-semibold tracking-tight sm:text-base">
            Flight Price Notifier
          </span>
          <Button variant="secondary" size="sm" onClick={handleSignOut}>
            Sign out / 登出
          </Button>
        </div>
      </header>

      <main className="bg-aurora">
        <div className="mx-auto max-w-3xl px-5 py-24">
          <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
            Hi {user.email}
          </h1>
          <div className="mt-8 rounded-2xl border border-border bg-card p-7">
            <p className="text-base leading-relaxed">
              你的航線追蹤儀表板即將上線 — 下一個里程碑會加上訂閱航線的功能。
            </p>
            <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
              Your dashboard is coming soon. Route-subscription will be added in the next
              milestone.
            </p>
          </div>
        </div>
      </main>
    </div>
  );
}
