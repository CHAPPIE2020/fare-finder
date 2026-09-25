---
name: m1-flight-price-checker-checklist
description: Flight Price Notifier Milestone 1 verification — the whole free notifier in one pass. Part 1.1 the DynamoDB subscriptions table + save_subscription Lambda + API Gateway + the subscribe form + subscribed-state; Part 1.2 the S3 routes + parser/wrapper Lambdas fetch dual-currency fares and enqueue matches to SQS; Part 1.3 the fare-notification Lambda sends a deduped Resend email. M1 has NO payment guard (no subscription_status — that's M2). Use when the student says "驗收 M1", "check M1", or after `m1-flight-price-checker` is built.
---

# M1 — Flight Price Checker Checklist (one shot)

Verify the whole free notifier end-to-end, then emit a single **READY for M2** verdict. Run after `m1-flight-price-checker` is built. You (Claude Code) run each check and report.

## Execution mode

Mainly **Cowork** — paste checks to the agent with the **AWS API MCP**; CLI runs the same `aws` lines. All commands `--region us-east-1`, `[default]` profile. MCP rules that bite here: **verify by effect** (can't `cat` an `invoke` output file); **`filter-log-events`**, not `logs tail`; **no JMESPath backtick literals** (use `SecretList[].Name`). Ask the student for: the API Gateway base URL, the live Vercel URL, and a real inbox = their **Resend-account email**.

## Flow being verified

```
 Product Site ─/subscribe─▶ Subscriptions [DynamoDB]      (Part 1.1: A,B,C,D,E)
 EventBridge ─▶ Parser Wrapper ─▶ Parser ─(Travelpayouts)─▶ match ─▶ [SQS]   (Part 1.2: F,G,H,I)
 [SQS] ─▶ Fare Notification λ ─(dedup: Notification History)─▶ Email [Resend] (Part 1.3: J,K,L)
```

---

## Part 1.1 — Subscribe

### Section A — DynamoDB tables
- **A1** `subscriptions` ACTIVE: `aws dynamodb describe-table --table-name subscriptions --region us-east-1 --query 'Table.{status:TableStatus,keys:KeySchema}'` → ACTIVE, HASH=email, RANGE=route.
- **A2** `notification_history` ACTIVE: same for `notification_history` → ACTIVE, HASH=pk, RANGE=sent_at (type **S** — used by 1.3 dedup).

### Section B — AWS plumbing
- **B1** Secrets present: `aws secretsmanager list-secrets --region us-east-1 --query "SecretList[].Name"` → `flight/travelpayouts`, `flight/resend` (+ the cache `flight/github`, `flight/supabase`). List-all-and-scan; no backtick filter.
- **B2** Role has DynamoDB: `aws iam get-role-policy --role-name flight-lambda-role --policy-name flight-data --query 'PolicyDocument.Statement[].Action'`.
- **B3** Lambda exists: `aws lambda get-function --function-name flight-save-subscription --region us-east-1 --query 'Configuration.FunctionName'`.

### Section C — save_subscription works
- **C1** Plain invoke (no `--query`/`--cli-binary-format`; don't `cat` the output): `aws lambda invoke --function-name flight-save-subscription --payload '{"body":"{\"email\":\"checklist@test.com\",\"plan_name\":\"tokyo\",\"target_price\":10000}"}' m1.json --region us-east-1`.
- **C2** The row landed (authoritative): `aws dynamodb get-item --table-name subscriptions --key '{"email":{"S":"checklist@test.com"},"route":{"S":"TPE-TYO"}}' --region us-east-1` → `route=TPE-TYO`, `currency=TWD`, `target_price` set, **no `subscription_status`** (that's M2).

### Section D — API Gateway + form (end-to-end)
- **D1** Route reachable — **mode-dependent:** CLI `curl -s -X POST "<api>/subscribe" -H "content-type: application/json" -d '{"email":"e2e@test.com","plan_name":"seoul","target_price":7000}'`; Cowork can't POST (no shell with AWS net, web tool is GET-only) → prove via C1/C2 + the live form (D2).
- **D2** The student submits the **real form** on the live Vercel site → a new row appears in DynamoDB (no `subscription_status`). **The decisive POST + CORS test.**
- **D3** CORS works — the browser `fetch` from the Vercel origin succeeds (no console CORS error).

### Section E — Subscribed state (closed loop)
- **E1** `GET <api>/subscriptions?email=e2e@test.com` returns the user's rows (a GET — testable from a browser / the Cowork web-fetch tool).
- **E2** On the live site, after subscribing + reloading, the card shows a **已訂閱 / Subscribed** badge + saved target + an **更新目標價 / Update** button (not write-only).

---

## Part 1.2 — Fetch on schedule

### Section F — Config + queue + deploy artifacts
- **F1** S3 routes load: `aws s3 cp s3://flight-config-<ACCOUNT_ID>/flight-routes.json - --region us-east-1` → the two routes.
- **F2** Fare queue exists: `aws sqs get-queue-url --queue-name flight-fare-queue --region us-east-1`.
- **F3** Zips in S3 with **ETag == local md5** (NOT just size — a same-length single-char corruption passes a size check): `aws s3 ls s3://flight-config-<ACCOUNT_ID>/lambda/ --region us-east-1` lists `parser.zip`+`wrapper.zip`; compare each `aws s3api head-object … --query "ETag"` to the local `md5`.

### Section G — Parser Lambdas run
- **G1** Both exist: `aws lambda get-function --function-name flight-parser …` and `flight-parser-wrapper`.
- **G2** Parser invoke clean — verify **by logs**, not the output file: `aws lambda invoke --function-name flight-parser --payload '{"origin":"TPE","destination":"TYO","route":"TPE-TYO"}' out.json --region us-east-1`. `Runtime.ImportModuleError` = bad S3 zip (re-seed, check ETag); `AccessDeniedException` = role merge dropped a statement.
- **G3** Realistic fares in logs: `aws logs filter-log-events --log-group-name /aws/lambda/flight-parser --query "events[].message" --region us-east-1` → a sane TWD fare (Tokyo ~NT$8–12k, Seoul ~NT$5–8k). Empty fares despite a live route = wrong API keys (`departure_at`/`return_at`, not `depart_date`/`return_date`).

### Section H — Match + enqueue (no payment guard)
- **H1** With a seeded subscriber whose `target_price` is ABOVE the live fare, invoking the parser **enqueues**: `aws sqs get-queue-attributes --queue-url <url> --attribute-names ApproximateNumberOfMessages --region us-east-1` → count > 0.
- **H2** **Body is real + dual-currency:** `aws sqs receive-message --queue-url <url> --max-number-of-messages 1 --visibility-timeout 3 --query "Messages[].Body" --region us-east-1` → has `email`, `route`, `cheapest` with **`currency:"TWD"`** (sane price/airline/ISO depart), and — when USD succeeded — a **`cheapest_usd`** block (`currency:"USD"`). Confirms the gate used **TWD vs target_price**. (Don't delete it — short visibility-timeout returns it.)
- **H3** **USD best-effort:** if the USD fetch fails, the message still ships **TWD-only** (`cheapest_usd` absent) — the parser omits it, never crashes.
- **H4** **No payment guard:** the matched subscriber has **no `subscription_status`** and is still matched.
- **H5** A subscriber whose `target_price` is BELOW the live fare is NOT enqueued (comparison direction correct).

### Section I — Schedule wired
- **I1** Rule enabled: `aws events describe-rule --name flight-price-check --region us-east-1 --query '{state:State,sched:ScheduleExpression}'` → `ENABLED`, `rate(30 minutes)`.
- **I2** Target is the wrapper: `aws events list-targets-by-rule --rule flight-price-check --region us-east-1 --query 'Targets[].Arn'`.
- **I3** Fires: invoke the wrapper manually (or wait one cycle) → both routes' parser logs appear.

---

## Part 1.3 — Email on target

### Section J — Resend + consumer wired
- **J1** Resend secret present: `aws secretsmanager describe-secret --secret-id flight/resend --region us-east-1 --query "Name"`.
- **J2** Consumer wired to the fare queue: `aws lambda list-event-source-mappings --function-name flight-fare-notification --region us-east-1 --query 'EventSourceMappings[].{src:EventSourceArn,state:State}'` → a mapping from `flight-fare-queue`, `Enabled`. Role has **all three** consumer SQS actions (Receive/Delete/**GetQueueAttributes**) — else the mapping fails closed silently.
- **J3** Dedup knobs set + a sane VisibilityTimeout: `aws lambda get-function-configuration --function-name flight-fare-notification --region us-east-1 --query 'Environment.Variables'` → `NOTIFY_FLOOR_HOURS`/`REALERT_PCT`/`REALERT_ABS_TWD`; queue `VisibilityTimeout` > the Lambda timeout (e.g. 180).

### Section K — Email fires (no payment guard)
- **K1** Seed a subscriber whose `email` is your **Resend-account** email (the demo sender only delivers there) with `target_price` ABOVE the live fare, **purge** stale queue messages, run the parser, then read the consumer logs: `aws logs filter-log-events --log-group-name /aws/lambda/flight-fare-notification --query "events[].message" --region us-east-1`.
- **K2** **Decisive:** the inbox receives the alert (subject 「✈️ 台北 → 東京 降價通知！NT$9,325 已達標」). The card shows the **NT$ headline** (+ a small **約 US$** line when the message had `cheapest_usd`), the target, and the 「立即訂購」 button.
- **K3** **No payment guard:** the subscriber was emailed despite **no `subscription_status`**.

### Section L — Dedup + re-alert
- **L1** Re-run the parser immediately → **no** second email (within `NOTIFY_FLOOR_HOURS`); logs say "skipped (deduped)".
- **L2** A history row was written: `aws dynamodb query --table-name notification_history --key-condition-expression 'pk = :p' --expression-attribute-values '{":p":{"S":"you@example.com#TPE-TYO"}}' --no-scan-index-forward --max-items 3 --region us-east-1` → recent `sent_at`+`price`.
- **L3** Seed a cheapest ≥20% OR ≥NT$2,000 below the last alerted price → a fresh email DOES go out (new history row).

---

## Reporting

| Check | Status | Notes |
|---|---|---|
| A1/A2 tables ACTIVE | ✅/❌ | subscriptions + notification_history |
| B1 secrets present | ✅/❌ | travelpayouts, resend, +cache |
| B2/B3 IAM + save Lambda | ✅/❌ | |
| C2 subscription row (no status) | ✅/❌ | authoritative |
| D2 form → row (live) | ✅/❌ | the key 1.1 one (POST+CORS) |
| E2 subscribed-state shows | ✅/❌ | closed loop |
| F1/F2/F3 routes+queue+zips (ETag) | ✅/❌ | verify ETag, not size |
| G2/G3 parser runs, realistic fares | ✅/❌ | filter-log-events |
| H1/H2 match → enqueued, body dual-currency | ✅/❌ | cheapest=TWD always, usd optional |
| H3/H4/H5 USD best-effort / no guard / below-target | ✅/❌ | |
| I1/I3 schedule enabled + fires | ✅/❌ | |
| J2 consumer wired (3 SQS perms) | ✅/❌ | mapping fails closed if any missing |
| J3 dedup env + VisibilityTimeout>timeout | ✅/❌ | |
| K2 email received (NT$ headline) | ✅/❌ | the key 1.3 one |
| K3 no payment guard | ✅/❌ | M1 design |
| L1 dedup blocks repeat | ✅/❌ | |
| L3 re-alert on big drop | ✅/❌ | |

**Verdict:**
- All ✅ → 「M1 驗收通過 ✅ 你有一個能動的*免費*降價通知器了。READY for M2。跟我說『啟動 M2』來加上付款門檻。」
- Any ❌ → name the failures + recovery:
  - **1.1:** CORS → set on the HTTP API; no row → Lambda `put_item` + IAM DynamoDB + Decimal; subscribed-state missing → add `GET /subscriptions` + the dashboard fetch.
  - **1.2:** `Runtime.ImportModuleError`/`InvalidZipFileException` → re-seed via `flight-seed`, verify **ETag==md5**; `State=Failed` → delete+recreate; empty fares → check `departure_at`/`return_at` keys + token + `CONFIG_BUCKET`; nothing enqueued → `Scan` filter (route only) + SQS SendMessage perm.
  - **1.3:** no email → recipient = **Resend-account** email + the 3 consumer SQS perms + custom User-Agent (Cloudflare 1010) + dedup logic; duplicate sent → dedup window / history write; permanent-failure loop (403/422) → drop, don't redeliver, purge stale messages.
  Then re-run `驗收 M1`. (See [[aws-best-practice]] + [[resend-best-practice]] for the failure modes.)
