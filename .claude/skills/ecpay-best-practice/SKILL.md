---
name: ecpay-best-practice
description: Hard rules and operational SOP for integrating ECPay (綠界) 信用卡定期定額 (credit-card recurring) checkout + callbacks in the Flight Price Notifier course (M2 — monthly subscription paywall). The callbacks are AWS Lambdas; the "ledger" is the subscription_status field on a DynamoDB row. Use whenever a student is building the recurring-checkout form in save_subscription, the ReturnURL/PeriodReturnURL callback Lambdas, the cancel Lambda, debugging "payment succeeded but the user still isn't active", or hitting CheckMacValue / idempotency / 1|OK / empty-field issues. Sourced from real ECPay callback incidents.
---

# ECPay Best Practice (Flight Price Notifier — M2 subscription paywall)

Battle-tested rules for integrating ECPay **信用卡定期定額 (credit-card recurring) Checkout + callbacks** into M2, where payment is what flips a subscription from `pending_payment` to `active` so the user actually receives price-drop emails. Students hit these in the *first hour* of M2, usually before they've internalized that **the callback — not the browser redirect — is the real payment flow**, and that **ECPay sends TWO callbacks (ReturnURL + PeriodReturnURL), not one webhook.**

When guiding a student through any ECPay-touching code, **apply these rules proactively** — stop them before they break one.

> **Model note (Stripe → ECPay):** an earlier version of this course used Stripe (one webhook, a `stripe-signature` header, a delete-event for cancel, a `stripe`+`requests` Lambda layer). This course is **ECPay 綠界**, charging in **TWD**, on **AWS Lambda**, flipping a **`subscription_status` field on a DynamoDB row**. The *principles* are identical — a verified callback is the source of truth, idempotency, identify-by-stable-key-not-payer-email, gate on `active` — but four mechanics differ and are the heart of these rules: **(1) two callbacks not one webhook**, **(2) CheckMacValue not a signature header**, **(3) cancel is an API call you make, not an event you receive**, **(4) no SDK/layer** (CMV is stdlib `hashlib`). The CMV algorithm + the empty-field bug are ported from the instructor's production My Site ECPay integration.

This is the application-layer sibling of [[aws-best-practice]] (the Lambda/IAM/secrets side of the same callbacks) and [[supabase-best-practice]] (auth-only — ECPay never touches Supabase here; the join key is `email`).

---

## Execution mode: Cowork vs CLI

