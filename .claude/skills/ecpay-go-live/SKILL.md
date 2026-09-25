---
name: ecpay-go-live
description: Walkthrough for flipping the Flight Price Notifier's ECPay (綠界) integration from the shared stage test merchant to a real production MerchantID so you can charge real subscribers — applying for a real merchant account (信箱/身分/銀行 verification, 3–5 工作日), CONFIRMING 信用卡定期定額 is enabled on it, swapping the real MerchantID/HashKey/HashIV into the flight/ecpay Secrets Manager secret, flipping the cashier URL to payment.ecpay.com.tw, and a small real-charge smoke test + refund. Part of M3 go-live, but the real-money flip is optional. Use when the student says "開通正式環境", "go live", "switch ECPay to production", "申請正式金流", or "start accepting real payments".
---

# ECPay Go-Live — stage test merchant → production MerchantID (Flight Price Notifier)

## Scope + when to use this skill

The course runs ECPay on the **shared stage test merchant** (`MerchantID=3002607`) through M2 by design — students walk the whole loop (recurring checkout → callback Lambda → `subscription_status = active` → only-active-users-emailed) without applying for a real merchant account. This skill is the **flip to a real production merchant** so you can charge real subscribers. It belongs in **M3** (go-live), but the real-money portion is **optional** — M3's custom-domain + Resend-domain work stands on its own, and a student can stay on the stage merchant forever if they're not charging real cards yet.

Use this when the student has:
1. ✅ Finished **M2** (stage recurring subscription end-to-end green — pay with the stage test card → row flips to `active` → only `active` users get emails).
2. ✅ Bound a **custom domain** (M3) so the front-end + callbacks live on `yourdomain.com`, not `*.vercel.app` / the raw API Gateway URL.
3. ✅ Decided to charge real customers — and is ready for ECPay's **信箱 / 身分 / 銀行** verification and the **3–5 工作日** review.

If any is missing, finish it first — don't half-activate.

> **Model note (Stripe → ECPay):** an earlier version of this course used Stripe, where go-live meant "activate the account, recreate product/price in *live mode*, swap `sk_test_`→`sk_live_`, recreate the webhook endpoint for a new `whsec_`." **ECPay is different:** there is **no separate live "mode" with parallel objects** — you apply for a **real MerchantID** (with its own `HashKey`/`HashIV`), point the cashier at the **prod URL**, and put those creds in `flight/ecpay`. There are **no products/prices to recreate** (the price is just the `TotalAmount` in your form) and **no webhook endpoint to recreate** (your `ReturnURL`/`PeriodReturnURL` Lambdas are the same; only the merchant + cashier URL change). The activation + smoke-test + refund discipline is identical.

---

## What changes when you go live (the full surface area)

Production is a **different merchant identity + a different cashier host**. Almost nothing else changes.

| Surface | M2 stage | After go-live | Where |
|---|---|---|---|
| ECPay account | shared stage test merchant | **your own, verified** | `vendor.ecpay.com.tw`, §1 |
| `MerchantID` | `3002607` (shared) | **your real MerchantID** | `flight/ecpay`, §3 |
| `HashKey` / `HashIV` | public stage keys | **your real keypair** | `flight/ecpay`, §3 |
| 定期定額 enabled? | always on (stage) | **must confirm enabled on your merchant** | dashboard, §2 |
| Cashier URL | `payment-stage.ecpay.com.tw/Cashier/AioCheckOut/V5` | `payment.ecpay.com.tw/Cashier/AioCheckOut/V5` | `env` in `flight/ecpay` → handler, §4 |
| `CreditCardPeriodAction` URL | `payment-stage…/Cashier/CreditCardPeriodAction` | `payment…/Cashier/CreditCardPeriodAction` | handler, §4 |
| `ReturnURL`/`PeriodReturnURL` | API GW (or custom domain) | **same URL** | n/a |
| Stage test card `4311-9522-…` | works | **stops working** | n/a |
| Real cards | rejected | accepted | n/a |

