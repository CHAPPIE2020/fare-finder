---
name: resend-best-practice
description: Hard rules and operational SOP for sending transactional email via Resend in the Flight Price Notifier course — the price-drop alert (M1.3) and the welcome/cancel email (M2), sent from an AWS Lambda by a plain REST POST to api.resend.com (no SDK). Covers the onboarding@resend.dev→verified-domain switch (M3), the "demo sender only reaches your own account email" trap, dedup-before-send, deliverability (SPF/DKIM, text+html), idempotency, rate limits, and the no-VPC requirement. Use whenever a student wires the flight-fare-notification / status_notification Lambda, debugging "the email didn't arrive / went to spam / sent twice", or hitting a 401/403/422/429 from Resend. Sourced from this stack's real failure modes.
---

# Resend Best Practice (Flight Price Notifier — the alert email)

Battle-tested rules for sending email through **Resend** — the transactional-email provider this course uses for the **M1.3 price-drop alert** and the **M2 welcome/cancel** email. The send happens **inside an AWS Lambda** as a plain **`POST https://api.resend.com/emails`** (no SDK — keeps the Lambda layer pure-Python). Students hit these in the first hour of M1.3, usually around "the test send worked but the real one didn't" or "it went to spam."

When guiding a student through any Resend-touching code, **apply these rules proactively** — stop them before they break one.

> **Why Resend at all (not SMS):** US SMS needs **A2P 10DLC** carrier registration (10–15 days + a paid account) — impossible for a live course. Resend sends to any inbox **immediately** with just an API key. (Twilio SMS is a documented later upgrade.)

> **Model note (differs from a source Resend project):** a sibling project used Resend the **Next.js** way — the `resend` npm SDK + a `lib/resend.ts` lazy client + Audiences/Broadcasts + an unsubscribe route. This course is **AWS Lambda + a plain REST POST + a single transactional alert** (no SDK, no audiences, no broadcasts, no unsubscribe flow). The deliverability principles (verified `from`, text+html, demo-sender-only-to-self) carry over; the SDK/Next.js/MCP plumbing does **not** and is dropped.

This is the email-layer sibling of [[aws-best-practice]] (the Lambda/IAM/secrets/no-VPC side of the same send) and [[m1-flight-price-checker]] (Part 1.3, where the send is wired). The `from`-domain story finishes in [[m3-domain]].

---

## Execution mode: Cowork vs CLI

Resend is a hosted REST API; the calling code (the Lambda) runs identically in both. The deltas are how you do the one-off test send and how you store the key.

| Operation | CLI mode | Cowork mode |
|---|---|---|
| One-off test send | `curl -X POST https://api.resend.com/emails -H "Authorization: Bearer re_…" …`, or the `resend` CLI | **NOT** a sandbox `curl`/web-fetch (POST is proxy-blocked — Rule 0). Send from a Lambda (`flight-resend-test`, invoked via the AWS MCP), or click **Resend dashboard → Emails → Send** |
| Store the API key + `from` | into the **`flight/resend`** Secrets Manager secret | same — `aws secretsmanager create-secret` / `put-secret-value` |
| Verify a sending domain (M3) | Resend dashboard → Domains (+ DNS at registrar) | identical (dashboard + registrar) |
| Read delivery status | Resend dashboard → Emails (per-message log) | identical (dashboard) |

**The Resend API key lives in `flight/resend` (Secrets Manager), never in code or the front-end** — `{"api_key":"re_…","from":"onboarding@resend.dev"}`. The browser never sees it; only the Lambda reads it (see [[aws-best-practice]] Rule 2). *(The CLI binary is `resend`, not `resend-cli`, even though the npm package is `resend-cli`.)*

---

## Hard rules

### Rule 0 — In Cowork you can't POST to `api.resend.com` from the sandbox — send only from a Lambda

