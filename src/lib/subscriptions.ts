import { supabase } from "@/integrations/supabase/client";

/**
 * Shared client for the paid-subscription API (M2 ECPay paywall).
 * Used by the flight plans (PlanSubscriptions) and by Viral Radar (RadarCard).
 */
export const FLIGHT_API_URL: string | undefined = import.meta.env["VITE_FLIGHT_API_URL"];

/** Display only — the amount actually charged comes from the `flight/ecpay` secret on the server. */
export const MONTHLY_FEE_TWD = 300;

/** The API identifies the user from this Supabase access token, not from a client-sent email. */
async function authHeaders(): Promise<Record<string, string>> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (!token) throw new Error("登入狀態已失效，請重新登入");
  return { Authorization: `Bearer ${token}` };
}

export type FlightPlanName = "tokyo" | "seoul";
export type SubscriptionPlanName = FlightPlanName | "radar";

export type SubscriptionStatus = "pending_payment" | "active" | "cancelled" | "expired";

export type Subscription = {
  route: string;
  plan_name: SubscriptionPlanName;
  /** Flight plans only. */
  target_price?: number;
  /** Viral Radar only. */
  keywords?: string[];
  /** Viral Radar only: alert when views >= channel average x min_ratio. */
  min_ratio?: number;
  /** Viral Radar only: subscriber switched alert emails off (billing unchanged). */
  notifications_paused?: boolean;
  currency: string;
  /** Missing on legacy M1 rows (created before the paywall) — treated as unpaid. */
  subscription_status?: SubscriptionStatus;
  /** Fixed-width UTC, e.g. 2026-10-25T08:00:00Z (same format the server compares). */
  current_period_end?: string;
  current_period_end_date?: string;
};

/** What a card should show, derived from the server row. */
export type CardState = "none" | "unpaid" | "active" | "grace" | "expired";

function nowUtc(): string {
  return new Date().toISOString().slice(0, 19) + "Z";
}

export function cardState(sub: Subscription | undefined): CardState {
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

async function errorMessage(res: Response, fallback: string): Promise<string> {
  try {
    const data = (await res.json()) as { error?: string };
    return data.error ?? fallback;
  } catch {
    return fallback;
  }
}

export async function fetchSubscriptions(): Promise<Subscription[]> {
  const res = await fetch(`${FLIGHT_API_URL}/subscriptions`, { headers: await authHeaders() });
  if (!res.ok) throw new Error("讀取訂閱狀態失敗");
  const data = (await res.json()) as { subscriptions: Subscription[] };
  return data.subscriptions;
}

export type SavePayload =
  | { plan_name: FlightPlanName; target_price: number }
  | { plan_name: "radar"; keywords: string[]; min_ratio: number };

export type SaveResult = { kind: "checkout" } | { kind: "updated"; subscription: Subscription };

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
 *  - application/json -> already paid (or cancelled but still in the paid period): settings updated in place
 */
export async function saveSubscription(payload: SavePayload): Promise<SaveResult> {
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

/** Viral Radar "暫停通知" switch — stops/resumes alert emails without touching billing. */
export async function setRadarPaused(paused: boolean): Promise<Subscription> {
  const res = await fetch(`${FLIGHT_API_URL}/subscribe`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify({ plan_name: "radar", notifications_paused: paused }),
  });
  if (!res.ok) throw new Error(await errorMessage(res, "更新通知設定失敗"));
  const data = (await res.json()) as { subscription: Subscription };
  return data.subscription;
}

export async function cancelSubscription(route: string): Promise<Subscription> {
  const res = await fetch(`${FLIGHT_API_URL}/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify({ route }),
  });
  if (!res.ok) throw new Error(await errorMessage(res, "取消訂閱失敗"));
  const data = (await res.json()) as { subscription: Subscription };
  return data.subscription;
}
