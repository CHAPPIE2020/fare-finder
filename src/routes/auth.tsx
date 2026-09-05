import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { supabase } from "@/integrations/supabase/client";

export const Route = createFileRoute("/auth")({
  head: () => ({
    meta: [
      { title: "Sign in / 登入 — Flight Price Notifier" },
      {
        name: "description",
        content: "登入或註冊 Flight Price Notifier，開始追蹤台北出發的機票價格。",
      },
      { property: "og:title", content: "Sign in / 登入 — Flight Price Notifier" },
      {
        property: "og:description",
        content: "Sign in or create an account to start watching flight fares.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: AuthPage,
});

function AuthPage() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      if (data.session) navigate({ to: "/app", replace: true });
    });
  }, [navigate]);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setLoading(true);

    const { error: authError } =
      mode === "signin"
        ? await supabase.auth.signInWithPassword({ email, password })
        : await supabase.auth.signUp({
            email,
            password,
            options: { emailRedirectTo: `${window.location.origin}/app` },
          });

    setLoading(false);

    if (authError) {
      setError(authError.message);
      return;
    }

    const { data } = await supabase.auth.getSession();
    if (data.session) {
      navigate({ to: "/app", replace: true });
    } else {
      setError("請至信箱確認你的帳號後再登入。Please confirm your email, then sign in.");
    }
  }

  return (
    <div className="bg-aurora flex min-h-screen items-center justify-center px-5 py-16">
      <div className="w-full max-w-md">
        <Link to="/" className="text-sm text-muted-foreground hover:text-foreground">
          ← Flight Price Notifier
        </Link>

        <div className="mt-4 rounded-2xl border border-border bg-card p-7">
          <h1 className="text-2xl font-semibold tracking-tight">
            {mode === "signin" ? "Sign in / 登入" : "Sign up / 註冊"}
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">
            {mode === "signin"
              ? "用 email 與密碼登入你的帳號。"
              : "建立帳號，開始追蹤機票價格。"}
          </p>

          <form onSubmit={handleSubmit} className="mt-6 space-y-4">
            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">Password / 密碼</Label>
              <Input
                id="password"
                type="password"
                autoComplete={mode === "signin" ? "current-password" : "new-password"}
                required
                minLength={6}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
              />
            </div>

            {error ? <p className="text-sm text-destructive">{error}</p> : null}

            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? "請稍候…" : mode === "signin" ? "Sign in / 登入" : "Sign up / 註冊"}
            </Button>
          </form>

          <button
            type="button"
            onClick={() => {
              setMode(mode === "signin" ? "signup" : "signin");
              setError(null);
            }}
            className="mt-5 w-full text-sm text-muted-foreground transition-colors hover:text-foreground"
          >
            {mode === "signin"
              ? "還沒有帳號？註冊 / Create an account"
              : "已經有帳號？登入 / Sign in"}
          </button>
        </div>
      </div>
    </div>
  );
}