> **The rule:** The Cowork bash/build sandbox **cannot reach `api.resend.com`** — the network proxy blocks outbound HTTPS to it (`403` on `CONNECT`, allowlist), and the agent's web-fetch tool is **GET-only** (it can't do an authenticated POST with a body). So **never try to verify or send Resend mail by a `curl`/POST in Cowork.** The only thing in your stack with real outbound internet is a **Lambda** — do every Resend POST from there.

**Why:** This is the same **two-host gap** that shapes the AWS deploy ([[aws-best-practice]] *Cowork execution constraints*): the sandbox has tools but no network to arbitrary hosts; the connectors (AWS MCP) have network but aren't a shell. A GET check (like the Travelpayouts token URL) sneaks through the agent's web-fetch — but Resend needs a **POST + `Authorization` header + body**, which web-fetch can't do and the sandbox can't route. Students hit a confusing `blocked-by-allowlist / 403 on CONNECT` and think their key is wrong; it isn't — the *path* is wrong.

**How to apply:**
- **Verify the key in the prereq** by deploying a tiny **`flight-resend-test`** Lambda (inline CFN, reads `flight/resend`, POSTs to Resend) and invoking it via the AWS MCP — the Lambda runs *inside AWS* and has internet. Read the result from its **logs** (`filter-log-events`) + your inbox, not the invoke output (the MCP can't read that file). See [[m1-flight-price-checker-prerequisites]] Part D.
- **The real alert** is sent the same way — from `flight-fare-notification`, a Lambda. That's why M1.3's design never sends from the browser or the sandbox.
- **Zero-code alternative for a pure key check:** the **Resend dashboard → Emails → Send** button (no network needed from your side at all).

---

### Rule 1 — `onboarding@resend.dev` only delivers to YOUR OWN Resend-account email — this trips the M1.3 test

> **The rule:** While `from` is the demo sender `onboarding@resend.dev`, Resend will only **deliver** to the email address registered on your Resend account. A send to any *other* address shows up as **accepted in the Resend log but never arrives**. So test M1.3 with a subscriber whose email **is your Resend-account email** — or verify your own domain (Rule 2) before testing with anyone else's.

**Why:** This is the single most confusing M1.3 failure: the Lambda logs `200 {"id": …}`, the Resend dashboard shows the message — and the inbox is empty, because the recipient wasn't the account owner. Students burn an hour suspecting the Lambda, the secret, the SQS wiring. It's none of those: the **demo sender is restricted to self-delivery**.

**How to apply:**
- For the M1.3 demo, seed the test subscriber's `email` to **your own Resend-account email** so the alert actually lands.
- The moment you want to email a *real, different* user, you must first verify a sending domain (Rule 2 / M3) and switch `from` off the demo sender.
- If a student says "Resend says delivered but I got nothing," check the **recipient vs. the account email** first.

---

### Rule 2 — The `from` address decides deliverability: `onboarding@resend.dev` until your domain is **Verified**

> **The rule:** Until you've verified your own sending domain (M3), the `from` **must** be `onboarding@resend.dev`. A custom `from` like `alerts@yourdomain.com` **rejects/spam-folders** until that domain shows **Verified** (SPF + DKIM) in Resend.

**Why:** Resend only lets you send `from` an address it has authorized — its demo domain or a domain you've proven you own via DNS. Setting `from: "alerts@yourdomain.com"` before the SPF/DKIM records are live gets the send **rejected** (`403`/`422`) or delivered straight to **spam** (the receiver can't verify SPF/DKIM). The symptom looks like a code bug; it's an unverified `from`.

**How to apply:**
- **M1.3 (sandbox):** `flight/resend` → `{"api_key":"re_…","from":"onboarding@resend.dev"}`.
- **M3 (go-live):** Resend → Domains → add `yourdomain.com` → add the SPF + DKIM (and DMARC) records at the registrar → wait for **Verified** (often ~minutes once DNS propagates) → only **then** `put-secret-value` to flip `from` to `alerts@yourdomain.com`. Don't flip the secret before Verified ([[m3-domain]] Step 3).
- An **ASCII or CJK display name** is fine on a verified domain: `"矽谷機票通知 <alerts@yourdomain.com>"`.
- The Lambda reads `from` from the secret — switching domains is a **secret update, not a redeploy**.