**The callback URLs do NOT change** — only the merchant identity and the cashier host the form posts to.

---

## How M3 connects to this skill

M3 binds your custom domain (front-end on Vercel + a verified Resend sending domain). The `ReturnURL`/`PeriodReturnURL` should point at your **API Gateway URL** — or, if you mapped a custom domain to the API, that. Going live without M3 still works (the callbacks can point at the raw `*.execute-api…` URL), but the conventional order is M3 first so customers and ECPay's receipts see your brand.

---

## Section 1 — Apply for a real ECPay merchant account

> Dashboard time + a **3–5 工作日** review (excludes weekends/holidays), which starts only after all three basic verifications pass.

1. Go to `vendor.ecpay.com.tw` → 註冊 / 登入.
2. Complete the **三項基本驗證**: 信箱驗證, 身分驗證 (個人/公司), 銀行帳號驗證 (撥款帳戶).
3. 申請服務 → **金流 - 信用卡收款** (and 非信用卡收款 if you ever want ATM/CVS — not needed for the recurring flow).
4. Submit. Review takes **3–5 工作日**; ECPay emails you when approved with your **正式 MerchantID + HashKey + HashIV**.

> Common rejections: 銀行帳戶 name ≠ legal entity name; incomplete 身分驗證; address/identity mismatch. Resolve and re-submit before §5.

---

## Section 2 — CONFIRM 信用卡定期定額 is enabled on your merchant (ECPay-specific gotcha)

> This step has no Stripe equivalent and is the easiest thing to miss.

The shared **stage** merchant has 定期定額 always available, but a **real** merchant may have it as a **separately-enabled feature**. ECPay's own docs do **not** guarantee the recurring service is on by default. Before you write any real creds:

- In `vendor.ecpay.com.tw`, check your 合約 / 啟用的服務 for **信用卡定期定額**. If it's not listed/enabled, request it (合約面) or call ECPay (**02-2655-1775**) to confirm it's switched on for your MerchantID.
- If you skip this, the recurring checkout form will be **rejected at the real cashier** even though the identical form worked on stage — a confusing "it worked yesterday" failure.

**Verify:** your merchant's enabled-services list shows 信用卡定期定額 (or ECPay support has confirmed it). Only then proceed.

---

## Section 3 — Put the real merchant creds into `flight/ecpay` (Secrets Manager)

There are **no Vercel ECPay env vars** in this course — the Lambdas read `flight/ecpay` from Secrets Manager at runtime. You're replacing the *values* in that one secret (and flipping `env` to `prod`, which §4 uses to choose the cashier host).

```bash
aws secretsmanager put-secret-value --secret-id flight/ecpay \
  --secret-string '{"merchant_id":"<real MerchantID>","hash_key":"<real HashKey>","hash_iv":"<real HashIV>","env":"prod","amount":"<your TWD monthly amount>"}' \
  --region us-east-1
```

> **No redeploy needed.** The Lambdas read `flight/ecpay` on their next cold start / invocation — there's no build to trigger. (If a handler caches the secret at module load, force a fresh cold start with `aws lambda update-function-configuration --function-name flight-save-subscription --description "rotate"` — or wait for the warm container to recycle.) **Never leave the stage creds (`3002607`) in a prod secret** — and never put the real `HashKey`/`HashIV` in code or the front-end.

---

## Section 4 — Flip the cashier + period-action URLs to prod

Your handlers must choose the cashier host from the secret's `env`. If you already coded that in M2, this is just data (§3 flips `env` to `prod`); otherwise patch the two hosts:

| | Stage URL | Prod URL |
|---|---|---|
| Cashier (checkout form `action`) | `payment-stage.ecpay.com.tw/Cashier/AioCheckOut/V5` | `payment.ecpay.com.tw/Cashier/AioCheckOut/V5` |
| `CreditCardPeriodAction` (cancel) | `payment-stage.ecpay.com.tw/Cashier/CreditCardPeriodAction` | `payment.ecpay.com.tw/Cashier/CreditCardPeriodAction` |

