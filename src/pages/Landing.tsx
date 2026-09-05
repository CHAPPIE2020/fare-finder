import { Link } from "react-router-dom";
import { BellRing, PlaneTakeoff, ShieldCheck } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useReveal } from "@/hooks/useReveal";
import { useDocumentMeta } from "@/hooks/useDocumentMeta";

const features = [
  {
    icon: PlaneTakeoff,
    title: "盯緊熱門航線",
    subtitle: "Always-on route watching",
    body: "持續監控台北出發的熱門航線（東京、首爾），自動抓最低票價。",
  },
  {
    icon: BellRing,
    title: "達標自動通知",
    subtitle: "Target-price email alerts",
    body: "低於你設定的目標價，就寄 email 提醒你，附上立即訂購連結。",
  },
  {
    icon: ShieldCheck,
    title: "隨時取消",
    subtitle: "Cancel anytime",
    body: "月訂閱制，不想用隨時停，沒有綁約。",
  },
];

function FeatureCard({ index, feature }: { index: number; feature: (typeof features)[number] }) {
  const ref = useReveal<HTMLDivElement>(index * 120);
  const Icon = feature.icon;

  return (
    <div
      ref={ref}
      className="reveal rounded-2xl border border-border bg-card p-6 transition-colors hover:border-primary/50"
    >
      <div className="mb-5 inline-flex size-11 items-center justify-center rounded-xl bg-accent text-primary">
        <Icon className="size-5" />
      </div>
      <h3 className="text-lg font-semibold">{feature.title}</h3>
      <p className="mt-1 text-sm font-medium text-primary">{feature.subtitle}</p>
      <p className="mt-3 text-sm leading-relaxed text-muted-foreground">{feature.body}</p>
    </div>
  );
}

export function Landing() {
  useDocumentMeta(
    "Flight Price Notifier — 機票降價通知",
    "設定台北出發的航線與目標價，機票降價就寄 email 通知你。Set a route and a target price — we email you when the fare drops.",
  );

  const heroRef = useReveal<HTMLDivElement>(60);

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-border/60 bg-background/80 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-5 py-4">
          <span className="text-sm font-semibold tracking-tight sm:text-base">
            Flight Price Notifier
          </span>
          <Button asChild size="sm">
            <Link to="/sign-in">Sign in / 登入</Link>
          </Button>
        </div>
      </header>

      <main>
        <section className="bg-aurora">
          <div ref={heroRef} className="reveal mx-auto max-w-3xl px-5 py-24 text-center sm:py-32">
            <span className="inline-flex rounded-full border border-border bg-card px-3 py-1 text-xs text-muted-foreground">
              台北出發 · 熱門航線監控
            </span>
            <h1 className="text-gradient-brand mt-6 text-4xl font-bold tracking-tight sm:text-6xl">
              Flight Price Notifier
            </h1>
            <p className="mt-6 text-xl font-medium sm:text-2xl">
              設定航線與目標價，機票降價就通知你
            </p>
            <p className="mt-3 text-base text-muted-foreground">
              Set a route and a target price — we email you when the fare drops.
            </p>
            <div className="mt-9 flex justify-center">
              <Button asChild size="lg">
                <Link to="/sign-in">Sign in / 登入</Link>
              </Button>
            </div>
          </div>
        </section>

        <section className="mx-auto max-w-6xl px-5 pb-24">
          <div className="grid gap-6 md:grid-cols-3">
            {features.map((feature, index) => (
              <FeatureCard key={feature.title} feature={feature} index={index} />
            ))}
          </div>
        </section>
      </main>

      <footer className="border-t border-border/60">
        <div className="mx-auto max-w-6xl px-5 py-8 text-sm text-muted-foreground">
          © 2026 Flight Price Notifier
        </div>
      </footer>
    </div>
  );
}
