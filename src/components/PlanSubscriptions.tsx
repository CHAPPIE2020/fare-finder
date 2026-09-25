import { useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
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

/** Display only — the amount actually charged comes from the `flight/ecpay` secret on the server. */
const MONTHLY_FEE_TWD = 300;

/** The API identifies the user from this Supabase access token, not from a client-sent email. */
async function authHeaders(): Promise<Record<string, string>> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (!token) throw new Error("登入狀態已失效，請重新登入");
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

type SubscriptionStatus = "pending_payment" | "active" | "cancelled" | "expired";

type Subscription = {
  route: string;
  plan_name: PlanName;
  target_price: number;
  currency: string;
  /** Missing on legacy M1 rows (created before the paywall) — treated as unpaid. */
  subscription_status?: SubscriptionStatus;
  /** Fixed-width UTC, e.g. 2026-10-25T08:00:00Z (same format the server compares). */
  current_period_end?: string;
  current_period_end_date?: string;
};

/** What the card should show, derived from the server row. */
type CardState = "none" | "unpaid" | "active" | "grace" | "expired";

function nowUtc(): string {
  return new Date().toISOString().slice(0, 19) + "Z";
}

function cardState(sub: Subscription | undefined): CardState {
  if (!sub) return "none";
  switch (sub.subscription_status) {
    case "active":
      return "active";
    case "cancelled":
      return sub.current_period_end && sub.current_period_end >= nowUtc() ? "grace" : "expired";
    case "expired":
      return "expired";
    default:
      return "unpaid"; // pending_payment, or a legacy M1 row with no status
  }
}

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

async function errorMessage(res: Response, fallback: string): Promise<string> {
  try {
    const data = (await res.json()) as { error?: string };
    return data.error ?? fallback;
  } catch {
    return fallback;
  }
}

async function fetchSubscriptions(): Promise<Subscription[]> {
  const res = await fetch(`${FLIGHT_API_URL}/subscriptions`, { headers: await authHeaders() });
  if (!res.ok) throw new Error("讀取訂閱狀態失敗");
  const data = (await res.json()) as { subscriptions: Subscription[] };
  return data.subscriptions;
}

type SaveResult = { kind: "checkout" } | { kind: "updated"; subscription: Subscription };

/** Hands the browser to ECPay: submits the server-built (CheckMacValue-signed) form. */
function submitCheckoutForm(html: string) {
  const parsed = new DOMParser().parseFromString(html, "text/html");
  const form = parsed.querySelector("form");
  if (!form) throw new Error("付款頁面產生失敗，請稍後再試");
  const imported = document.importNode(form, true);
  imported.style.display = "none";
  document.body.appendChild(imported);
  imported.submit();
}

/**
 * POST /subscribe answers in one of two ways:
 *  - text/html        -> needs payment: an auto-submit form for ECPay's cashier
 *  - application/json -> already paid (or cancelled but still in the paid period): target updated in place
 */
async function saveSubscription(payload: {
  plan_name: PlanName;
  target_price: number;
}): Promise<SaveResult> {
  const res = await fetch(`${FLIGHT_API_URL}/subscribe`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await errorMessage(res, "儲存訂閱失敗"));
  const contentType = res.headers.get("Content-Type") ?? "";
  if (contentType.includes("text/html")) {
    submitCheckoutForm(await res.text());
    return { kind: "checkout" };
  }
  const data = (await res.json()) as { subscription: Subscription };
  return { kind: "updated", subscription: data.subscription };
}

async function cancelSubscription(route: string): Promise<Subscription> {
  const res = await fetch(`${FLIGHT_API_URL}/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify({ route }),
  });
  if (!res.ok) throw new Error(await errorMessage(res, "取消訂閱失敗"));
  const data = (await res.json()) as { subscription: Subscription };
  return data.subscription;
}

const POLL_AFTER_PAYMENT_MS = 60_000;

/** Two fixed route plans behind the M2 paywall (ECPay 定期定額, monthly). */
export function PlanSubscriptions({ email }: { email: string }) {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const purchase = searchParams.get("purchase");
  // After ECPay sends the user back, the ReturnURL callback may land a few seconds later — poll briefly.
  const [pollUntil] = useState(() =>
    purchase === "success" ? Date.now() + POLL_AFTER_PAYMENT_MS : 0,
  );

  const { data: subscriptions, isLoading } = useQuery({
    queryKey: ["subscriptions", email],
    queryFn: fetchSubscriptions,
    enabled: Boolean(FLIGHT_API_URL && email),
    refetchInterval: (query) => {
      const anyUnpaid = (query.state.data ?? []).some((s) => cardState(s) === "unpaid");
      return anyUnpaid && Date.now() < pollUntil ? 3000 : false;
    },
  });

  const { data: prices } = useQuery({
    queryKey: ["prices"],
    queryFn: fetchPrices,
    enabled: Boolean(FLIGHT_API_URL),
    staleTime: 5 * 60 * 1000,
  });

  const saveMutation = useMutation({
    mutationFn: saveSubscription,
    onSuccess: (result) => {
      if (result.kind === "updated") {
        void queryClient.invalidateQueries({ queryKey: ["subscriptions", email] });
      }
    },
  });

  const cancelMutation = useMutation({
    mutationFn: cancelSubscription,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["subscriptions", email] });
    },
  });

  if (!FLIGHT_API_URL) {
    return (
      <p className="text-sm text-muted-foreground">
        訂閱功能尚未設定完成(缺少 API 位址，請設定 VITE_FLIGHT_API_URL)。
      </p>
    );
  }

  const anyActive = (subscriptions ?? []).some((s) => cardState(s) === "active");

  function dismissBanner() {
    const next = new URLSearchParams(searchParams);
    next.delete("purchase");
    setSearchParams(next, { replace: true });
  }

  return (
    <div className="space-y-5">
      {purchase === "success" && (
        <Alert>
          <AlertTitle>{anyActive ? "訂閱已生效 ✅" : "付款完成，正在確認訂閱狀態…"}</AlertTitle>
          <AlertDescription className="flex flex-wrap items-center justify-between gap-2">
            <span>
              {anyActive
                ? "歡迎信已寄出，降價時我們會寄信通知你。"
                : "綠界通知通常幾秒內就會送達；如果一直沒更新，請稍後重新整理頁面。"}
            </span>
            <Button variant="ghost" size="sm" onClick={dismissBanner}>
              關閉
            </Button>
          </AlertDescription>
        </Alert>
      )}
      {purchase === "failed" && (
        <Alert variant="destructive">
          <AlertTitle>付款沒有完成</AlertTitle>
          <AlertDescription className="flex flex-wrap items-center justify-between gap-2">
            <span>這次沒有扣款成功，可以按「完成付款」再試一次。</span>
            <Button variant="ghost" size="sm" onClick={dismissBanner}>
              關閉
            </Button>
          </AlertDescription>
        </Alert>
      )}

      <div className="grid gap-5 sm:grid-cols-2">
        {PLANS.map((plan) => {
          const existing = subscriptions?.find((s) => s.plan_name === plan.name);
          const isThisPlan = saveMutation.variables?.plan_name === plan.name;
          return (
            <PlanCard
              key={plan.name}
              plan={plan}
              existing={existing}
              latest={prices?.find((p) => p.plan_name === plan.name)}
              isLoading={isLoading}
              isSaving={saveMutation.isPending && isThisPlan}
              isRedirecting={saveMutation.data?.kind === "checkout" && isThisPlan}
              isCancelling={
                cancelMutation.isPending && cancelMutation.variables === existing?.route
              }
              error={
                (isThisPlan ? saveMutation.error?.message : undefined) ??
                (cancelMutation.variables === existing?.route
                  ? cancelMutation.error?.message
                  : undefined)
              }
              onSubmit={(targetPrice) =>
                saveMutation.mutate({ plan_name: plan.name, target_price: targetPrice })
              }
              onCancel={() => existing && cancelMutation.mutate(existing.route)}
            />
          );
        })}
      </div>
    </div>
  );
}

function PlanCard({
  plan,
  existing,
  latest,
  isLoading,
  isSaving,
  isRedirecting,
  isCancelling,
  error,
  onSubmit,
  onCancel,
}: {
  plan: Plan;
  existing: Subscription | undefined;
  latest: LatestPrice | undefined;
  isLoading: boolean;
  isSaving: boolean;
  isRedirecting: boolean;
  isCancelling: boolean;
  error: string | undefined;
  onSubmit: (targetPrice: number) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState("");
  const [hasEdited, setHasEdited] = useState(false);

  const state = cardState(existing);
  const endDate = existing?.current_period_end_date ?? "";
  const displayValue = hasEdited ? draft : existing ? String(existing.target_price) : draft;
  const busy = isLoading || isSaving || isRedirecting || isCancelling;

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const parsed = Number(displayValue);
    if (!Number.isFinite(parsed) || parsed <= 0) return;
    onSubmit(parsed);
    setHasEdited(false);
  }

  function handleCancel() {
    const ok = window.confirm(
      `確定要取消「${plan.title}」的訂閱嗎？\n之後不會再自動扣款，降價通知會持續到 ${endDate}。`,
    );
    if (ok) onCancel();
  }

  const target = existing ? `目前目標價 NT$${existing.target_price.toLocaleString()}` : "";
  const description: Record<CardState, string> = {
    none: `月費 NT$${MONTHLY_FEE_TWD}，設定 TWD 目標價，達標就寄信通知你`,
    unpaid: `${target} · 尚未完成付款，付款後才會開始通知`,
    active: `${target} · 下次續訂 ${endDate}`,
    grace: `${target} · 已取消續訂，通知持續到 ${endDate}`,
    expired: "訂閱已結束，重新訂閱後恢復通知",
  };
  const submitLabel: Record<CardState, string> = {
    none: `訂閱並付款（NT$${MONTHLY_FEE_TWD}/月）`,
    unpaid: "完成付款",
    active: "更新目標價",
    grace: "更新目標價",
    expired: "重新訂閱並付款",
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle>{plan.title}</CardTitle>
          {state === "active" && <Badge>已訂閱（有效）</Badge>}
          {state === "grace" && <Badge variant="secondary">已取消 · 有效至 {endDate}</Badge>}
          {state === "unpaid" && <Badge variant="destructive">未完成付款</Badge>}
          {state === "expired" && <Badge variant="outline">已結束</Badge>}
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
        <CardDescription>{description[state]}</CardDescription>
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
          {error && <p className="text-sm text-destructive">{error}</p>}
        </CardContent>
        <CardFooter className="flex flex-wrap gap-2">
          <Button type="submit" disabled={busy || !displayValue}>
            {isRedirecting ? "前往付款頁…" : isSaving ? "處理中…" : submitLabel[state]}
          </Button>
          {state === "active" && (
            <Button type="button" variant="outline" disabled={busy} onClick={handleCancel}>
              {isCancelling ? "取消中…" : "取消訂閱"}
            </Button>
          )}
        </CardFooter>
      </form>
    </Card>
  );
}