```python
host = "payment.ecpay.com.tw" if cfg["env"] == "prod" else "payment-stage.ecpay.com.tw"
cashier = f"https://{host}/Cashier/AioCheckOut/V5"
period_action = f"https://{host}/Cashier/CreditCardPeriodAction"
```
Confirm `save_subscription` builds the form `action` from this, and `flight-cancel-subscription` posts to the prod period-action URL. **No redeploy** if it's secret-driven — just the `env: "prod"` from §3.

---

## Section 5 — Smoke test against production (small real charge + refund)

> **Pre-flight:** §1 approved, §2 confirmed 定期定額 enabled, §3/§4 prod creds + URL in place. Use a **small** real amount — but note ECPay hides credit-card payment below the card minimum (~NT$6–11), so a NT$1 test won't show the card option. Use the smallest realistic amount your `flight/ecpay` `amount` allows (e.g. NT$30–100 for the test, then raise it).

### 5.1 — Real recurring charge
1. Open your live site's subscribe flow (the M0/M1 form on your custom domain).
2. Log in as **yourself** (a real account on your Supabase whose subscription you can later inspect).
3. Subscribe to a route → the form auto-POSTs to ECPay's **prod** cashier.
4. **Pay with a real card you own** (complete 3DS/OTP for real this time). Land back on `/account?purchase=success`.

### 5.2 — Confirm it landed (three places, consistent within ~1 min)
1. **ECPay 廠商後台 → 信用卡定期定額訂單查詢** (`p=2892`): your order shows the first authorization succeeded.
2. **CloudWatch:** `aws logs tail /aws/lambda/flight-ecpay-return --since 10m --region us-east-1` → CMV verified, `UpdateItem`, replied `1|OK`, no error.
3. **DynamoDB:** the row is now `active`:
   ```bash
   aws dynamodb get-item --table-name subscriptions \
     --key '{"email":{"S":"<your email>"},"route":{"S":"TPE-TYO"}}' \
     --region us-east-1 \
     --query 'Item.{status:subscription_status,gwsr:ecpay_gwsr,trade:merchant_trade_no}'
   ```
   Expect `subscription_status = active`, `ecpay_gwsr` + `merchant_trade_no` set. (Then a parser run would email you if a fare is at/below target.)

### 5.3 — Cancel + refund yourself
Don't keep a real recurring charge against your own card.
- **Cancel the subscription:** use your `/account` 「取消訂閱」 button → your `/cancel` Lambda calls `CreditCardPeriodAction Action=Cancel` → the row flips to `expired`. (Or stop it in the 廠商後台.)
- **Refund the charge:** in the ECPay 廠商後台 → 信用卡 → 退刷/退款 (there's no test-mode auto-refund; it's a real backoffice action; a 退款手續費 may apply).
- **Verify** the row flipped to `expired`:
  ```bash
  aws dynamodb get-item --table-name subscriptions \
    --key '{"email":{"S":"<your email>"},"route":{"S":"TPE-TYO"}}' \
    --region us-east-1 --query 'Item.subscription_status'
  ```

### 5.4 — Stage test cards are now closed
On the **prod** cashier, the stage test card `4311-9522-2222-2222` is rejected — that confirms you're live. (Keep the stage merchant creds somewhere for future debugging; ECPay routes by MerchantID + cashier host, so stage and prod don't interfere.)

---

## Section 6 — Sanity checklist

