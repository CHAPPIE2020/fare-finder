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
import {
  FLIGHT_API_URL,
  MONTHLY_FEE_TWD,
  cancelSubscription,
  cardState,
  fetchSubscriptions,
  saveSubscription,
  setRadarPaused,
  type CardState,
} from "@/lib/subscriptions";

const MAX_KEYWORDS = 3;
const RATIO_OPTIONS = [2, 3, 5, 10];

/** "貓咪、寵物, 收納" -> ["貓咪", "寵物", "收納"] (deduped, case-insensitive). */
function parseKeywords(raw: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const part of raw.split(/[,，、;；\n]/)) {
    const k = part.trim().replace(/\s+/g, " ");
    if (k && !seen.has(k.toLowerCase())) {
      seen.add(k.toLowerCase());
      out.push(k);
    }
  }
  return out;
}

/**
 * Viral Radar (小眾爆發雷達): paid YouTube alerts for fresh videos that outperform their
 * channel's normal views by N times, in the keywords the user cares about.
 * Uses the same ECPay paywall as the flight plans (plan_name "radar", route "RADAR").
 */
export function RadarCard({ email }: { email: string }) {
  const queryClient = useQueryClient();
  const { data: subscriptions, isLoading } = useQuery({
    queryKey: ["subscriptions", email],
    queryFn: fetchSubscriptions,
    enabled: Boolean(FLIGHT_API_URL && email),
  });
  const existing = subscriptions?.find((s) => s.plan_name === "radar");
  const state = cardState(existing);

  const [keywordsDraft, setKeywordsDraft] = useState<string | null>(null);
  const [ratioDraft, setRatioDraft] = useState<number | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const keywordsValue = keywordsDraft ?? (existing?.keywords ?? []).join("、");
  const ratioValue = ratioDraft ?? existing?.min_ratio ?? 3;

  const saveMutation = useMutation({
    mutationFn: saveSubscription,
    onSuccess: (result) => {
      if (result.kind === "updated") {
        setKeywordsDraft(null);
        setRatioDraft(null);
        void queryClient.invalidateQueries({ queryKey: ["subscriptions", email] });
      }
    },
  });
  const pauseMutation = useMutation({
    mutationFn: setRadarPaused,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["subscriptions", email] });
    },
  });
  const cancelMutation = useMutation({
    mutationFn: cancelSubscription,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["subscriptions", email] });
    },
  });

  if (!FLIGHT_API_URL) return null;

  const isRedirecting = saveMutation.data?.kind === "checkout";
  const busy =
    isLoading ||
    saveMutation.isPending ||
    isRedirecting ||
    cancelMutation.isPending ||
    pauseMutation.isPending;
  const paid = state === "active" || state === "grace";
  const paused = paid && Boolean(existing?.notifications_paused);
  const endDate = existing?.current_period_end_date ?? "";
  const tracked = (existing?.keywords ?? []).join("、");

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const keywords = parseKeywords(keywordsValue);
    if (keywords.length === 0) {
      setFormError("請至少輸入 1 個關鍵字");
      return;
    }
    if (keywords.length > MAX_KEYWORDS) {
      setFormError(`關鍵字最多 ${MAX_KEYWORDS} 個`);
      return;
    }
    setFormError(null);
    saveMutation.mutate({ plan_name: "radar", keywords, min_ratio: ratioValue });
  }

  function handleCancel() {
    const ok = window.confirm(
      `確定要取消「爆發雷達」嗎？\n之後不會再自動扣款，爆發通知會持續到 ${endDate}。`,
    );
    if (ok && existing) cancelMutation.mutate(existing.route);
  }

  const description: Record<CardState, string> = {
    none: `月費 NT$${MONTHLY_FEE_TWD}。每小時掃描 YouTube 台灣區新影片，你關注的領域一有影片表現超過頻道平均 N 倍，就寄信通知你，附上爆紅原因與改編點子。`,
    unpaid: "尚未完成付款，付款後才會開始偵測。",
    active: `追蹤中：${tracked} · 門檻 ${existing?.min_ratio ?? ""} 倍 · 下次續訂 ${endDate}`,
    grace: `已取消續訂，爆發通知持續到 ${endDate}`,
    expired: "訂閱已結束，重新訂閱後恢復偵測。",
  };
  const submitLabel: Record<CardState, string> = {
    none: `訂閱並付款（NT$${MONTHLY_FEE_TWD}/月）`,
    unpaid: "完成付款",
    active: "更新設定",
    grace: "更新設定",
    expired: "重新訂閱並付款",
  };
  const error =
    formError ??
    saveMutation.error?.message ??
    cancelMutation.error?.message ??
    pauseMutation.error?.message;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle>🔥 小眾爆發雷達（YouTube）</CardTitle>
          {state === "active" && <Badge>已訂閱（有效）</Badge>}
          {state === "grace" && <Badge variant="secondary">已取消 · 有效至 {endDate}</Badge>}
          {state === "unpaid" && <Badge variant="destructive">未完成付款</Badge>}
          {state === "expired" && <Badge variant="outline">已結束</Badge>}
        </div>
        <CardDescription>{description[state]}</CardDescription>
        {paused && (
          <p className="text-sm text-muted-foreground">
            ⏸️ 通知已暫停：目前不會寄爆發通知信，也不影響扣款與有效期限。
          </p>
        )}
      </CardHeader>
      <form onSubmit={handleSubmit}>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="radar-keywords">關注的領域關鍵字（最多 {MAX_KEYWORDS} 個）</Label>
            <Input
              id="radar-keywords"
              placeholder="例如：貓咪、居家收納、露營"
              value={keywordsValue}
              disabled={isLoading}
              onChange={(event) => setKeywordsDraft(event.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="radar-ratio">爆發門檻：觀看數達頻道平均的幾倍</Label>
            <select
              id="radar-ratio"
              className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm"
              value={ratioValue}
              disabled={isLoading}
              onChange={(event) => setRatioDraft(Number(event.target.value))}
            >
              {RATIO_OPTIONS.map((r) => (
                <option key={r} value={r} className="bg-background">
                  {r} 倍
                  {r === 3
                    ? "（建議）"
                    : r === 2
                      ? "（通知較多）"
                      : r === 10
                        ? "（只要大爆的）"
                        : ""}
                </option>
              ))}
            </select>
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
        </CardContent>
        <CardFooter className="flex flex-wrap gap-2">
          <Button type="submit" disabled={busy || !keywordsValue.trim()}>
            {isRedirecting
              ? "前往付款頁…"
              : saveMutation.isPending
                ? "處理中…"
                : submitLabel[state]}
          </Button>
          {paid && (
            <Button
              type="button"
              variant={paused ? "default" : "secondary"}
              disabled={busy}
              onClick={() => pauseMutation.mutate(!paused)}
            >
              {pauseMutation.isPending ? "更新中…" : paused ? "恢復通知" : "暫停通知"}
            </Button>
          )}
          {state === "active" && (
            <Button type="button" variant="outline" disabled={busy} onClick={handleCancel}>
              {cancelMutation.isPending ? "取消中…" : "取消訂閱"}
            </Button>
          )}
        </CardFooter>
      </form>
    </Card>
  );
}
