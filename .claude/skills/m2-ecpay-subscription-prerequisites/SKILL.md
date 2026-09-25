---
name: m2-ecpay-subscription-prerequisites
description: Prerequisites before M2 of the Flight Price Notifier course — an ECPay (綠界) merchant (the public stage test merchant is fine for the whole course), the `flight/ecpay` secret, and confirmation that M1.3's notifier works. Use when the student starts M2, or when `m2-ecpay-subscription` / `-checklist` detects ECPay isn't set up.
---

# M2 Prerequisites — ECPay 綠界

## What this skill does

M2 adds one account: **ECPay 綠界**. Unlike Stripe there's **no CLI to install and no live/test "mode" toggle** — you just need merchant credentials (MerchantID / HashKey / HashIV) in the `flight/ecpay` secret. For the whole course you can use ECPay's **public stage test merchant**; applying for a real one is M3 ([[ecpay-go-live]]). This skill stores the secret + confirms the M1.3 notifier carryover.

## Architecture

![Flight Fare / Notification architecture (M2) — the payment layer these prerequisites set up the ECPay merchant credentials for. The Product Site [Vercel] POSTs to the ECPay Lambda Handlers (flight-ecpay-return / flight-ecpay-period / flight-cancel-subscription), which talk to ECPay and write subscription_status onto Subscriptions [DynamoDB]; a "subscription check" gate on that table is what makes the Parser scan only active rows. On payment events the handlers enqueue to the Notification-side SQS, where the Subscription Status Notification Lambda emails welcome/cancel via Resend. The M1 flow remains: EventBridge → Parser Wrapper → Parser (×N) reads Flight Routes [S3] + the travelpayouts API, scans Subscriptions, enqueues matches to the Flight Fare Notification SQS → Flight Fare Notification Lambda dedups against Notification History [DynamoDB] and emails via Resend. Inset: the subscribe → ECPay → callback (W = write) loop that flips subscription_status. Legend: orange = manual input, teal = main component, pink = user data.](assets/flight_notification_structure2.jpg)

## When to load this skill

- "M2 環境準備" / any time M2 detects ECPay (`flight/ecpay`) is missing.

## Execution mode

CLI uses `aws` + `curl`/`python3`; Cowork uses AWS MCP + the ECPay 廠商後台 (`vendor.ecpay.com.tw`). `aws` commands `--region us-east-1`. (There is **no `ecpay` CLI** — ECPay is a hosted gateway with a dashboard only.)

## Step 1 — ECPay credentials (stage test merchant is fine)

You don't need a real merchant account to build and test M2. Use ECPay's **public shared stage test merchant** (these are published by ECPay and safe to commit as defaults):

| Field | Stage value |
|---|---|
| `MerchantID` | `3002607` |
| `HashKey` | `pwFHCqoQZGmho4w6` |
| `HashIV` | `EkRm7iFT261dpevs` |

**Stage 廠商後台 (shared, public — for 模擬付款 + 訂單查詢):** the shared test merchant comes with a **public test backoffice** you log into — a *separate* host from production. The login form needs **four** fields (the 統一編號 one trips everyone up):

| 欄位 | Stage value (use this in M2) | Production (M3 go-live) |
|---|---|---|
| 後台網址 | **`https://vendor-stage.ecpay.com.tw/`** | `https://vendor.ecpay.com.tw/` |
| 廠商編號 (MerchantID) | `3002607` — goes in your **code/checkout form**, **NOT** the login field | your own |
| 賣家帳號 (login) | `stagetest3` | your own |
| 登入密碼 | `test1234` | your own |
| 統一編號 | `00000000` (eight zeros — **required** on the login page) | your real 統編 |
| 驗證碼 | whatever the page shows (refresh ↻ for a clearer one) | — |

