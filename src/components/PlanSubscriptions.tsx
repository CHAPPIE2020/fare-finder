import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { supabase } from "@/integrations/supabase/client";

const FLIGHT_API_URL = import.meta.env["VITE_FLIGHT_API_URL"];

/** The API identifies the user from this Supabase access token, not from a client-sent email. */
async function authHeaders(): Promise<Record<string, string>> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (!token) throw new Error("登入狀態已失效,請重新登入");
  return { Authorization: `Bearer ${token}` };
}

type PlanName = "tokyo" | "seoul";

type Plan = {
  name: PlanName;
  title: string;
};

const PLANS: Plan[] = [
  { name: "tokyo", title: "台北 ✈ 東京" },
  { name: "seoul", title: "台北 ✈ 首爾" },
];

type Subscription = {
  route: string;
  plan_name: PlanName;
  target_price: number;
  currency: string;
};

type LatestPrice = {
  route: string;
  plan_name: PlanName;
  month: string;
  price: number;
  checked_at: string;
};

/** Latest cheapest fare per route, written by the parser every 30 minutes (public, no login). */
async function fetchPrices(): Promise<LatestPrice[]> {
  const res = await fetch(`${FLIGHT_API_URL}/prices`);
  if (!res.ok) throw new Error("讀取票價失敗");
  const data = (await res.json()) as { prices: LatestPrice[] };
  return data.prices;
}

function timeAgo(iso: string): string {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (minutes < 1) return "剛剛";
  if (minutes < 60) return `${minutes} 分鐘前`;
  return `${Math.round(minutes / 60)} 小時前`;
}

async function fetchSubscriptions(): Promise<Subscription[]> {
  const res = await fetch(`${FLIGHT_API_URL}/subscriptions`, { headers: await authHeaders() });
  if (!res.ok) throw new Error("讀取訂閱狀態失敗");
  const data = (await res.json()) as { subscriptions: Subscription[] };
  return data.subscriptions;
}

async function saveSubscription(payload: { plan_name: PlanName; target_price: number }) {
  const res = await fetch(`${FLIGHT_API_URL}/subscribe`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error("儲存訂閱失敗");
  return (await res.json()) as Subscription;
}

/** Two fixed route plans (Part 1.1 — no payment guard, no subscription_status yet). */
export function PlanSubscriptions({ email }: { email: string }) {
  const queryClient = useQueryClient();

  const { data: subscriptions, isLoading } = useQuery({
    queryKey: ["subscriptions", email],
    queryFn: fetchSubscriptions,
    enabled: Boolean(FLIGHT_API_URL && email),
  });

  const { data: prices } = useQuery({
    queryKey: ["prices"],
    queryFn: fetchPrices,
    enabled: Boolean(FLIGHT_API_URL),
    staleTime: 5 * 60 * 1000,
  });

  const mutation = useMutation({
    mutationFn: saveSubscription,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["subscriptions", email] });
    },
  });

  if (!FLIGHT_API_URL) {
    return (
      <p className="text-sm text-muted-foreground">
        訂閱功能尚未設定完成(缺少 API 位址,請設定 VITE_FLIGHT_API_URL)。
      </p>
    );
  }

  return (
    <div className="grid gap-5 sm:grid-cols-2">
      {PLANS.map((plan) => {
        const existing = subscriptions?.find((s) => s.plan_name === plan.name);
        return (
          <PlanCard
            key={plan.name}
            plan={plan}
            existing={existing}
            latest={prices?.find((p) => p.plan_name === plan.name)}
            isLoading={isLoading}
            isSaving={mutation.isPending && mutation.variables?.plan_name === plan.name}
            onSubmit={(targetPrice) =>
              mutation.mutate({ plan_name: plan.name, target_price: targetPrice })
            }
          />
        );
      })}
    </div>
  );
}

function PlanCard({
  plan,
  existing,
  latest,
  isLoading,
  isSaving,
  onSubmit,
}: {
  plan: Plan;
  existing: Subscription | undefined;
  latest: LatestPrice | undefined;
  isLoading: boolean;
  isSaving: boolean;
  onSubmit: (targetPrice: number) => void;
}) {
  const [draft, setDraft] = useState("");
  const [hasEdited, setHasEdited] = useState(false);

  const displayValue = hasEdited ? draft : existing ? String(existing.target_price) : draft;

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const parsed = Number(displayValue);
    if (!Number.isFinite(parsed) || parsed <= 0) return;
    onSubmit(parsed);
    setHasEdited(false);
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle>{plan.title}</CardTitle>
          {existing && <Badge variant="secondary">已訂閱</Badge>}
        </div>
        {latest && (
          <p className="text-sm text-muted-foreground">
            參考最低價{" "}
            <span className="font-semibold text-foreground">
              NT${latest.price.toLocaleString()}
            </span>{" "}
            起 · {Number(latest.month.slice(5))} 月出發 · {timeAgo(latest.checked_at)}更新
          </p>
        )}
        <CardDescription>
          {existing
            ? `目前目標價 NT$${existing.target_price.toLocaleString()}`
            : "設定 TWD 目標價,達標就寄信通知你"}
        </CardDescription>
      </CardHeader>
      <form onSubmit={handleSubmit}>
        <CardContent className="space-y-2">
          <Label htmlFor={`target-${plan.name}`}>目標價(TWD)</Label>
          <Input
            id={`target-${plan.name}`}
            type="number"
            min={1}
            step={1}
            inputMode="numeric"
            placeholder="例如 10000"
            value={displayValue}
            disabled={isLoading}
            onChange={(event) => {
              setHasEdited(true);
              setDraft(event.target.value);
            }}
          />
        </CardContent>
        <CardFooter>
          <Button type="submit" disabled={isLoading || isSaving || !displayValue}>
            {isSaving ? "儲存中…" : existing ? "更新目標價" : "開始追蹤"}
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}