ECPay is a hosted gateway with **no CLI** (unlike Stripe's). The calling code (the Lambdas) runs identically in both modes; the deltas are around testing the callback and credential management.

| Operation | CLI mode | Cowork mode |
|---|---|---|
| Trigger a test callback to your ReturnURL | ECPay 廠商後台 (stage) →「模擬付款」 on a pending order (Rule 7); or a real stage test-card run | same — the dashboard 模擬付款 button, or a real test-card run |
| Inspect a recurring order / its charges | 廠商後台 → 信用卡定期定額訂單查詢 (`p=2892`) | same dashboard |
| Cancel a recurring series | call `CreditCardPeriodAction Action=Cancel` (your `/cancel` Lambda), or 後台 manual stop | same — your `/cancel` Lambda, or the dashboard |
| Store ECPay credentials | into the **`flight/ecpay`** Secrets Manager secret | same — `aws secretsmanager put-secret-value` |
| Verify CheckMacValue locally | a tiny `python3` `hashlib` script (Rule 2) | same script |

**ECPay credentials live in `flight/ecpay` (Secrets Manager), never in code or the front-end.** There is **no `stripe listen` equivalent** — ECPay can only POST to a *publicly reachable* URL, so you test against the deployed API Gateway (Rule 8), not localhost.

---

## Hard rules

### Rule 1 — The callbacks are the source of truth for `active` — never flip status from the OrderResultURL page

> **The rule:** The `flight-ecpay-return` (ReturnURL) and `flight-ecpay-period` (PeriodReturnURL) Lambdas are the **only** things that set `subscription_status = active` (or `expired`) on a `subscriptions` row. The `OrderResultURL` browser-redirect page (`/account?purchase=success`) is UX-only — it can say "thanks, you're subscribed" but must never call any API that activates the subscription.

**Why:** Three failure modes the callback handles and the redirect can't:
1. **User closes the browser after paying.** ECPay still POSTs the `ReturnURL` (server-to-server); the callback still activates them. If activation lived on the success page, they'd be charged but never `active`, so they'd never get emails. (This is exactly the My Site dual-callback finding — sometimes the S2S `ReturnURL` wins, sometimes the browser does; only the S2S one is guaranteed.)
2. **Network blip on the redirect.** Same shape — charge succeeded, redirect failed, never activated.
3. **Client-side activation is forgeable.** Anyone could hit a "mark me active" endpoint and get the paid product for free. Trusting the redirect = free subscriptions.

**How to apply:**
- The `OrderResultURL` page shows a static "subscription confirmed" message (optionally `GET`s the row to display status, read-only).
- The only `UpdateItem` that writes `active` lives in the callback Lambdas; `/cancel` writes `cancelled` (grace); the parser writes `expired` (grace lapsed / 6-strikes). See Rules 9, 10, 12.
- The callbacks authenticate via their IAM role + the **CheckMacValue** on the payload (ECPay is not a logged-in user) — no Supabase credential.

---

### Rule 2 — Verify the CheckMacValue on EVERY callback — and KEEP empty-string fields in the hash

> **The rule:** Every ECPay callback carries a `CheckMacValue`. Recompute it over the returned fields and compare before trusting anything. When building the string to hash, **drop only `CheckMacValue` itself and truly-absent keys — keep fields whose value is the empty string** (e.g. `CustomField3=`, `CustomField4=`). ECPay includes those empty fields when it computes the MAC; if you filter them out you hash a different string and **every real callback fails verification**.

**Why:** This is the single most common ECPay bug, and it's *silent* — `CheckMacValue` fails, the Lambda 400s or skips, and the symptom looks like "ECPay isn't sending the callback" when really you're rejecting a valid one. (In My Site this exact bug — filtering `v === ''` — made *every* real callback fail; the fix was to keep empty strings and drop only `CheckMacValue` + truly-undefined keys.)

**The CMV algorithm (SHA256, AIO).** Use the **official `ecpayUrlEncode`** (it matches `UrlService::ecpayUrlEncode` in ECPay's PHP SDK) — **`~` MUST become `%7E`**, which Python's `quote_plus` does **not** do for you (`~` is "unreserved", left literal). An earlier compact helper had `("%7e","%7e")` which is a no-op that never encoded `~` → CMV mismatches on any value containing `~`. The verified version (passes **all** known-answer vectors in `.claude/skills/ecpay/test-vectors/checkmacvalue.json`):
```
1. Drop CheckMacValue; KEEP empty-string fields, drop only truly-absent keys.
2. Sort the remaining keys case-insensitively (A–Z).
3. Join: HashKey={hashKey}&{k1}={v1}&{k2}={v2}&...&HashIV={hashIV}
4. ecpayUrlEncode: quote_plus → replace ~→%7E → lowercase → restore - _ . ! * ( )
5. sha256 hex of that string.  6. UPPERCASE the hex → the CheckMacValue.
```
```python
import hashlib, urllib.parse
def ecpay_url_encode(s: str) -> str:
    e = urllib.parse.quote_plus(str(s)).replace("~", "%7E")  # quote_plus leaves ~ literal — fix it
    e = e.lower()                                             # lowercase FIRST (so %7E→%7e)
    for o, n in (("%2d","-"),("%5f","_"),("%2e","."),("%21","!"),
                 ("%2a","*"),("%28","("),("%29",")")):        # .NET ↔ PHP restorations
        e = e.replace(o, n)
    return e
def gen_cmv(params: dict, hash_key: str, hash_iv: str) -> str:
    items = {k: v for k, v in params.items() if k != "CheckMacValue"}  # KEEP "" values
    body = "&".join(f"{k}={items[k]}" for k in sorted(items, key=str.lower))
    raw = f"HashKey={hash_key}&{body}&HashIV={hash_iv}"
    return hashlib.sha256(ecpay_url_encode(raw).encode()).hexdigest().upper()
def verify_cmv(params, hash_key, hash_iv) -> bool:
    return params.get("CheckMacValue","").upper() == gen_cmv(params, hash_key, hash_iv)
```
**How to apply:** verify in **both** callback Lambdas (factor into one shared helper). **Before shipping, run your helper against `.claude/skills/ecpay/test-vectors/checkmacvalue.json`** (the official known-answer set, incl. the `~` and `'` cases) — if those pass, your CMV is correct. If a live callback fails, log the recomputed-vs-received MAC and return `0|CheckMacValueInvalid` (HTTP 400) — but first re-check you didn't drop empty fields. (See [[aws-best-practice]] for the same incident from the AWS/body-handling side, and the official **[[ecpay]]** skill for the reference implementation in 5 languages.)

---

### Rule 3 — Reply with the plain-text string `1|OK` (HTTP 200) — anything else makes ECPay resend 4×

> **The rule:** A successfully-processed callback must respond with the exact body `1|OK`, content-type `text/plain`, HTTP 200. On a permanent failure reply `0|<reason>`. Never return JSON, HTML, quotes, or even a bare `OK`.

**Why:** ECPay's contract is byte-literal: it looks for `1|OK`. Any other body (including `"1|OK"` with quotes, or an API-Gateway-default JSON wrapper) is read as "not acknowledged," so ECPay **resends the callback up to 4 more times, every ~5–15 minutes**. The first delivery says `RtnMsg=交易成功`; resends say `RtnMsg=paid` (English) — a useful tell that your first handling failed. With idempotency (Rule 4) the resends are harmless no-ops, but a handler that *never* returns `1|OK` will get retried forever and then abandoned.

**How to apply:**
```python
def _text(body, status=200):
    return {"statusCode": status, "headers": {"Content-Type": "text/plain; charset=utf-8"}, "body": body}
# success / already-processed:  return _text("1|OK")
# permanent failure:            return _text("0|CheckMacValueInvalid", 400)
```
Do the `UpdateItem` + SQS `SendMessage`, then return `1|OK`. Keep it fast — ECPay (like Stripe) doesn't want you doing slow work before acking.

---

### Rule 4 — Make the callbacks idempotent — ECPay WILL resend; activating twice must be a no-op

> **The rule:** `UpdateItem`-ing a row to `active` must be safe to run more than once, AND the once-only side-effect (the welcome email) must be guarded. Key idempotency on **`MerchantTradeNo` + a status check**, NOT on `gwsr`. Always return `1|OK` for an event you've already processed.

**Why:** ECPay resends each callback up to 4× (Rule 3), **and** in this course *two* callbacks can both run authorization logic — the S2S `ReturnURL` and (separately) any browser path — so the same charge can hit you more than once. Setting `subscription_status = active` twice is harmless; **sending a welcome email twice is not.**

> **⚠️ `gwsr` came back EMPTY on the real 定期定額 first-period `ReturnURL` (verified live this session).** Field naming/case on the recurring callback differs from one-time AIO, and `Gwsr` was blank. **Idempotency keyed on `gwsr` alone would be weak/broken.** Key on `MerchantTradeNo` + "is the row already `active`?" instead. More generally: **capture a real callback payload before relying on any specific field name** — don't assume the one-time-AIO field set.

**How to apply:**
- The status write is naturally idempotent (`SET subscription_status = :active`). Good.
- Before processing, check by **`MerchantTradeNo`** whether you've already activated this order (e.g. the row is already `active` for that trade-no). If so, skip the writes but **still return `1|OK`**. (Store `merchant_trade_no` on the row at subscribe time — Rule 9 needs it for cancel anyway.)
- The **welcome/cancel email** is the once-only action — decouple it: the producer (callback or cancel Lambda) **enqueues** a message stamped with `event_type` (`"welcome"`/`"cancel"`) to the **status SQS queue**, and the **single** `flight-status-notification` consumer branches on `event_type` and guards on whether it's already greeted this subscription. Don't email inline, and don't build two notification Lambdas — one consumer, routed by the field.

---

### Rule 5 — Carry `email` + `route` in `CustomField1/2` — set them server-side, and key the write off them

> **The rule:** `save_subscription` builds the checkout form with `CustomField1=<email>`, `CustomField2=<route>` (set from the **authenticated** Supabase session, never from a client-supplied body). ECPay echoes them back on every callback; the callback reads them to know **which row** `(email, route)` to `UpdateItem`. Never identify the subscriber by a payer/billing email from the payload.

**Why:** ECPay's `CustomField1..4` are the equivalent of Stripe metadata — the only reliable link from a payment back to your `(email, route)` DynamoDB key. Keying off the cardholder's email instead breaks when the billing email differs from the login email, and is forgeable. Because *your* server sets the CustomFields when the user is authenticated, they're trustworthy.

**How to apply:**
```
# in the checkout form (save_subscription):
CustomField1 = email          # from Supabase session
CustomField2 = route          # e.g. "TPE-TYO"
# in the callback:
email = params["CustomField1"]; route = params["CustomField2"]
ddb.update_item(Key={"email": email, "route": route}, ... status=active)
```
ECPay always returns CustomFields as strings, and **echoes all four even when unset** (`CustomField3=&CustomField4=`) — which is exactly why Rule 2's keep-empty-fields matters.

---

### Rule 6 — `PeriodAmount` must equal `TotalAmount`; `ExecTimes` is a COUNT, not "infinite"

> **The rule:** For 信用卡定期定額 set `ChoosePayment=Credit`, `PeriodType=M`, `Frequency=1`, and `TotalAmount == PeriodAmount` (ECPay requires the first-charge amount to equal the fixed recurring amount). `ExecTimes` is the **total number of charges** — there is no true "until cancelled." Use a large value (`ExecTimes=999`, the monthly max) for an effectively-long-term subscription.

**Why:** This is the biggest *conceptual* gap from Stripe. A Stripe subscription renews forever until cancelled; ECPay 定期定額 runs a **bounded count** of charges (`D`/`M`: max 999, `Y`: max 99) and then stops on its own. If you set `ExecTimes=12` thinking "monthly," the subscription silently ends after a year. And if `PeriodAmount != TotalAmount`, ECPay rejects the order at checkout.

**How to apply:**
- Monthly, effectively-indefinite: `PeriodType=M, Frequency=1, ExecTimes=999`.
- Teach students this is **not** infinite — at 999 months it ends; for a real long-running product you'd re-enroll before exhaustion (out of scope for the course, but say it).
- Keep `TotalAmount` and `PeriodAmount` in lockstep — both come from the single `amount` in `flight/ecpay`.

---

### Rule 7 — Guard `SimulatePaid` — the stage 模擬付款 button must NOT grant access

> **The rule:** ECPay's stage 廠商後台 (`vendor-stage.ecpay.com.tw`) has a「模擬付款」button that POSTs a fake successful result (`RtnCode=1`) **with `SimulatePaid=1`** to your `ReturnURL`/`PeriodReturnURL`. It's the fastest way to test a callback is reached — but the handler must verify the CMV, reply `1|OK`, and **NOT write `subscription_status=active`** when `SimulatePaid == "1"`. Either skip the activation write or record it to a separate test log.

**Why:** 模擬付款 is invaluable for proving "does ECPay reach my Lambda" without a real card — but if the handler activates on it, **anyone with stage-backoffice access (or a replayed payload) gets the paid product free.** (My Site flagged this as a must-fix-before-prod gap: the handler there activated on `RtnCode==1` without checking `SimulatePaid`.)

> **Where to log in (the part that trips everyone up):** the 模擬付款 button lives in the **stage** backoffice **`vendor-stage.ecpay.com.tw`** — you sign in with ECPay's **published shared test login** (a `stagetest*` account + test password from ECPay's 測試帳號 page), **NOT** with the MerchantID. **`3002607` is a MerchantID, not a login** — typing it into the 賣家帳號 field gives `帳號格式錯誤`. An order you paid via `3002607` shows up under this shared stage backoffice (mixed with other testers' orders), not in any private console — your real evidence is the callback in CloudWatch + the DynamoDB row.

**How to apply:**
```python
if params.get("SimulatePaid") == "1":
    # CMV already verified above; ack but don't grant
    return _text("1|OK")
# real payment → UpdateItem active ...
```
Use 模擬付款 freely in stage to confirm reachability; rely on a **real stage test-card** run to confirm the *activation* path.

---

### Rule 8 — There's no `stripe listen` — ECPay needs a PUBLIC URL; test against the deployed API

> **The rule:** ECPay can only POST callbacks to a publicly reachable `https://` URL on port 80/443. `localhost` / ngrok-with-a-weird-port won't receive `ReturnURL`/`PeriodReturnURL`. Test against the **deployed** API Gateway URL (or your M3 custom domain).

**Why:** Students coming from Stripe expect a local-forwarding tool; ECPay has none. A `ReturnURL` of `http://localhost:3000/...` simply never gets called, and the student waits forever wondering why the row never flips. ECPay also only honors **port 80/443** — an API-Gateway URL is fine; a `:3300` dev port is not.

**How to apply:**
- Deploy first, then test. Use **模擬付款** (Rule 7) for a fast "is my Lambda reached" check, and a **real stage test-card** run for the full path.
- Watch `aws logs tail /aws/lambda/flight-ecpay-return --since 5m --region us-east-1` for CMV-verified + `UpdateItem`.
- If 模擬付款 in the backoffice errors, the usual causes are: ReturnURL not public, a firewall blocking ECPay's source IP, or the handler replying something other than `1|OK`.

> **No-card backend verification — POST a validly-signed synthetic callback.** The real cashier payment needs a human (card + OTP), but the whole **callback → activation** path can be proven **without a card** by replaying a callback you sign yourself with the real `flight/ecpay` secret. This is how to verify B2/B3/cancel/grace **same-day** (mark the real-card run separately) — and it catches CMV / empty-field / idempotency bugs early. Build the form body exactly as ECPay would (`MerchantID`, `MerchantTradeNo` = a row's stored trade-no, `RtnCode=1`, `CustomField1=<email>`, `CustomField2=<route>`, **including the empty `CustomField3=&CustomField4=`**), compute `CheckMacValue` over it with `gen_cmv` (Rule 2), and POST it `application/x-www-form-urlencoded` to your deployed `…/ecpay-return`. Assert the row flips to `active`.
> - **Crucially keep the empty CustomFields in the signed body** — that's the exact shape Rule 2 protects; a synthetic callback that drops them won't catch the #1 bug (it tests the wrong string).
> - Do **not** set `SimulatePaid=1` (that path is correctly *not* activated, Rule 7) — a synthetic *real* callback is `RtnCode=1` without it.
> - This is a **backend** proof; it does not exercise the cashier UI or `OrderResultURL` (Rule 11). Still do one real stage test-card run before calling M2 done — note the synthetic check and the real-card check separately in the checklist.

---

### Rule 9 — Cancel is an API call you make (`CreditCardPeriodAction`), not an event you receive — and it grants a GRACE PERIOD, not instant expiry

> **The rule:** To stop a recurring subscription, your `/cancel` Lambda `POST`s to `…/Cashier/CreditCardPeriodAction` with `MerchantID`, the original `MerchantTradeNo`, `Action=Cancel`, `TimeStamp`, `CheckMacValue` — which stops **future renewals**. But the user **keeps service until the period they already paid for ends**, so cancel sets status **`cancelled`** (a transition state, NOT `expired`), preserving `current_period_end`. There is **no ECPay equivalent of `customer.subscription.deleted`** arriving on its own.

**Why:** ECPay doesn't push an unsolicited "cancelled" callback — **you** initiate the cancel and **you** flip the status. And flipping straight to `expired` is *wrong product behavior*: the customer paid through the end of the current period, so cutting alerts off the instant they cancel cheats them. Cancel = "don't renew," not "revoke now."

**How to apply (the cancellation-grace lifecycle — the single biggest thing the naive design gets wrong):**
- Store `merchant_trade_no` on the `subscriptions` row at subscribe time (you need it to cancel).
- **Track `current_period_end`** (a sortable ISO timestamp; keep a human `current_period_end_date` too): **set it on the first charge** (`flight-ecpay-return`) and **refresh it on every renewal** (`flight-ecpay-period`) — each successful charge extends the paid-through date by one period.
  > **⚠️ The grace check (`current_period_end >= now`) is a *lexicographic string compare*, not a datetime compare** — DynamoDB stores the string and the parser compares strings. So **every** writer (`flight-ecpay-return`, `flight-ecpay-period`, the cancel fallback) and the parser **must use the identical fixed-width UTC format** — standardize on **`%Y-%m-%dT%H:%M:%SZ`** (e.g. `2026-07-21T03:00:00Z`). A `+00:00` offset vs a `Z` suffix, or a non-zero-padded field, sorts wrong as a string and **silently breaks the grace math** (a paying user cut off early, or a lapsed one alerted forever) even though the instants are equal. Compute `now` the same way (`datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")`).
- `/cancel` Lambda: call ECPay `Action=Cancel`, then `UpdateItem` status → **`cancelled`** (keep `current_period_end`), enqueue the cancel email. Do **not** set `expired` here.
- **The parser gate must serve `active` AND `cancelled`-within-period rows**, and **lazily flip `cancelled` → `expired`** once `current_period_end` has passed (the parser is the natural place to do this since it scans the rows anyway — see Rule 12).
- **A `cancelled`-in-grace subscriber can still update their target price** in place — no re-payment, status stays `cancelled`. (Their existing watch is still live until the period ends.)
- **Migration edge case (will bite you):** rows activated *before* you added period-tracking have **no `current_period_end`** — on cancel, **fall back to `now + 1 month`** so the parser doesn't expire them on its very next run. Backfill any pre-existing `active` rows with a `current_period_end` too.
- There's **no Stripe Customer Portal** — the self-service 退訂 button on `/account` calls *your* `/cancel` route.
- **`ReAuth` (re-authorize a failed charge) cannot be tested on the stage merchant** — only `Cancel` is testable on stage. Don't build the course around verifying `ReAuth` end-to-end.
- **Stage cancel of a never-paid order returns `90100150 不存在的訂單編號`** — expected for a synthetic `MerchantTradeNo` that never entered the scheduler. The `/cancel` Lambda should **log it and still cancel/expire locally** (which is the correct, idempotent behavior).

**Subscription lifecycle (the full state machine):**
```
pending_payment ──(first charge ReturnURL)──▶ active ⇄ (target-price updates, in place)
       ▲                                        │
       │ (re-subscribe + pay)                   │ /cancel  (ECPay Action=Cancel; keep current_period_end)
       │                                        ▼
   expired ◀──(parser: current_period_end passed)── cancelled (grace — still alerted, can update target)
       ▲                                        ▲
       └──(6 consecutive failed renewals — Rule 10)┘
```

---

### Rule 10 — Set `expired` on ECPay's 6-strikes termination, not the first failed charge — and use the query API as the safety net

> **The rule:** A failed monthly charge does **not** mean "cancel the subscription." ECPay auto-retries: failures **1–3** → retry every 3–5 days (monitor, don't act); **4–5** → longer-interval retry (warn the customer); **6th consecutive failure → ECPay auto-terminates the contract.** Only flip the row to `expired` when ECPay signals the series has actually ended, not on a single `RtnCode != 1` on `PeriodReturnURL`. And because `PeriodReturnURL` notifies **only once per cycle**, when you miss one, **don't guess — query `QueryCreditCardPeriodInfo`** for the real authorization state.

**Why:** Treating the first failed renewal as `expired` cuts off a paying customer whose card merely had a transient decline that ECPay will successfully retry days later. Conversely, never reacting means a genuinely-dead card keeps a row `active` forever. The 6-strikes rule is ECPay's actual lifecycle; mirror it. And the "notify only once" guarantee means a dropped/4xx'd period callback can leave you out of sync — the query API is the authoritative reconciliation.

**How to apply:**
- `flight-ecpay-period` on `RtnCode == "1"` → keep `active` (optionally record `last_charged_at`, `TotalSuccessTimes`). On `RtnCode != "1"` → **log a failed-attempt counter, don't expire yet**; optionally email the customer to update their card around attempt #3.
- Treat the series as ended (→ `expired`) when ECPay's payload/Query indicates termination (the 6th failure auto-cancels), or when *you* called `Cancel` (Rule 9).
- **Reconciliation Lambda / on-demand check:** `POST …/Cashier/QueryCreditCardPeriodInfo` with `{MerchantID, MerchantTradeNo, TimeStamp}` + CMV → returns the order's executed/successful counts and per-charge records. Use it to recover a missed `PeriodReturnURL` and to drive `expired` decisions. (Deep field reference: the official **[[ecpay]]** skill, `guides/01-payment-aio.md` 定期定額 section + `QueryPeridicTrade.php`.)

---

### Rule 11 — `OrderResultURL` is a browser **POST** — point it at a redirect Lambda, NEVER at the static SPA (else a 405 right after payment)

> **The rule:** `OrderResultURL` (the front-end return URL) is delivered by ECPay as an **auto-submit POST**, not a GET. A static SPA host (Vercel/Netlify/S3) only serves **GET** on a page route, so a POST to `<site>/account?purchase=success` returns **HTTP 405 "This page isn't working"** — the user sees an error the instant after they pay. Point `OrderResultURL` at a tiny **POST-capable endpoint that `302`-redirects** to the SPA.

**Why (verified live this session):** the payment itself **still succeeds** — the S2S `ReturnURL` is what activates the row (Rule 1), so this is purely a *UX* bug, not a payment bug. But "I paid and got an error page" destroys trust. ECPay POSTs `OrderResultURL`; a static host has no POST handler for an app route; 405. (This is *why* Rule 1 matters — activation never depended on the browser landing.)

**How to apply:**
- Add a small Lambda (`flight-ecpay-result`) behind **`ANY /ecpay-result`** that returns `302 Location: https://<site>/app?purchase=success` (read `RtnCode` from the POST body if you want success/fail branching). It does **no** auth work — activation is the `ReturnURL`'s job.
- Set `OrderResultURL=<api>/ecpay-result` in the checkout form (NOT the SPA page).
- Symptom to recognize: "I get a 405 / 'This page isn't working' right after paying, but my row still went `active`." → `OrderResultURL` points at the static SPA; add the redirect Lambda.

---

### Rule 12 — The parser gate serves `active` AND `cancelled`-in-grace, and is where `cancelled → expired` happens lazily

> **The rule:** The paywall gate in the parser is **not** simply `subscription_status = active`. It must alert **`active` OR (`cancelled` AND `current_period_end` not yet passed)** — and, since the parser already scans every row, it's the natural place to **lazily flip `cancelled` → `expired`** once the paid-through date passes.

**Why:** Cancellation grants a grace period (Rule 9), so a `cancelled`-in-grace subscriber is still a paying customer until their period ends — they must keep getting alerts. And nobody pushes a "now expired" event when the grace period lapses; the parser noticing `current_period_end < now` on its next scan is what actually retires the row. A naive `status == "active"` gate cuts off grace-period users immediately (wrong) and never expires `cancelled` rows (they'd be alerted forever).

**How to apply:**
- Parser scan/filter: include a row if `subscription_status == "active"`, **or** `subscription_status == "cancelled"` and `current_period_end >= now`.
- When the parser sees a `cancelled` row whose `current_period_end < now`, `UpdateItem` it → `expired` (and stop alerting). `pending_payment`/`expired` are never alerted.
- This composes with Rule 10 (6-strikes failure → `expired`) — both are "the parser/period-handler retires the row," just on different triggers.

---

## What ECPay IS / is NOT in M2

| IS (M2) | is NOT (M2) |
|---|---|
| **信用卡定期定額** (monthly recurring), `ChoosePayment=Credit` | One-time AIO payment / 分期 / ATM / CVS |
| Stage test merchant — test card `4311-9522-2222-2222`, OTP `1234` | Real charges — needs a real MerchantID + 定期定額 enabled ([[ecpay-go-live]], M3) |
| An **AIO auto-submit HTML form** (you return HTML) | An SDK call returning a JSON checkout URL (that was Stripe) |
| **Two callbacks** (ReturnURL + PeriodReturnURL), verified by **CheckMacValue** | One webhook with a signature header |
| Cancel via **`CreditCardPeriodAction Action=Cancel`** (you call it) | A self-arriving cancel event / a Customer Portal |
| The callbacks flip a **DynamoDB `subscription_status`** | A credits ledger / Supabase tables |
| **stdlib only** (`hashlib` for CMV, `urllib` for the cancel POST) | A `stripe`+`requests` Lambda layer (not needed) |
| TWD whole-number amounts (`TotalAmount=150` = NT$150) | A cents/×100 amount (that was the Stripe-TWD trap) |

---

## Things to actively watch out for

1. **Filtering empty-string fields before hashing** → kills CheckMacValue verification (Rule 2). When a callback "never arrives" but ECPay's backoffice shows the order paid, suspect this first — you're rejecting a valid callback.
2. **Only building the ReturnURL handler** → the first charge activates, but every monthly renewal (`PeriodReturnURL`) silently does nothing (Rule 1/Rule 9 region). Build both.
3. **Replying anything but `1|OK`** → ECPay resends 4× (Rule 3). A JSON/HTML body or quotes counts as "not acked."
4. **Callback errors live in CloudWatch**, not the browser — `aws logs tail /aws/lambda/flight-ecpay-return --follow …`. ECPay's S2S POST never touches DevTools.
5. **`RtnCode` is a STRING `"1"`** in the form-POST callback, not the integer `1` — `if params["RtnCode"] != "1":` (the AES-JSON invoice/logistics APIs use an integer; this AIO callback does not).
6. **`SimulatePaid=1` must not grant** (Rule 7) — verify CMV, reply `1|OK`, don't activate.
7. **Testing with NT$1** → ECPay silently hides credit-card payment below the card minimum (~NT$6–11); the cashier shows only wallets and the student thinks the card "disappeared." Use a realistic amount (≥ ~NT$30 for stage testing).
8. **`NEXT_PUBLIC`-style trailing newline in a URL env** → a `\n` in `ReturnURL`/`OrderResultURL` gets signed into the CMV and ECPay's recomputed MAC differs → CMV Error. `.strip()` any URL you build from an env var.
9. **Different services have different HashKey/HashIV** — 金流, 電子發票, and 物流 each have their own keypair in the backoffice. M2 only uses 金流; don't mix in an invoice keypair.
10. **Don't do slow work before `1|OK`** — `UpdateItem` + SQS `SendMessage`, then ack; the status-SQS consumer does the actual emailing.
11. **`%26`/`%3C` in callback params need `urldecode` first** — ECPay's doc warns that any field value containing `%26`(`&`) or `%3C`(`<`) must be `urldecode`d before you use/verify it, or the call fails. `urllib.parse.parse_qs(..., keep_blank_values=True)` already decodes percent-escapes for you — just don't double-encode when recomputing the CMV.
12. **Monthly billing-day edge case** (`PeriodType=M`) — ECPay charges on the same day-of-month as the first charge; **if that day doesn't exist in a month (e.g. the 31st), it bills on the last day of that month**. Harmless for us (amount is fixed), but know it before a customer queries "why charged on the 28th." (Our daily-`D` test path sidesteps this entirely.)
13. **First auth must succeed or there's no schedule** — *「若第一次授權失敗,此訂單不會進入排程,需重新建立一筆訂單」*. A failed first charge yields **no** `PeriodReturnURL` ever; the row stays `pending_payment` and the user must re-subscribe. Don't expect renewals from an order whose first auth failed.
14. **`OrderResultURL` 405 ≠ payment failure** (Rule 11) — a static SPA returns 405 to ECPay's POST; the row still activated via `ReturnURL`. Add a `flight-ecpay-result` 302-redirect Lambda; never blame the payment.
15. **`gwsr` is empty on the recurring first-period callback** (Rule 4) — key idempotency on `MerchantTradeNo`, and capture a real payload before depending on any field name.
16. **Resend sandbox only delivers to the account owner** — welcome/cancel/fare emails from `onboarding@resend.dev` reach **only your own verified address**; any other recipient `403`s `validation_error`. Tests look broken but aren't — verify a sending domain at go-live ([[resend-best-practice]]). Tie this to M3.

---

## Out of scope for M2 (deferred)

- **Real (prod) merchant + live charging** — M3 / [[ecpay-go-live]] (apply for a MerchantID, confirm 定期定額 is enabled, switch to `payment.ecpay.com.tw`).
- **電子發票 (e-invoice)** — a separate ECPay service with its own keypair; add `InvoiceMark=Y` later if needed.
- **ATM / CVS / 超商 / 分期** — M2 is credit-card recurring only.
- **退款 (refund)** — handled in the ECPay backoffice or a later `DoAction` integration; not in M2.
- **Apple Pay / wallets** — `ChoosePayment=Credit` only for the recurring flow.

---

## Cross-references

- [[m2-ecpay-subscription]] — where the recurring-checkout form (in `save_subscription`) + the two callback Lambdas + the cancel Lambda are written.
- [[m2-ecpay-subscription-prerequisites]] — ECPay account + the `flight/ecpay` secret + the stage test merchant.
- [[m2-ecpay-subscription-checklist]] — verifies CMV handling, the two callbacks, idempotency, and the `active` gate.
- [[aws-best-practice]] — the form-body/`isBase64Encoded` handling from the AWS side; where `flight/ecpay` lives; why no layer is needed.
- [[supabase-best-practice]] — ECPay never touches Supabase here (auth-only); the join key is `email`.
- [[ecpay-go-live]] — applying for a real MerchantID + switching to prod in M3.
- ECPay 信用卡定期定額: https://developers.ecpay.com.tw/?p=2868 · 定期定額結果通知: https://developers.ecpay.com.tw/?p=5631 · 定期定額訂單作業: https://developers.ecpay.com.tw/?p=2900 · CheckMacValue: https://developers.ecpay.com.tw/?p=2902