> **`3002607` is the MerchantID, NOT the login.** Typing `3002607` into 賣家帳號 gives `帳號格式錯誤` — the login is `stagetest3` + `test1234` + 統編 `00000000`. (These are ECPay's **published shared** test creds, from `developers.ecpay.com.tw/?p=2856` — verify there if ECPay rotates them.)

This shared backoffice is what lets you press **模擬付款** and open **信用卡定期定額訂單查詢** on the `3002607` orders **without** a real merchant account. It's **shared/public** (you'll see other testers' orders too — filter by your `MerchantTradeNo`) — fine for testing, but it is **not** your private console; a real MerchantID + your own backoffice is **not** required until go-live ([[ecpay-go-live]]).

> **Why this matters:** an order you pay via `3002607` does **not** appear in any *private* console (you don't have one yet) — it lives in this **shared `vendor-stage` backoffice**. Your real M2 acceptance evidence is **the callback hitting your Lambda (CloudWatch) + the DynamoDB row flipping** — the backoffice is only for the optional 模擬付款 / 查單 convenience.

> **Don't bother applying for your own "專屬測試帳號".** ECPay does offer a private test merchant (a clean, non-shared stage backoffice + your own stage MerchantID), **but it takes a 1–3 工作天 review** — it is NOT instant/self-service. The shared `3002607` + `stagetest3` backoffice tests **everything** in M2 (模擬付款, 定期定額訂單查詢, full callback flow), and ECPay's own docs say *"先用共用測試帳號開發,不必等審核"*. So **M2 uses the shared account only**; you apply for a real merchant at **go-live (M3, [[ecpay-go-live]])**, skipping the private-test-account step entirely.

**Stage test card** (for the whole course): card `4311-9522-2222-2222`, expiry `12/30`, CVV `222`, OTP `1234`. No real money moves.

## Step 2 — Store the `flight/ecpay` secret

```bash
aws secretsmanager describe-secret --secret-id flight/ecpay --region us-east-1 --query "Name" 2>/dev/null \
  || aws secretsmanager create-secret --name flight/ecpay \
       --secret-string '{"merchant_id":"3002607","hash_key":"pwFHCqoQZGmho4w6","hash_iv":"EkRm7iFT261dpevs","env":"stage","amount":"300"}' \
       --region us-east-1
```
`amount` is our **implemented monthly price: `300` (NT$300)**. Students can later set it to any integer TWD value they want — everything downstream reads it from this secret, so the price is a one-line change with no code edits. Keep it realistic — ECPay hides credit-card payment below the card minimum (~NT$6–11), so don't use NT$1.

**Verify:**
```bash
aws secretsmanager get-secret-value --secret-id flight/ecpay --region us-east-1 --query SecretString --output text
```
Shows `merchant_id`, `hash_key`, `hash_iv`, `env:"stage"`, and your `amount`.

## Step 3 — Confirm M1.3 carryover

```bash
# the notifier pipeline exists and sends email (M1.2 parser + M1.3 fare-notification + Resend)
aws lambda get-function --function-name flight-parser --region us-east-1 --query 'Configuration.FunctionName'
aws lambda get-function --function-name flight-fare-notification --region us-east-1 --query 'Configuration.FunctionName'
aws secretsmanager describe-secret --secret-id flight/resend --region us-east-1 --query 'Name'
# save_subscription Lambda exists (M1.1) — M2 upgrades it to write pending_payment + build the ECPay recurring form
aws lambda get-function --function-name flight-save-subscription --region us-east-1 --query 'Configuration.FunctionName'
```
If any are missing, finish the corresponding earlier milestone first.

## Heads-up — two things that look like failures but aren't

- **Resend sandbox only delivers to YOUR OWN address.** While `flight/resend` sends from `onboarding@resend.dev` (pre-M3), welcome/cancel/fare emails reach **only the Resend account's own verified email**; any other recipient `403`s `validation_error`. So test M2's emails **to yourself** — it's not broken, it's the sandbox. Real delivery to anyone needs a **verified sending domain** (M3 / [[resend-best-practice]]).
- **`OrderResultURL` needs a redirect Lambda, or you get a 405 right after paying.** ECPay returns the browser via a **POST**; a static SPA (Vercel/Netlify) only serves GET on a page route → **405 "This page isn't working"** (the payment still succeeded via the S2S `ReturnURL`). M2 Step 2/3 builds a tiny `flight-ecpay-result` 302-redirect Lambda for `OrderResultURL` — know this symptom up front. (See [[ecpay-best-practice]] Rule 11.)

## Verify (all must pass)

- `flight/ecpay` secret exists with stage `merchant_id` + your `amount` ✅
- `flight-parser` + `flight-fare-notification` + `flight/resend` exist (M1.2/M1.3 done) ✅
- `flight-save-subscription` exists (M1.1 done) ✅

## Next step

Return to `m2-ecpay-subscription` Step 1.

## Reference

- [[ecpay-best-practice]] — the CMV + callback rules you'll apply throughout M2.
- ECPay stage test info / 測試帳號: https://developers.ecpay.com.tw/?p=2856
