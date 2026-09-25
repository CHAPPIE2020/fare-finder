---
name: m2-ecpay-subscription-checklist
description: Flight Price Notifier Milestone 2 verification — confirms the ECPay recurring checkout works, the callbacks verify CheckMacValue and flip subscriptions active/expired in DynamoDB, cancel calls CreditCardPeriodAction, and only paying users get alerts. Use when the student says "驗收 M2", "check M2", or after `m2-ecpay-subscription` Step 6.
---

# M2 — ECPay Checklist

## What this skill does

Confirms the paywall really works end-to-end: recurring checkout → callback (CMV-verified) → `active`; cancel → `expired`; and the active-only gating actually controls who gets emailed. Emits `READY for M3`. Run after `m2-ecpay-subscription` Step 6.

## Architecture

![Flight Fare / Notification architecture (M2) — this checklist verifies the payment layer M2 adds on top of the M1 notifier. The Product Site [Vercel] POSTs to the ECPay Lambda Handlers (flight-ecpay-return / flight-ecpay-period / flight-cancel-subscription), which talk to ECPay and write subscription_status onto Subscriptions [DynamoDB]; a "subscription check" gate on that table is what makes the Parser scan only active rows. On payment events the handlers enqueue to the Notification-side SQS, where the Subscription Status Notification Lambda emails welcome/cancel via Resend. The M1 flow remains: EventBridge → Parser Wrapper → Parser (×N) reads Flight Routes [S3] + the travelpayouts API, scans Subscriptions, enqueues matches to the Flight Fare Notification SQS → Flight Fare Notification Lambda dedups against Notification History [DynamoDB] and emails via Resend. Inset: the subscribe → ECPay → callback (W = write) loop that flips subscription_status. Legend: orange = manual input, teal = main component, pink = user data.](assets/flight_notification_structure2.jpg)

## Execution mode