---

### Rule 3 — Dedup BEFORE you send — the `notification_history` check is what stops inbox-spam

> **The rule:** The `flight-fare-notification` Lambda must **query `notification_history` and decide whether to send BEFORE calling Resend**, then write the history row **after** a successful send. Order: query → decide → POST Resend → `PutItem` history. Never send first and dedup later.

**Why:** The parser runs every 30 minutes. A fare that sits below a user's target would email **every run** (48×/day) without the floor. The dedup rules (`NOTIFY_FLOOR_HOURS=24`, re-alert only if ≥`REALERT_PCT`% or ≥`REALERT_ABS_TWD` cheaper) live *around the send*, and SQS is **at-least-once** — the same match can be delivered more than once — so the history check also makes a **redelivery** safe. Send-then-record inverts this: a crash between send and record re-sends on the next delivery.

**How to apply:**
- Query `notification_history` (pk = `"{email}#{route}"`, newest first, limit 1) → apply the floor + re-alert thresholds → only then POST to Resend → on a **2xx**, `PutItem` the history row.
- A crash *after* send but *before* the history write re-sends once on redelivery — acceptable, far safer than the inverse. (See [[aws-best-practice]] Rule 5 — SQS visibility timeout ≥ Lambda timeout.)

---

### Rule 3a — Classify failures: RETRY transient (429/5xx), DROP permanent (403/422) — or you loop forever

> **The rule:** M1 has **no DLQ**, so a Lambda that raises on **every** non-2xx makes SQS redeliver that message **forever**. Distinguish: **transient** (`429` rate-limit, `5xx`) → raise/return non-success so SQS retries with backoff; **permanent** (`403`, `422` — including the demo-sender `403` to a non-account address, and malformed-payload `422`) → **log and DROP** (return success so SQS deletes it). It will never succeed on retry.

**Why:** A real incident: stale `test@example.com` messages left on the fare queue from M1.2 testing were undeliverable on the demo sender (`403`, permanent). When the event-source mapping was enabled they retried in a tight loop and **cascaded into a Resend `429`** — turning a few dead messages into a rate-limit outage for the live ones. "Let SQS redeliver on any failure" is only safe for *transient* failures.

**How to apply:**
- In the consumer, branch on the HTTP status: `429`/`5xx` → re-raise (redeliver); `403`/`422` → `print` the body + return normally (drop). Write `notification_history` **only** after a real 2xx.
- **Purge stale test messages** (`aws sqs purge-queue`) before enabling the mapping, and set a redrive policy with a small `maxReceiveCount` as a backstop even though full DLQs stay out of M1 scope (see [[aws-best-practice]] Rule 5).

---

### Rule 4 — Send a plain REST POST, not the SDK — keep the Lambda layer pure-Python, and send BOTH `html` and `text`

> **The rule:** Send via `POST https://api.resend.com/emails` with `Authorization: Bearer <api_key>` and a JSON body that includes **both `html` and `text`**. Use `requests` (in the shared layer) or stdlib `urllib` — **do not** add the `resend` PyPI SDK.

**Why:** The packaging strategy keeps the Lambda zip/layer **pure-Python** so a Mac-built artifact runs on Lambda's Linux ([[aws-best-practice]] Rule 6) — an SDK buys nothing for one POST and risks extra deps. And **html-only mail hurts deliverability**: many spam filters down-rank a message with no plain-text part. Always include a `text` fallback. Also keep the HTML **simple** — avoid `<table>`-heavy layouts and large images, which read as *marketing* (more likely to be filtered) for what is a *transactional* alert.