- [ ] ECPay account **approved** (real MerchantID issued).
- [ ] **信用卡定期定額 confirmed enabled** on your merchant (§2) — the easy-to-miss one.
- [ ] `flight/ecpay`: `merchant_id`/`hash_key`/`hash_iv` are the **real** ones, `env` = `prod`, `amount` = your TWD monthly price. No `3002607` left.
- [ ] Cashier `action` resolves to `payment.ecpay.com.tw/...` (not `-stage`); `CreditCardPeriodAction` likewise.
- [ ] §5 smoke test: real card → CMV-verified `1|OK` in CloudWatch → row `active` → cancel+refund → row `expired`; the 廠商後台 order查詢 agrees.
- [ ] No `HashKey`/`HashIV`/AWS key / Supabase `service_role` key in the front-end bundle (grep the deployed site).

If any box is unchecked, fix it before announcing launch.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Real cashier rejects the form (`MerchantID 不存在` / 交易失敗) but stage worked | prod MerchantID with stage `HashKey`/`HashIV` (or vice-versa) — CMV passes but merchant check fails | put the **matching** real keypair in `flight/ecpay`; never mix stage+prod creds |
| Cashier rejects the **recurring** form specifically (one-time would work) | 信用卡定期定額 not enabled on your merchant (§2) | request/confirm the service in 廠商後台 or via 02-2655-1775 |
| Form posts to `-stage` cashier despite prod creds | `env` still `stage`, or the handler hardcodes the stage host | set `env:"prod"` (§3) + build the host from it (§4) |
| Callback `CheckMacValue Error` after switching | computing CMV with the stage keypair, or a trailing newline in a URL env | use the real `HashKey`/`HashIV`; `.strip()` URL envs — see [[ecpay-best-practice]] Rule 2 |
| Row never flips to `active` though 後台 shows paid | callback rejected (CMV / empty-field bug) or replied ≠ `1|OK` | CloudWatch `flight-ecpay-return`; [[ecpay-best-practice]] Rules 2–3 |
| 模擬付款 still grants on stage | `SimulatePaid` not guarded | [[ecpay-best-practice]] Rule 7 — verify CMV, reply `1|OK`, don't activate |

---

## Why most of the codebase is untouched

- **Lambda code** (`save_subscription`, the callbacks, `cancel_subscription`) — entirely secret-driven. Real MerchantID/HashKey/HashIV for stage ones is invisible to the code; no edit, no redeploy (only the cashier host flips on `env`).
- **The parser / notification Lambdas** — never talk to ECPay; they only read `subscription_status`. No change.
- **DynamoDB schema** — identical; only the secret's *values* + `env` change.
- **The callback URLs** — same API Gateway / custom-domain URLs; only the merchant + cashier host change.
- **`flight/ecpay` key names** — same JSON keys, new values.

## Cost reminder

Applying is free. Production introduces ECPay's **per-transaction fee** (信用卡 default ~2.75%; you may negotiate ~2.45% or lower with volume — settle the rate *before* signing). A 退款手續費 applies to refunds. 電子發票 is a separate paid service if you need to issue invoices. No change to your AWS/Vercel/Supabase bill — ECPay's fee is the only new line item.

## When done

> 「ECPay 正式金流開通完成 — 真的 MerchantID / HashKey / HashIV 都寫進 `flight/ecpay` secret、`env=prod`、收銀台指向 `payment.ecpay.com.tw`；信用卡定期定額已確認啟用；用真實卡實測訂閱一筆，DynamoDB 的 row 變 `active`，取消+退款後變 `expired`，三邊（ECPay 後台訂單查詢、CloudWatch、DynamoDB）對得起來。可以開始收真錢。」

## Cross-references

- [[m2-ecpay-subscription]] — the stage recurring subscription this flips to production.
- [[m3-domain]] — the custom domain the callbacks point at.
- [[ecpay-best-practice]] — the callback hard rules (CMV, empty-field, `1|OK`, two callbacks, SimulatePaid, cancel-is-an-API-call).
- [[aws-best-practice]] — `flight/ecpay` lives in Secrets Manager; the form-body handling from the AWS side.
- ECPay 申請正式金流: https://www.ecpay.com.tw/ · 廠商後台: https://vendor.ecpay.com.tw/ · 信用卡定期定額: https://developers.ecpay.com.tw/?p=2868