Mainly **Cowork** — paste checks to the agent with the **AWS API MCP**; CLI runs the same `aws` lines. All commands `--region us-east-1`, `[default]` profile. MCP rules that bite here: **verify by effect** (can't `cat` an `invoke`/output file — read the DynamoDB row or the logs instead); **`filter-log-events`**, not `logs tail`; **no JMESPath backtick literals** (use `SecretList[].Name`, scan the list). There is **no `ecpay` CLI or MCP** — ECPay-side steps are driven from the **廠商後台** (模擬付款 button + 信用卡定期定額訂單查詢) and from a real **stage test-card** run in the browser. The `POST` checks (`/subscribe`, `/cancel`, `/ecpay-result`) are **mode-dependent**: Cowork can't POST (no shell with AWS net; the web tool is GET-only) → prove those by their **effect** (the DynamoDB row + the callback logs) and by driving the live form in the browser. CLI can run the `curl` lines directly.

## How to run

Run each check and report. Ask the student for: the API Gateway base URL, the live Vercel URL, and a test inbox.

> **⚠️ Use the Resend-account owner's email as the test subscriber for B3 / C1 / E1.** While `flight/resend` still sends from `onboarding@resend.dev` (pre-M3, no verified domain), the Resend **sandbox only delivers to the account owner's own verified address** — any other recipient `403`s `validation_error` and the welcome/cancel email silently never arrives. So set the subscriber email to **your Resend-account email** (e.g. `uopsaoa@gmail.com`) — the examples below write `<your-resend-account-email>`; substitute that address everywhere. Using a throwaway like `pay@test.com` here would make C1/E1's email checks fail for the wrong reason. (See [[ecpay-best-practice]] watch-out 16.)

Subscription rows are the authoritative source — read them with `aws dynamodb get-item` (works identically via the AWS API MCP).

### Section A — ECPay checkout form
- **A1** The `flight/ecpay` secret exists with stage `merchant_id` + an `amount` (our implemented price is `300` = NT$300; any integer the student chose is acceptable): `aws secretsmanager get-secret-value --secret-id flight/ecpay --region us-east-1 --query SecretString --output text`.
- **A2** `POST /subscribe` returns an **auto-submit HTML form** whose `action` is the ECPay cashier and that contains a `CheckMacValue` hidden field + `PeriodType`/`PeriodAmount`. **Mode-dependent:**
  - **CLI:**
    ```bash
    curl -s -X POST "<api>/subscribe" -H "content-type: application/json" \
      -d '{"email":"<your-resend-account-email>","origin":"TPE","destination":"TYO","depart_month":"2026-07","target_price":400}' \
      | grep -oE 'AioCheckOut/V5|CheckMacValue|PeriodType'
    ```
    Expect all three tokens.
  - **Cowork** (can't POST): submit the subscribe form in the **browser** on the live Vercel URL and confirm it bounces to the ECPay cashier (URL contains `AioCheckOut/V5`) — that proves the form + CMV server-side. Then verify by **effect**: a `pending_payment` row with a `merchant_trade_no` was written (B-section `get-item` below works for this).
  - **Both:** confirm the `pending_payment` row with a `merchant_trade_no` exists:
    ```bash
    aws dynamodb get-item --table-name subscriptions \
      --key '{"email":{"S":"<your-resend-account-email>"},"route":{"S":"TPE-TYO"}}' \
      --region us-east-1 --query 'Item.{status:subscription_status,mtn:merchant_trade_no}'
    ```

### Section B — Callbacks verify CMV + flip to active

> **No card handy? Verify the callback path with a validly-signed synthetic callback (same-day).** The real cashier needs a human (card + OTP), but B2/B3 (and E1/D3 grace) can be proven **without a card** by POSTing a callback you sign yourself with the real `flight/ecpay` secret — `RtnCode=1`, `CustomField1=<email>`, `CustomField2=<route>`, **including the empty `CustomField3=&CustomField4=`**, `CheckMacValue` via `gen_cmv` (no `SimulatePaid`) — to the deployed `…/ecpay-return`, then assert the row flips to `active`. This catches CMV / empty-field / idempotency bugs early. It's a **backend** proof only (no cashier UI / `OrderResultURL`), so still do **one** real stage test-card run before signing off, and record the two separately (see [[ecpay-best-practice]] Rule 8). *(Cowork can't POST from the sandbox — run the signed POST from a throwaway Lambda or the CLI.)*

- **B1** Both callback Lambdas exist — two separate calls (Cowork MCP runs one API call at a time, no shell loop): `aws lambda get-function --function-name flight-ecpay-return --region us-east-1 --query 'Configuration.FunctionName'` and `aws lambda get-function --function-name flight-ecpay-period --region us-east-1 --query 'Configuration.FunctionName'`.
- **B2** CheckMacValue verification works (no rejects). Trigger the first-period callback via the stage 後台「模擬付款」 (or a real test-card run) and read the logs — use `filter-log-events` (Cowork MCP has no `logs tail`):
  ```bash
  aws logs filter-log-events --log-group-name /aws/lambda/flight-ecpay-return \
    --query "events[].message" --region us-east-1
  ```
  Look for CMV verified + `UpdateItem` + replied `1|OK`, no `CheckMacValueInvalid`. **And** a bare 模擬付款 (`SimulatePaid=1`) must NOT grant active (the row stays `pending_payment` — confirm via the `get-item` in B3).
- **B3** **The decisive test:** complete a real **stage test-card** payment (`4311-9522-2222-2222`, `12/30`, CVV `222`, OTP `1234`) via the form → the row flips to `active`:
  ```bash
  aws dynamodb get-item --table-name subscriptions \
    --key '{"email":{"S":"<your-resend-account-email>"},"route":{"S":"TPE-TYO"}}' \
    --region us-east-1
  ```
  Expect `subscription_status=active`, `merchant_trade_no` + `current_period_end` set. *(Don't expect `gwsr` — it comes back **empty** on the real 定期定額 first-period callback; idempotency keys on `merchant_trade_no`. See [[ecpay-best-practice]] Rule 4.)*
- **B4** *(renewal — `PeriodReturnURL`/`flight-ecpay-period`; optional/time-gated)* To verify the renewal path the faithful way, subscribe once with a **daily** period (`PeriodType=D, Frequency=1, ExecTimes=2`) and pay the first charge **successfully** (a failed first auth never enters the scheduler). **The next day**, confirm the scheduler fired the 2nd charge into your period handler:
  ```bash
  aws logs filter-log-events --log-group-name /aws/lambda/flight-ecpay-period \
    --query "events[].message" --region us-east-1
  ```
  Expect CMV-verified, replied `1|OK`, `TotalSuccessTimes=2`, no `SimulatePaid`. *(Fast smoke-only alternative: 模擬付款 on the recurring order → reaches `flight-ecpay-period` with `SimulatePaid=1`; proves reachability + CMV + `1|OK` but not the real bookkeeping — see the M2 skill Step 3.)* Mark ⚠️ "pending next-day check" if you ran the checklist same-day.

### Section C — Status-change email (one consumer, routed by event_type)
- **C0** The status queue + its single consumer exist: `aws sqs get-queue-url --queue-name flight-status-queue --region us-east-1` and `aws lambda get-function --function-name flight-status-notification --region us-east-1 --query 'Configuration.FunctionName'`.
- **C1** After B3, a **welcome** email arrives at the test inbox (the `flight-ecpay-return` callback enqueued `{event_type:"welcome"}` → the one `flight-status-notification` consumer sent it via Resend).

- **B5** *(`OrderResultURL` returns 302, not 405)* The browser-return endpoint redirects instead of erroring. ECPay delivers it as a **POST**. **Mode-dependent:**
  - **CLI:**
    ```bash
    curl -s -o /dev/null -w "%{http_code}\n" -X POST "<api>/ecpay-result" -d "RtnCode=1"
    ```
    Expect **`302`** (Location → `/app?purchase=success`), **NOT `405`**.
  - **Cowork** (can't POST): you already exercise this for real in B3 — after the stage test-card payment the browser is returned to the app; if you **land on `/app?purchase=success`** (not an error page) the `OrderResultURL` redirect is working. As a cross-check, confirm a dedicated `flight-ecpay-result` Lambda exists (`aws lambda get-function --function-name flight-ecpay-result --region us-east-1 --query 'Configuration.FunctionName'`) — its absence is the 405 cause.
  
  A 405 means `OrderResultURL` points at the static SPA (Rule 11) — the payment still works but the UX is broken.

### Section D — Gating works, grace-aware (the point of M2)
- **D0** The parser gate (Step 4) is **grace-aware**, not plain `active` — invoking the parser for a route with a `pending_payment` row whose target is met does NOT enqueue it. Invoke, then verify **by logs** (Cowork can't `cat` the output file — read `filter-log-events`):
  ```bash
  aws lambda invoke --function-name flight-parser \
    --payload '{"origin":"TPE","destination":"TYO","route":"TPE-TYO"}' /tmp/p.json \
    --region us-east-1
  aws logs filter-log-events --log-group-name /aws/lambda/flight-parser \
    --query "events[].message" --region us-east-1
  ```
  Look for the gate decision (`active`/`cancelled`/`skip`/`enqueue`/`expired`) in the messages.
- **D1** The now-`active` row (target above live fare) **is** enqueued + emailed when the parser runs → fare email arrives.
- **D2** A `pending_payment` (unpaid) row is NOT enqueued/emailed. Confirms payment gates alerts.
- **D3** A `cancelled` row with a **future** `current_period_end` **IS** still enqueued (grace). A `cancelled` row with a **past** `current_period_end` is NOT, and the parser flips it to `expired` on that run.

### Section E — Cancellation (an API call you make; grace, not instant expiry)
- **E1** The cancel Lambda exists; `POST /cancel` calls `CreditCardPeriodAction` and flips the row to **`cancelled`** (NOT `expired`), preserving `current_period_end`. **Mode-dependent — trigger the cancel, then verify by effect:**
  - **CLI** triggers it directly:
    ```bash
    curl -s -X POST "<api>/cancel" -H "content-type: application/json" -d '{"email":"<your-resend-account-email>","route":"TPE-TYO"}'
    ```
  - **Cowork** (can't POST): click **取消訂閱** in the live app UI for that subscriber instead.
  - **Both** then read the authoritative row (works via the AWS API MCP):
    ```bash
    aws dynamodb get-item --table-name subscriptions \
      --key '{"email":{"S":"<your-resend-account-email>"},"route":{"S":"TPE-TYO"}}' \
      --region us-east-1 --query 'Item.{status:subscription_status,end:current_period_end}'
    ```
  Expect `status=cancelled` with `current_period_end` set. A **cancel** email also arrives (same status consumer, `{event_type:"cancel"}`). In the 後台 → 信用卡定期定額訂單查詢, the series shows terminated (no more renewals). *(Stage cancel of a never-paid synthetic order returns `90100150 不存在的訂單編號` — expected; the Lambda should log it and still cancel locally, not treat it as a failure.)*
- **E2** A `cancelled`-in-grace subscriber can **update their target price** in place (no re-payment, status stays `cancelled`). **Mode-dependent:** CLI re-POSTs `/subscribe` with a new `target_price` and expects a JSON response (not an ECPay form); Cowork edits the target in the **live app form** instead. **Both** confirm by **effect** with `get-item` — `target_price` changed, `subscription_status` still `cancelled`, no new `pending_payment`.
- **E3** Lifecycle end-to-end: `pending_payment → active → cancelled (grace, still alerted) → expired` (after `current_period_end` passes, via the parser). The expired row is no longer enqueued/emailed.

## Reporting

| Check | Status | Notes |
|---|---|---|
| A1 flight/ecpay secret | ✅/❌ | |
| A2 checkout form (CMV + Period*) | ✅/❌ | |
| B1 both callback Lambdas | ✅/❌ | return + period |
| B2 CMV verified + SimulatePaid guarded | ✅/❌ | the fiddly one |
| B3 payment → active (+ current_period_end) | ✅/❌ | the key one |
| B4 renewal → PeriodReturnURL (daily test) | ✅/❌/⚠️ | ⚠️ if next-day check pending |
| B5 OrderResultURL → 302 not 405 | ✅/❌ | the 405-after-paying bug |
| C0 status queue + 1 consumer | ✅/❌ | |
| C1 welcome email sent | ✅/❌ | event_type routing |
| D0 parser gate grace-aware (Step 4) | ✅/❌ | the gate itself |
| D1 active → emailed | ✅/❌ | |
| D2 pending_payment → not emailed | ✅/❌ | gating proof |
| D3 cancelled-in-grace emailed; grace-expired flipped | ✅/❌ | grace period |
| E1 cancel → cancelled (NOT expired) + email | ✅/❌ | API call, grace not instant |
| E2 cancelled-in-grace can update target | ✅/❌ | in-place, no re-pay |
| E3 lifecycle → expired after period passes | ✅/❌ | parser lazily expires |

**Verdict:**
- All ✅ → 「M2 驗收通過 ✅ 產品會賺錢了，只有付費者收得到通知。READY for M3。跟我說『啟動 M3』來掛自己的網域、正式開張。」
- Any ❌ → name failures + recovery:
  - CMV reject / callback "never arrives" but 後台 shows paid → you're **dropping empty-string fields** before hashing; keep `CustomField3=`/`CustomField4=` ([[ecpay-best-practice]] Rule 2). For a `~` in any value, you may be using the buggy `ecpayUrlEncode` — validate against `ecpay/test-vectors/checkmacvalue.json` (Rule 2).
  - not flipping → check `CustomField1/2` (email+route) + `UpdateItem` + IAM DynamoDB perms; idempotency on **`MerchantTradeNo` not `gwsr`** (`gwsr` is empty on recurring — Rule 4).
  - **405 right after paying** → `OrderResultURL` points at the static SPA; add the `flight-ecpay-result` 302 Lambda (Rule 11). Payment still succeeded.
  - renewals don't update → you only built `ReturnURL`; add the `PeriodReturnURL` handler ([[ecpay-best-practice]] Rule 1/9).
  - **cancel expires instantly instead of granting grace** → cancel must set `cancelled` + keep `current_period_end`, and the parser serves `cancelled`-in-grace + lazily expires (Rules 9, 12). Don't set `expired` in the cancel Lambda.
  - 模擬付款 grants free access → guard `SimulatePaid` (Rule 7).
  - cancel does nothing → cancel is `CreditCardPeriodAction Action=Cancel` that **you** call, not an event you wait for (Rule 9). `90100150` on a never-paid order is expected.
  - emails not arriving → Resend sandbox only reaches your own account email (verify a domain at M3).
  - **welcome/cancel email never arrives, log shows `403` with body `error code: 1010`** → the new `flight-status-notification` Lambda is POSTing to Resend **without a `User-Agent` header**, so Cloudflare (in front of `api.resend.com`) bans it. This is **not** an account/recipient issue — add a `User-Agent` to the POST and reuse M1's `_send`/headers ([[resend-best-practice]] Rule 4). Distinguish from the sandbox `validation_error` 403 by the `1010` code.
  
  then re-run `驗收 M2`.