**How to apply:**
```python
import json, urllib.request, urllib.error
def send_email(api_key, frm, to, subject, html, text):
    body = {"from": frm, "to": to, "subject": subject, "html": html, "text": text}
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                 # Resend sits behind Cloudflare — the default `Python-urllib/3.x` UA gets a
                 # 403 with body `error code: 1010`. A custom User-Agent fixes it.
                 "User-Agent": "Mozilla/5.0 (compatible; flight-notifier/1.0)"},
        method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())     # {"id": "…"} on success
```
Pass both `html` and `text`. A **2xx with an `id`** = accepted (not "delivered" — Rule 8). On a non-2xx, **classify before you react** (Rule 3a): **`429`/`5xx`** = transient → don't write `notification_history`, let SQS redeliver; **`403`/`422`** = permanent → log + **drop** (don't loop).

---

### Rule 5 — Read `api_key` + `from` from `flight/resend` at runtime — never hardcode, never put it in the front-end (a sending-only key is enough here)

> **The rule:** The Lambda fetches `flight/resend` from Secrets Manager and reads `api_key` + `from`. The key never appears in code, in a committed `.env`, or anywhere the browser can see it. A **sending-only** key (Resend's "Sending access") is sufficient for this course.

**Why:** A Resend key can send mail as you — a leak means spam/phishing from your domain and a burned reputation. Committed keys are indexed by GitHub's secret scanner in seconds; a key in client JS is in every visitor's network tab. The browser has **no reason** to touch Resend — only the Lambda sends. *(A "full-access" key is only needed for Audiences/contacts writes — which this course doesn't use; a sending-only key would `401` on `contacts.*` but the course never calls those, so scope it down.)*

**How to apply:**
- `flight/resend` = `{"api_key":"re_…","from":"…"}`; the role's `secretsmanager:GetSecretValue` is scoped to `flight/*`.
- Create a **Sending-access** key in Resend (least privilege). Rotate it at course end (and immediately if it lands in a transcript/screen-share — a key briefly transits the chat on its way to `create-secret`).

---

### Rule 5a — After you change `flight/resend`, BUST the warm-container cache before testing (the "I updated the secret but it still 403s" trap)

> **The rule:** A Lambda that reads `flight/resend` **at container init and caches it** (the normal, efficient pattern) keeps the **old** `api_key`/`from` in any **warm** container. So right after `put-secret-value` — most commonly the M3 flip of `from` to a verified domain — the next invocation can still use the stale `from` and **`403 validation_error`** even though the secret is already correct. Force a cold start before you test.

**Why:** This is one of the most confusing M3 incidents: the secret in Secrets Manager is right, the domain is Verified, yet Resend keeps rejecting — because the running container never re-read the secret. It looks like the domain isn't verified or the secret didn't save; it's neither. Any config change recycles the container and forces a fresh `get_secret_value`.

**How to apply:**
```bash
# any env-var change forces a cold start → fresh secret read
aws lambda update-function-configuration --function-name flight-fare-notification \
  --environment "Variables={CACHE_BUST=$(date +%s)}" --region us-east-1
# repeat for flight-status-notification if it also reads flight/resend
```
- Do this **immediately after** every `flight/resend` `put-secret-value`, then test.
- (Alternatively, read the secret per-invocation instead of caching — but that adds a Secrets Manager call to every send; the cache-bust-on-change pattern is cheaper.)
- Symptom → fix map: "secret looks right + domain Verified, but still `403`" ⇒ stale warm container ⇒ cache-bust.

---

### Rule 6 — Lambdas are NOT in a VPC — or the call to `api.resend.com` hangs and times out

> **The rule:** Create the notification Lambdas with **no `--vpc-config`**. They need the public internet to reach `api.resend.com`.

**Why:** A VPC removes the Lambda's default internet route — the POST to `api.resend.com` **hangs until the Lambda times out** (no error, just a stall) unless you also add a NAT gateway or VPC endpoint. There's no reason to be in a VPC here. If a student copied a VPC config from elsewhere and Resend (or ECPay/Travelpayouts) calls start timing out, **the VPC is the first suspect.** (This is [[aws-best-practice]] Rule 7.)

**How to apply:** Don't pass `--vpc-config` to `create-function`. Outbound HTTPS to Resend then works with zero config.

---

### Rule 7 — Respect the rate limit (5 req/s) — one email per SQS message paces it; retry on `429`

> **The rule:** Resend's default send rate is **5 requests/second** (the live `429` body says *"You can only make 5 requests per second."*). Don't fire one synchronous POST per subscriber in a tight loop or you'll get **`429 Too Many Requests`**. The SQS decoupling already paces this (one message = one invocation = one send). A `429` is **transient** → retry (let SQS redeliver), don't drop (Rule 3a).

**Why:** At course scale (a few test subscribers) you won't hit it — but the design is built to scale, and a future burst (many subscribers, one big drop) is exactly when a naive per-subscriber loop trips the limit. The **fare SQS queue → per-message Lambda** model spreads sends over time, which is why the send lives in the consumer, not the parser.

**How to apply:**
- One email per SQS message (the current design) stays well under the limit.
- On a `429`, **don't** write the history row — return non-success so SQS redelivers with backoff.
- A genuine bulk blast later → `POST https://api.resend.com/emails/batch` (array up to 100), still from the Lambda.

---

### Rule 8 — A 2xx means "accepted," not "delivered" — verify in the inbox, debug in the Resend dashboard

> **The rule:** A `200` + `{"id": …}` means Resend **accepted** the message, not that it **landed**. To confirm M1.3 works, check the **real inbox** (and spam); to debug a missing email, read the **Resend dashboard → Emails** log (Delivered / Bounced / Complained), not just the Lambda logs.

**Why:** The Lambda log shows the POST returned 200 and stops there — but mail can still **bounce** (bad recipient), **spam-folder** (unverified `from`, Rule 2), be **dropped**, or simply not reach a non-account address on the demo sender (Rule 1). "The Lambda says it sent but I got nothing" is almost always a deliverability outcome visible only in Resend's per-message log, never in CloudWatch.

**How to apply:**
- **M1.3 verify** = a real alert lands in **your** inbox (subject 「✈️ 台北 → 東京 降價通知！…」, **NT$ headline + optional 約 US$**, 「立即訂購」 button) — check spam if missing.
- Debug order for a missing email: **recipient == account email?** (Rule 1) → Resend Email log (what happened?) → `from` domain verified? (Rule 2) → Lambda log (did it even POST, or did dedup correctly skip it? — Rule 3).

---

### Rule 9 — The `to` is the **subscriber's** email (the join key); the body comes from `email_render.py`

> **The rule:** `to` is the subscriber's email — the same `email` that is the DynamoDB `subscriptions` partition key and the Supabase auth identity. The HTML/text body is built by `flightproxy/email_render.py` from the queued fare; don't hand-assemble email strings in the handler.

**Why:** `email` is the one join key across Supabase auth ↔ DynamoDB ↔ the alert — sending to anything else (e.g. a card-billing email from the payment provider) mis-routes the alert. And the renderer already produces the correct bilingual card (**NT$ headline + optional 約 US$**, the 「立即訂購」 link), the subject, and the plain-text part; keeping the render logic in one place keeps it consistent with what the checklist verifies. Resend `to` accepts a single address or a list (≤50) — here it's the **one** subscriber.

**How to apply:**
- `to = message["email"]`, `subject = email_render.subject(...)`, `html = email_render.render_html(...)`, `text = email_render.render_text(...)`.
- Keep it a **per-subscriber** alert — don't BCC a list. The `from` field key is literally `from` (not `from_email`/`sender`) — a wrong key is a `422`.

---

## What Resend IS / is NOT in this course

| IS | is NOT |
|---|---|
| Transactional email — one alert per matched subscriber, via REST | A marketing/broadcast/newsletter blaster (no Audiences/Broadcasts) |
| Sent from a **Lambda** (`POST /emails`), key in `flight/resend` | Sent from the browser / front-end (the key would leak) |
| `onboarding@resend.dev` (self-delivery only) in M1.3 → **verified domain** in M3 | A custom `from` before SPF/DKIM verify (bounces / spam) |
| A plain REST POST with **html + text** (pure-Python layer) | The `resend` PyPI/npm SDK / a `lib/resend.ts` client |
| A **sending-only** key | A full-access key (needed only for contacts/audiences, unused here) |
| The channel chosen to **avoid A2P 10DLC** SMS delay | SMS/Twilio (a later upgrade) |

---

## Things to actively watch out for

1. **Demo sender only reaches your own account email** (Rule 1) → "delivered in the log, empty inbox." Seed the test subscriber as your Resend-account email.
2. **Custom `from` before the domain is Verified** → silent bounce / spam (Rule 2). Stay on `onboarding@resend.dev` until M3 shows Verified.
3. **Send-before-dedup** → inbox spam every 30 min (Rule 3). Query `notification_history` first; write it only after a 2xx.
4. **html-only body** → hurts deliverability; always include a `text` part, and avoid `<table>`/image-heavy "marketing-looking" HTML (Rule 4).
5. **Lambda in a VPC** → the POST to `api.resend.com` hangs to timeout with no clear error (Rule 6). Remove the VPC config.
6. **`429` under fan-out** → exceeded **5 req/s** (Rule 7). One-email-per-SQS-message paces it; retry on 429, don't drop. Watch for a `429` **cascade** from a backlog of permanent-failure messages looping (Rule 3a).
7. **`403` with body `error code: 1010`** → **Cloudflare** blocking the default `Python-urllib/3.x` User-Agent (Rule 4). Set a custom `User-Agent`. Looks like a bad key — it isn't.
8. **Looping forever on a permanent failure** → `403`/`422` redelivered with no DLQ (Rule 3a). Drop permanent, retry only transient; purge stale test messages before enabling the mapping.
9. **"200 but no email"** → check the **Resend dashboard Email log**, not CloudWatch (Rule 8) — it's a deliverability outcome.
10. **Wrong JSON key** → Resend expects `from`/`to`/`subject`/`html`/`text`; a wrong key (`from_email`, `sender`) is a `422`.
11. **`put-secret-value` replaces the whole value** — include **both** `api_key` and `from`, or you'll drop one (same wholesale-replace gotcha as every secret).
12. **CLI binary is `resend`** (not `resend-cli`) if a student uses it for the test send.

---

## Out of scope (deferred / not used)

- **Audiences / Broadcasts / contacts** — this is transactional, one-recipient mail; no `contacts.*` calls (so a sending-only key suffices, Rule 5).
- **Unsubscribe / preference links** — alerts are opt-in subscriptions managed in-app, not a mailing list.
- **Webhooks / open-&-click tracking** — the dashboard log suffices for the course.
- **The `resend` SDK / `lib/resend.ts` lazy client / Resend MCP** — those belong to the Next.js sibling project; here it's a Lambda REST POST.
- **Inbound / reply parsing, multiple regions, dedicated IPs** — not used at this scale.

---

## Cross-references

- [[m1-flight-price-checker]] — Part 1.3, where the send is wired (the `flight-fare-notification` consumer + `flight/resend`).
- [[m1-flight-price-checker-prerequisites]] — Part D: Resend account + sending key + the test-Lambda send.
- [[m1-flight-price-checker-checklist]] — verifies the alert lands and that dedup holds (Sections J–L).
- [[m3-domain]] — verifying your own sending domain (SPF/DKIM) and flipping the `from`.
- [[aws-best-practice]] — Rule 2 (secrets), Rule 5 (SQS visibility timeout), Rule 6 (pure-Python layer), Rule 7 (no VPC) — the AWS side of the same send.
- [[ecpay-best-practice]] — the M2 welcome/cancel email follows the same status-SQS pattern (one consumer routed by `event_type`).
- Resend send API: https://resend.com/docs/api-reference/emails/send-email · Batch: https://resend.com/docs/api-reference/emails/send-batch-emails · Domains: https://resend.com/docs/dashboard/domains/introduction
