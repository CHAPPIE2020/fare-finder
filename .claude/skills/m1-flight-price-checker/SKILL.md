---
name: m1-flight-price-checker
description: Flight Price Notifier Milestone 1 — build the whole free notifier end-to-end in one shot. Part 1 a signed-in user subscribes to a route (Tokyo/Seoul) + TWD target → DynamoDB; Part 2 an EventBridge-driven parser fetches the cheapest fare (TWD primary, USD supplementary) and enqueues matches to SQS; Part 3 a fare-notification Lambda dedups against notification_history and emails the subscriber via Resend. NO payment guard in M1 — a subscription row means "eligible for alerts" (subscription_status + the paywall arrive in M2). Supabase is auth-only. Use when the student says "啟動 M1", "start M1", "做機票降價通知器", "build the flight price checker", or the older "啟動 M1.1/M1.2/M1.3".
---

# M1 — Flight Price Checker（一次做出整個免費降價通知器）

Build the complete **free** notifier in one pass: **subscribe → fetch on a schedule → email on target**. It's structured in three parts (the old M1.1 / M1.2 / M1.3), but you do them back-to-back in a single session.

## What this skill builds

| Part | Goal | Key pieces |
|---|---|---|
| **1.1 Subscribe** | A signed-in user picks a plan (Tokyo/Seoul) + TWD target → a row in DynamoDB; the UI shows the subscribed state. | `subscriptions` table, `flight-save-subscription` + `flight-list-subscriptions` Lambdas, API Gateway, subscribe UI |
| **1.2 Fetch on schedule** | Every 30 min, fetch each route's cheapest fare (TWD + USD) and enqueue whoever's target is met. | S3 routes config, `flight-fare-queue` (SQS), `flight-parser-wrapper` + `flight-parser` Lambdas, EventBridge |
| **1.3 Email on target** | Turn a queued match into a real, de-duplicated email. | `flight-fare-notification` Lambda, `notification_history` dedup, Resend |

**End state:** pick a plan + budget on the live site → a row appears in DynamoDB (and the card shows you're subscribed) → the schedule fetches fares → when a watched route hits the target, a real **email** lands (NT$ headline + optional 約 US$, 「立即訂購」 button), with **no duplicate spam** and **no payment required**.

> **NO payment guard in M1.** A subscription row's mere existence = eligible for alerts. There is **no `subscription_status`** field anywhere in M1 — it, the payment-confirmation flow, and the `active`-only parser filter are all **introduced in M2** (the paywall). Don't add a status here.
>
> **Architecture invariants:** Supabase = **auth only** (the M0 login); all app data lives in **DynamoDB on AWS**. The join key across Supabase-auth / DynamoDB / the payment provider is the user's **email**. The browser holds **no AWS credentials** — it POSTs to API Gateway; only Lambdas touch AWS (via the shared IAM role). **Why email, not SMS:** US SMS needs A2P 10DLC carrier registration (10–15 days, paid) — Resend emails any inbox immediately.

## When to load this skill

- "啟動 M1" / "start M1" / "做機票降價通知器" / "build the flight price checker" (or the older "啟動 M1.1/M1.2/M1.3").

**Before Part 1.1, confirm `m0-landing-and-signin-checklist` is green and `m1-flight-price-checker-prerequisites` is complete** (project + AWS access via the `[default]` profile + the `flight/travelpayouts` and `flight/resend` secrets). If not, load those first.

## Execution mode: Cowork (default) vs CLI

This course runs mainly in **Cowork** — you talk to a Cowork agent with the **AWS API MCP** and paste the **`ask """ … """`** blocks below to it verbatim. On the local Claude CLI instead, run the equivalent `aws`/`zip` commands directly. Every AWS command uses `--region us-east-1`; the `[default]` profile means **no `--profile` flag**.

> **Read [[aws-best-practice]] *Cowork execution constraints* once before you deploy.** The connector is usually **`aws`-only** (no shell, no `zip`, no file authoring). Two deploy methods follow from that: **Method 1 — inline CFN `Code.ZipFile`** for a single file ≤4096 chars (Part 1.1's Lambdas); **Method 2 — the `flight-seed` base64→S3 bridge** for anything bigger (Part 1.2's parser, Part 1.3's notification Lambda). MCP quirks you'll hit: `logs tail` and `cloudformation wait` are **rejected** (use `filter-log-events` / poll `describe-stacks`); `lambda invoke --payload` is **raw, not base64**; you **can't `cat` an invoke's output file** (verify by effect); **JMESPath backtick literals** fail (use `SecretList[].Name`, not `SecretList[?starts_with(Name,\`flight/\`)]`).

## Full system (what you're building)

```
 SUPABASE (auth only) ─▶ Product Site [Vercel] ──POST /subscribe──┐
 ┌──── Flight Fare Checker ───────────────────────────────────────┼──────────┐   ┌──── Notification ──────────┐
 │                                                                 ▼          │   │  ┌──────────────────────┐  │
 │  ┌──────────┐  ┌─────────┐   ┌──────────────┐   ┌────────────────────────┐ │   │  │ Notification History │  │
 │  │  Event   │─▶│ Parser  │──▶│ Parser (×N)  │──▶│ Subscriptions [DynamoDB]│ │   │  │ [DynamoDB] (dedup)   │  │
 │  │  Bridge  │  │ Wrapper │   │   λ  λ  λ     │   └────────────────────────┘ │   │  └──────────┬───────────┘  │
 │  └──────────┘  └────┬────┘   └──────┬───────┘   ▲ scan (subscriber,target) │   │ ┌──────────┴───────────┐  │
 │     admin ✈ ─▶ Flight Routes [S3]   │ fetch cheapest (TWD + USD)           │   │ │ Flight Fare          │  │
 │                ┌────────────────┐   ▼  ┌──────────────────────┐            │   │ │ Notification  λ      │  │
 │                │ travelpayouts  │◀─────│ (per-route worker)   │            │   │ │  · 24h floor / ≥20%  │  │
 │                └────────────────┘      └──────────────────────┘            │   │ │  · render → Resend   │  │
 │  match → SQS flight-fare-queue {email,route,target_price,cheapest(TWD),cheapest_usd?} ─▶ │ └──────────┬───────────┘  │
 └─────────────────────────────────────────────────────────────────┬─────────┘   └────────────┼──────────────┘
                                                                    └──▶ [SQS] ────────────────┘  ▼
                                                                                          ┌────────────────┐
                                                                                          │ Email [Resend] │
                                                                                          └────────────────┘
 Legend:  ▮ orange = manual input (Flight Routes [S3])   ▮ teal = main component (λ)
          ▮ pink = user data (Subscriptions / Notification History [DynamoDB])   ▮ grey = SQS / 3rd-party / shared Lambda
 Repo loop:  Landing Page (Lovable) ──▶ Repo (GitHub) ──R──▶ Product Site (Vercel)
```

> **Deploy-time only (not in the runtime flow):** a tiny **`flight-seed` λ** exists solely to land the routes JSON + the >4096-char function zips in S3 (the `aws`-only connector can't upload objects otherwise). It plays no part once the system runs.

---

# Part 1.1 — Subscribe to a plan

### Step 1 — Create the DynamoDB tables

One table holds subscriptions (PK `email`, SK `route` — `TPE-TYO`/`TPE-SEL`, no dates). Create the **dedup/audit** table here too (Part 1.3 uses it). PAY_PER_REQUEST so there's no capacity to manage.

```bash
aws dynamodb create-table --table-name subscriptions \
  --attribute-definitions AttributeName=email,AttributeType=S AttributeName=route,AttributeType=S \
  --key-schema AttributeName=email,KeyType=HASH AttributeName=route,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST --region us-east-1

aws dynamodb create-table --table-name notification_history \
  --attribute-definitions AttributeName=pk,AttributeType=S AttributeName=sent_at,AttributeType=S \
  --key-schema AttributeName=pk,KeyType=HASH AttributeName=sent_at,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST --region us-east-1
```

**`subscriptions` row shape** (schemaless — written by the handlers): `email` (PK), `route` (SK, `TPE-TYO`|`TPE-SEL`), `plan_name` (`tokyo`|`seoul`), `origin` (`TPE`), `destination` (`TYO`|`SEL`), `target_price` (N, TWD), `currency` (`TWD`), `created_at`, `updated_at`. **No `subscription_status` or any payment-related fields in M1** (those arrive with the M2 paywall). **`notification_history`**: `pk` (S, `"{email}#{route}"`), `sent_at` (S, ISO-8601 UTC) — Part 1.3's dedup target.

**The two fixed plans** (seeded in the UI + known to the Lambda — no separate table):

| plan_name | 顯示名稱 | origin | destination | route |
|---|---|---|---|---|
| `tokyo` | 台北 ✈ 東京 | TPE | TYO | TPE-TYO |
| `seoul` | 台北 ✈ 首爾 | TPE | SEL | TPE-SEL |

**Verify:** `aws dynamodb describe-table --table-name subscriptions --region us-east-1 --query 'Table.{status:TableStatus,keys:KeySchema}'` → `ACTIVE`, HASH=email, RANGE=route. (And the same for `notification_history`.)

### Step 2 — Confirm the Travelpayouts secret

The `flight/travelpayouts` token was stored in the prereq. Confirm it's present (don't re-create):
```bash
aws secretsmanager list-secrets --region us-east-1 --query "SecretList[].Name"   # expect flight/travelpayouts (+ the cache flight/github, flight/supabase)
```
(The fetch API authenticates on the **token** alone — no `marker`. DynamoDB needs **no** secret; the Lambda reaches it via IAM. `flight/supabase` exists but the Lambda never reads it — it caches the front-end's url + publishable key for session recall; Supabase stays auth-only for data. See [[aws-best-practice]] Rule 2.)

### Step 3 — Create the shared Lambda IAM role

> Replace **`<ACCOUNT_ID>`** below — resolve once: `aws sts get-caller-identity --query Account --output text --region us-east-1`. (In Cowork: "use my account ID in the ARNs".)

```bash
aws iam create-role --role-name flight-lambda-role \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
aws iam attach-role-policy --role-name flight-lambda-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam put-role-policy --role-name flight-lambda-role --policy-name flight-data \
  --policy-document '{"Version":"2012-10-17","Statement":[
    {"Effect":"Allow","Action":"secretsmanager:GetSecretValue","Resource":"arn:aws:secretsmanager:us-east-1:<ACCOUNT_ID>:secret:flight/*"},
    {"Effect":"Allow","Action":["dynamodb:PutItem","dynamodb:UpdateItem","dynamodb:GetItem","dynamodb:Query","dynamodb:Scan"],"Resource":["arn:aws:dynamodb:us-east-1:<ACCOUNT_ID>:table/subscriptions","arn:aws:dynamodb:us-east-1:<ACCOUNT_ID>:table/notification_history"]}
  ]}'
```
This one shared role is reused by **all** of M1's Lambdas. **No SNS, no VPC.** Parts 1.2/1.3 *extend* this same `flight-data` policy (S3 + SQS + InvokeFunction) — you'll merge those in as you reach them. **Verify:** `aws iam get-role --role-name flight-lambda-role` succeeds.

### Step 4 — Write & deploy `flight-save-subscription` (inline CFN — Method 1)

The handler takes `{email, plan_name, target_price}`, maps `plan_name`→`(origin,destination)`, builds `route = origin-destination`, and `PutItem`s the row — **no `subscription_status` in M1**. boto3 is in the runtime (no layer).
```python
import boto3, json, time
from decimal import Decimal
PLANS = {"tokyo": {"origin":"TPE","destination":"TYO"}, "seoul": {"origin":"TPE","destination":"SEL"}}
ddb = boto3.resource("dynamodb").Table("subscriptions")
# validate plan_name in PLANS; target_price a positive number (TWD); route=f"{origin}-{destination}"
# ddb.put_item(Item={"email":…,"route":…,"plan_name":…,"origin":…,"destination":…,
#   "target_price": Decimal(str(target_price)),"currency":"TWD","created_at":…,"updated_at":…})  # NO subscription_status
```
Deploy as **inline CloudFormation** (one file, handler `index.handler`, ≤4096 chars):
```bash
aws cloudformation create-stack --stack-name flight-save-subscription \
  --capabilities CAPABILITY_IAM --region us-east-1 \
  --template-body '{"Resources":{"Fn":{"Type":"AWS::Lambda::Function","Properties":{
    "FunctionName":"flight-save-subscription","Runtime":"python3.12","Handler":"index.handler",
    "Role":"arn:aws:iam::<ACCOUNT_ID>:role/flight-lambda-role","Timeout":10,
    "Code":{"ZipFile":"<your one-file handler, JSON-escaped>"}}}}}'
```
Keep it **one file**. Edit later → re-inline + `update-stack` (the deployed code is the source of truth). *(CLI mode: `zip` + `aws lambda create-function --zip-file fileb://…` — doesn't work in Cowork.)*

**Verify** — invoke, then read the **row** back (don't `cat` the invoke output):
```bash
aws lambda invoke --function-name flight-save-subscription \
  --payload '{"body":"{\"email\":\"test@example.com\",\"plan_name\":\"tokyo\",\"target_price\":10000}"}' out.json --region us-east-1
aws dynamodb get-item --table-name subscriptions \
  --key '{"email":{"S":"test@example.com"},"route":{"S":"TPE-TYO"}}' --region us-east-1
```
Expect `route=TPE-TYO`, `currency=TWD`, `target_price=10000`, **no `subscription_status`**.

### Step 5 — API Gateway + the subscribe UI

```bash
aws apigatewayv2 create-api --name flight-api --protocol-type HTTP --region us-east-1
# AWS_PROXY integration → route 'POST /subscribe' → $default stage --auto-deploy
# CORS: AllowOrigins=* AllowMethods=POST,OPTIONS AllowHeaders=content-type
# aws lambda add-permission for apigateway.amazonaws.com
```
Add the UI to the M0 site: **two plan cards** (台北✈東京 / 台北✈首爾), each with a **TWD target-price input** + a 「開始追蹤」 button that `fetch`es `POST <api>/subscribe` with `{email, plan_name, target_price}`. (Hint the current cheapest so they pick a sane budget — Tokyo ~NT$9,325, Seoul ~NT$5,989.)

**Verify:** picking 台北✈東京 + NT$10,000 on the live site creates a `TPE-TYO` row.

### Step 6 — Show the subscribed state (close the write-only loop)

1. **`flight-list-subscriptions` Lambda behind `GET /subscriptions?email=…`:** `Query` the table by `email` and return the rows. Same shared role (the `Query` perm already covers it); deploy inline (Method 1). Add the `GET /subscriptions` route to `flight-api` (CORS allows `GET`).
2. **UI:** on mount, `fetch('<api>/subscriptions?email=<signed-in email>')` → mark subscribed plans with a **已訂閱** badge + current target + an **更新目標價** button; flip a card to subscribed right after a successful `POST`.

> **Security note (say it):** `GET /subscriptions?email=` trusts a client-supplied email with **no auth** — fine for this no-guard course; production would verify the Supabase JWT in the Lambda first.

**Verify:** reload the live site → subscribed cards show the badge + target + Update button. `GET /subscriptions?email=you@x.com` returns your rows (a GET — testable from a browser / the Cowork web-fetch).

---

# Part 1.2 — Fetch prices on a schedule

> **Dates:** M1 has no user dates — the parser picks **next month** (`YYYY-MM`). **Currency:** **TWD is the gate** (vs `target_price`); a **USD** call is best-effort for the email's supplementary line. **No payment guard:** the scan matches every subscriber whose target is met (no `subscription_status` filter — that's M2).

### Step 7 — Create the routes config bucket + the fare queue + extend the role

The bucket holds **`flight-routes.json`** *and* the **Lambda zips** (Step 9 seeds both via the bridge). Paste to the agent:

ask """
>
In us-east-1 (replace <ACCOUNT_ID>):
>
1. Bucket: `aws s3api create-bucket --bucket flight-config-<ACCOUNT_ID> --region us-east-1`
>
2. Fare queue: `aws sqs create-queue --queue-name flight-fare-queue --region us-east-1`
>
Show me the queue URL.
>
"""

Then **extend the shared role** (merge — keep the M1.1 DynamoDB+Secrets statements):

ask """
>
Update the inline policy `flight-data` on role `flight-lambda-role` to ALSO allow (add as new statements, keep existing):
>
- `s3:GetObject` and `s3:PutObject` on `arn:aws:s3:::flight-config-<ACCOUNT_ID>/*` (Get: wrapper reads routes; Put: the flight-seed bridge writes objects in Step 9)
>
- `sqs:SendMessage` and `sqs:GetQueueUrl` on `arn:aws:sqs:us-east-1:<ACCOUNT_ID>:flight-fare-queue`
>
- `lambda:InvokeFunction` on `arn:aws:lambda:us-east-1:<ACCOUNT_ID>:function:flight-parser`
>
Use `aws iam put-role-policy --role-name flight-lambda-role --policy-name flight-data --policy-document '<full merged JSON>' --region us-east-1`. Read the current policy with `get-role-policy` FIRST and merge — don't drop the existing statements.
>
"""

> ⚠️ `put-role-policy` **replaces** the named policy wholesale — merge or you silently strip M1.1's access and the parser dies with `AccessDeniedException`.

**Verify:** the queue URL resolves; the merged `flight-data` lists S3(Get+Put) + SQS + InvokeFunction **alongside** the original DynamoDB + Secrets.

### Step 8 — Write the parser + parser_wrapper (one `index.py` each)

Everything folds inline — no separate `travelpayouts.py`/`routes.py` to ship (that file isn't in the repo). They deploy via **S3** (Step 9) because the parser's `index.py` runs **past the 4096-char inline cap**.

**`flight-parser` `index.py`** (one route per invocation):
1. Read the `token` from `flight/travelpayouts`.
2. From the event get `{origin, destination, route}`; compute **next month** `YYYY-MM`.
3. Fetch cheapest **in TWD** (the gate), then **best-effort in USD** (inline `fetch_cheapest`, below). TWD empty/`429` → log + return; **USD empty/`429` → just omit it** (never block/crash on USD).
4. **`Scan subscriptions`** `FilterExpression route = :r`. **M1: no status filter.**
5. For each subscriber where **`Decimal(target_price) >= Decimal(cheapest_twd["price"])`**: `SendMessage` the **dual-currency body** — `cheapest` (TWD, always) + `cheapest_usd` (USD, only if present).

**Inline `fetch_cheapest`** (stdlib `urllib`, no layer). Parse the **real keys** — `data[<DEST>][<idx>]` uses **`departure_at`/`return_at`** (not `depart_date`/`return_date`), plus `price`, `airline`. **Set a `User-Agent`** (some hosts behind Cloudflare reject the default urllib UA with `403`):
```python
import os, json, urllib.request, urllib.parse
UA = "Mozilla/5.0 (compatible; flight-notifier/1.0)"
def fetch_cheapest(origin, destination, month, token, currency):
    q = urllib.parse.urlencode({"origin":origin,"destination":destination,"depart_date":month,"currency":currency,"token":token})
    req = urllib.request.Request(f"https://api.travelpayouts.com/v1/prices/cheap?{q}",
        headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        body = json.loads(r.read())
    if not body.get("success") or not body.get("data"): return None
    offers = body["data"].get(destination, {})
    if not offers: return None
    best = min(offers.values(), key=lambda o: o["price"])
    return {"price": best["price"], "currency": currency.upper(), "airline": best.get("airline"),
            "depart_date": best.get("departure_at"), "return_date": best.get("return_at")}
```
**Call it twice — gate on TWD, USD best-effort:**
```python
tw = fetch_cheapest(origin, destination, month, token, "twd")
if not tw:
    print("no TWD fare for", route, "(empty/429) - skipping"); return {"ok": True, "route": route, "matched": 0}
us = fetch_cheapest(origin, destination, month, token, "usd")   # may be None — do NOT block on it
body = {"email": it["email"], "route": route, "plan_name": it.get("plan_name"), "target_price": int(tp),
        "cheapest": {"price": tw["price"], "currency":"TWD", "airline": tw["airline"],
                     "depart_date": tw["depart_date"], "return_date": tw["return_date"]}}
if us:
    body["cheapest_usd"] = {"price": us["price"], "currency":"USD", "airline": us["airline"],
                            "depart_date": us["depart_date"], "return_date": us["return_date"]}
_sqs.send_message(QueueUrl=QURL, MessageBody=json.dumps(body))
```

**`flight-parser-wrapper` `index.py`** (EventBridge target): read `flight-routes.json` from S3 (`boto3.client("s3").get_object(Bucket=os.environ["CONFIG_BUCKET"], Key="flight-routes.json")`); for each route `lambda.invoke(FunctionName="flight-parser", InvocationType="Event", Payload=…)` (async fan-out).

**Message schema enqueued to `flight-fare-queue`** (the contract Part 1.3 consumes): **`cheapest` (TWD) always present** — the gate + headline; **`cheapest_usd` (USD) optional** — the supplementary 約US$ line.
```json
{ "email":"you@example.com", "route":"TPE-TYO", "plan_name":"tokyo",
  "target_price": 12000,
  "cheapest":     {"price":9325,"currency":"TWD","airline":"LJ","depart_date":"2026-07-12T01:25:00+08:00","return_date":"2026-07-23T22:40:00+09:00"},
  "cheapest_usd": {"price":298, "currency":"USD","airline":"LJ","depart_date":"2026-07-12T01:25:00+08:00","return_date":"2026-07-23T22:40:00+09:00"} }
```
**Currency roles:** TWD primary (gate, subject, headline); USD supplementary only. Additive/backward-compatible. **Caveats:** two TP calls/route (2× `429` exposure — USD failure omits the block, never blocks); the two responses are **independent** (may be different flights / not exact FX — label 約); API param lowercase, message uppercase; same token for both.

### Step 9 — Deploy via the `flight-seed` S3 bridge (Method 2)

The `aws`-only connector can't author files / `zip` / inline an `s3 put-object` body, so a tiny **`flight-seed`** Lambda base64-decodes bytes into S3.

**9a — Deploy `flight-seed` once** (inline CFN — it's tiny):

ask """
>
Create a one-off `flight-seed` Lambda via inline CloudFormation in us-east-1 (replace <ACCOUNT_ID>). Handler `index.handler`, Runtime python3.12, Role `arn:aws:iam::<ACCOUNT_ID>:role/flight-lambda-role`, Timeout 30. Code (escape for the template):
>
```python
import base64, boto3
def handler(e, c):
    boto3.client("s3").put_object(Bucket=e["bucket"], Key=e["key"], Body=base64.b64decode(e["b64"]), ContentType=e.get("ct","application/octet-stream"))
    return {"ok": True, "key": e["key"]}
```
>
Then poll `aws cloudformation describe-stacks --stack-name flight-seed --query "Stacks[0].StackStatus" --region us-east-1` until CREATE_COMPLETE (the connector can't use `cloudformation wait`).
>
"""

**9b — Seed the routes JSON + the two zips** (build each zip in the sandbox, `base64 -w0`, invoke the bridge with **raw JSON**):

ask """
>
Land three objects in S3 via flight-seed, us-east-1 (replace <ACCOUNT_ID>). RAW JSON payload — only the `b64` field is base64:
>
1. routes config — `b64` = base64 of `[{"plan":"tokyo","origin":"TPE","destination":"TYO"},{"plan":"seoul","origin":"TPE","destination":"SEL"}]`:
   `aws lambda invoke --function-name flight-seed --payload '{"bucket":"flight-config-<ACCOUNT_ID>","key":"flight-routes.json","b64":"<BASE64>","ct":"application/json"}' /tmp/aws-api-mcp/workdir/out.json --region us-east-1`
>
2. parser zip — `zip -j parser.zip index.py && base64 -w0 parser.zip` → `key":"lambda/parser.zip"`, `ct":"application/zip"`.
>
3. wrapper zip — same → `key":"lambda/wrapper.zip"`.
>
Then verify each landed and **the zip ETag == the local md5** (NOT just size — a same-length single-char corruption passes a size check): `aws s3api head-object --bucket flight-config-<ACCOUNT_ID> --key lambda/parser.zip --query "ETag" --region us-east-1`. For a big paste, chunk it (see [[aws-best-practice]] Method 2). Read the routes back: `aws s3 cp s3://flight-config-<ACCOUNT_ID>/flight-routes.json - --region us-east-1`.
>
"""

**9c — Create both functions from S3:**

ask """
>
Create both Lambdas pointing Code at S3, us-east-1 (replace <ACCOUNT_ID>). Same role, python3.12, handler `index.handler`, env `CONFIG_BUCKET=flight-config-<ACCOUNT_ID>`:
>
`aws lambda create-function --function-name flight-parser --runtime python3.12 --handler index.handler --role arn:aws:iam::<ACCOUNT_ID>:role/flight-lambda-role --timeout 30 --environment 'Variables={CONFIG_BUCKET=flight-config-<ACCOUNT_ID>}' --code S3Bucket=flight-config-<ACCOUNT_ID>,S3Key=lambda/parser.zip --region us-east-1`
>
Repeat for `flight-parser-wrapper` with `--timeout 60` and `S3Key=lambda/wrapper.zip`.
>
"""

> **Redeploy:** re-seed (9b) → `aws lambda update-function-code --s3-bucket … --s3-key …`. A function stuck `State=Failed` from a bad zip → **delete + recreate**. *(CLI: `zip -j parser.zip index.py` + `create-function --zip-file fileb://…`.)*

**Verify** — invoke the parser, read the **logs** (`filter-log-events`, not `logs tail`):
```bash
aws lambda invoke --function-name flight-parser --payload '{"origin":"TPE","destination":"TYO","route":"TPE-TYO"}' out.json --region us-east-1
aws logs filter-log-events --log-group-name /aws/lambda/flight-parser --query "events[].message" --region us-east-1
```
Logs show e.g. `TPE-TYO … 9325 TWD`. `Runtime.ImportModuleError` → bad zip (re-seed, check ETag); `AccessDeniedException` → the role merge dropped a statement.

### Step 10 — Seed a test match + wire EventBridge

Bump a test subscriber above the live fare, then wire the 30-min schedule:

ask """
>
In us-east-1 (replace <ACCOUNT_ID>):
>
1. `aws dynamodb update-item --table-name subscriptions --key '{"email":{"S":"test@example.com"},"route":{"S":"TPE-TYO"}}' --update-expression 'SET target_price = :t' --expression-attribute-values '{":t":{"N":"12000"}}' --region us-east-1`
>
2. `aws events put-rule --name flight-price-check --schedule-expression "rate(30 minutes)" --region us-east-1`
>
3. `aws lambda add-permission --function-name flight-parser-wrapper --statement-id eventbridge --action lambda:InvokeFunction --principal events.amazonaws.com --source-arn arn:aws:events:us-east-1:<ACCOUNT_ID>:rule/flight-price-check --region us-east-1`
>
4. `aws events put-targets --rule flight-price-check --targets 'Id=1,Arn=arn:aws:lambda:us-east-1:<ACCOUNT_ID>:function:flight-parser-wrapper' --region us-east-1`
>
Then invoke the wrapper once manually and show me both routes' parser logs:
`aws lambda invoke --function-name flight-parser-wrapper --payload '{}' out.json --region us-east-1`
`aws logs filter-log-events --log-group-name /aws/lambda/flight-parser --query "events[].message" --region us-east-1`
>
"""

**Change frequency live:** `aws events put-rule --name flight-price-check --schedule-expression 'rate(...)'`. **Verify:** both routes ran; the fare queue depth went up for the seeded match (`aws sqs get-queue-attributes --queue-url <url> --attribute-names ApproximateNumberOfMessages --region us-east-1` ≥ 1).

---

# Part 1.3 — Email on target

> Adds one account: **Resend** (set up + verified in the prereq via the throwaway `flight-resend-test` Lambda — the Cowork sandbox can't POST to `api.resend.com`).

### Step 11 — Confirm the Resend secret

```bash
aws secretsmanager describe-secret --secret-id flight/resend --region us-east-1 --query "Name"   # expect flight/resend
```
Missing → run the prereq's Resend step. (Sending stays from `onboarding@resend.dev` until M3.)

### Step 12 — Write the `flight-fare-notification` consumer

A **single self-contained `index.py`** (you write the renderer inline). **Too big for inline CFN** (~5–6 KB + mixed quote types break CFN escaping) → deploys via the **S3 bridge** (Method 2), like the parser. Per SQS message:
1. Read `flight/resend` (api_key + `from`).
2. **`Query notification_history`** on `pk=f"{email}#{route}"`, newest first, limit 1.
   - **Key schema:** `pk` (HASH, S) + `sent_at` (RANGE, S — **ISO-8601 UTC string**). `ScanIndexForward=False, Limit=1` → the latest. **Don't** store `sent_at` as an epoch Number (breaks the range-key type / ordering).
3. **Decide to send:** no recent row OR last `sent_at` older than `NOTIFY_FLOOR_HOURS` → send; else within the floor send only if **`new <= last*(1-REALERT_PCT/100)` OR `(last-new) >= REALERT_ABS_TWD`**; else **skip** (log "skipped (deduped)").
4. **Build the email** from **`cheapest` (TWD)** headline + optional **`cheapest_usd` (USD)** supplementary:
   - `subject(fare)` → 「✈️ 台北 → 東京 降價通知！NT$9,325 已達標」 (leads with **NT$**).
   - `render_html(fare, target_price, usd_price=…)` → **NT$ headline + optional 約 US$** line (only when the message has `cheapest_usd`) + a 「立即訂購」 button via `fare.booking_url(currency="twd")`. Pass `usd_price` **only if** `cheapest_usd` is present, else omit (TWD-only card).
5. **POST to Resend** (with a `User-Agent` header) → on a real **2xx**, `PutItem notification_history` `{pk, sent_at(now ISO-8601 UTC), email, route, price, currency}`.
   - **Failure classes (no DLQ → don't loop):** **`429`/`5xx`** transient → re-raise so SQS redelivers; **`403`/`422`** permanent (incl. demo-sender to a non-account address) → log + **drop** (return success). Write history **only after a 2xx**.

> **You WRITE the renderer here — `email_render.py` doesn't exist in any prior milestone.** Don't `cp`/"reuse" it; fold these into `index.py`:
> - **`subject(fare) -> str`** → NT$ subject.
> - **`render_html(fare, target_price, marker=None, usd_price=None) -> str`** → NT$ headline + optional 約US$ + 「立即訂購」. **Simple, table-free, transactional** HTML (table/image-heavy reads as marketing — [[resend-best-practice]]).
> - **`render_text(fare, target_price, marker=None, usd_price=None) -> str`** → plain-text fallback (always send `html` **and** `text`).
> - **`booking_url(fare, marker=None) -> str`** → Aviasales deep link `ORIGIN+DDMM+DEST+DDMM`; append `?marker=` only when given.
> `flightproxy/email_render.py` in the repo is the **reference spec** for what you fold inline.

**Extend the role with CONSUMER-side SQS perms** — the event-source mapping needs **all three**: `sqs:ReceiveMessage`, `sqs:DeleteMessage`, **and `sqs:GetQueueAttributes`** on the fare queue. **Miss any one → the mapping fails closed silently** (never polls, no error). Merge into `flight-data` (don't drop existing).

**Set VisibilityTimeout, purge, then wire** (in this order):
```bash
# 1. VisibilityTimeout strictly > the Lambda timeout (default 30s == 30s is NOT enough) — set ~6×:
aws sqs set-queue-attributes --queue-url <fare-queue-URL> --attributes VisibilityTimeout=180 --region us-east-1
# 2. PURGE stale test messages — creating the mapping instantly drains the queue (old undeliverable matches → 403 burst → 429 cascade):
aws sqs purge-queue --queue-url <fare-queue-URL> --region us-east-1
# 3. event-source mapping + the tunable knobs:
aws lambda create-event-source-mapping --function-name flight-fare-notification \
  --event-source-arn <fare-queue-ARN> --region us-east-1
aws lambda update-function-configuration --function-name flight-fare-notification \
  --environment 'Variables={NOTIFY_FLOOR_HOURS=24,REALERT_PCT=20,REALERT_ABS_TWD=2000}' --region us-east-1
```
*(Deploy the function itself via the seed bridge, Step 9-style. Optional: a redrive policy with a small `maxReceiveCount` as a permanent-failure backstop.)*

**Verify** — with a seeded match on the queue, the consumer sends one email:
```bash
aws logs filter-log-events --log-group-name /aws/lambda/flight-fare-notification --query "events[].message" --region us-east-1
```
Inbox receives the alert (subject 「✈️ 台北 → 東京 降價通知！NT$9,325 已達標」, **NT$ headline + optional 約 US$**, 「立即訂購」 button).

### Step 13 — Prove dedup + the re-alert threshold

1. Re-run the parser immediately → **no** second email (within `NOTIFY_FLOOR_HOURS`); logs say "skipped (deduped)".
2. Seed a cheapest ≥20% or ≥NT$2,000 below the last alerted price → a fresh email **does** go out.

**Verify:**
```bash
aws dynamodb query --table-name notification_history --key-condition-expression 'pk = :p' \
  --expression-attribute-values '{":p":{"S":"you@example.com#TPE-TYO"}}' \
  --no-scan-index-forward --max-items 3 --region us-east-1
```
A recent `sent_at`+`price`; the immediate re-run skipped; the big-drop wrote a new row.

### Optional: monetize the booking link (affiliate marker)

**Skippable — the notifier works without it.** Travelpayouts gives you a **marker** (affiliate ID, e.g. `736582`); a booking link with `?marker=<id>` credits bookings to you (30-day cookie). To enable: copy your marker → `aws secretsmanager put-secret-value --secret-id flight/travelpayouts --secret-string '{"token":"<TOKEN>","marker":"<MARKER>"}' --region us-east-1` (both fields — a partial update drops the token) → read `marker` in the handler and pass `render_html(…, marker=marker)`. `booking_url()` adds `?marker=` only when present. Skip it and the milestone is still complete.

---

## Things to watch out for (whole milestone)

1. **No payment guard in M1** — no `subscription_status` anywhere; everyone who subscribes is eligible. The paywall (status + payment confirmation + active-only parser filter) is **M2**.
2. **CORS + no-AWS-creds-in-front-end** — set CORS on the HTTP API; the browser only POSTs to API Gateway; only Lambdas touch AWS (the Supabase publishable key in the front-end is fine — public, auth-only).
3. **Decimal, not float** — `target_price`/`price` to DynamoDB as `Decimal(str(x))`; convert back for JSON ([[aws-best-practice]] Rule 3).
4. **Deploy method by size** — 1-file ≤4096 chars → inline CFN; bigger (parser, notification) → the `flight-seed` S3 bridge, **verify by ETag==md5** (size misses a same-length corruption).
5. **Parse the API's real keys** — `departure_at`/`return_at`, not `depart_date`/`return_date`, or you get silently-empty fares.
6. **TWD primary, USD supplementary** — gate/dedup/headline on `cheapest` (TWD); render 約US$ only when `cheapest_usd` is present.
7. **SQS: all three consumer perms + VisibilityTimeout > timeout + purge before mapping** — miss a perm → silent no-poll; equal timeout → mid-flight redelivery; stale messages drain on mapping creation.
8. **Resend: drop permanent (403/422), retry transient (429/5xx); custom User-Agent (Cloudflare 1010); send to your Resend-account email on the demo sender** — see [[resend-best-practice]].
9. **`notification_history` dedup BEFORE send**, write the row **only after a 2xx** — the at-least-once-safe ordering.
10. **MCP gotchas** — inline CFN (no `fileb://`), verify-by-effect (no `cat`), `filter-log-events` (not `logs tail`), poll `describe-stacks` (not `cloudformation wait`), raw `--payload`, no JMESPath backticks, clone into a native dir.

## Expected duration

3–5 hours for the full milestone (DynamoDB + 5 Lambdas + API Gateway + S3 + SQS + EventBridge + Resend + the subscribe/subscribed-state UI). It's three parts back-to-back — take a break between 1.1, 1.2, 1.3.

## Next step

When `m1-flight-price-checker-checklist` is green: 「M1 完成！你有一個能動的*免費*降價通知器了 — 訂閱、定時抓價、達標寄信（含去重），而且還沒有付款門檻。跟我說『啟動 M2』，我們來接金流，加上『只有付費者才收得到通知』的門檻。」Then load `m2-ecpay-subscription`.

## Reference

- DynamoDB CLI: https://docs.aws.amazon.com/cli/latest/reference/dynamodb/ · AWS HTTP API: https://docs.aws.amazon.com/apigatewayv2/
- Travelpayouts `/v1/prices/cheap`: https://travelpayouts.github.io/slate/ — `data[<DEST>][<idx>]` with `price`/`airline`/`departure_at`/`return_at`.
- EventBridge: https://docs.aws.amazon.com/eventbridge/ · SQS: https://docs.aws.amazon.com/sqs/ · Resend: https://resend.com/docs/api-reference/emails/send-email
- `fetch_cheapest` + the renderer are written **inline** in their Lambdas; `flightproxy/email_render.py` in the repo is the renderer reference spec.
- [[aws-best-practice]] — Cowork execution constraints (Method 1 inline / Method 2 seed-bridge + ETag), Decimal, region discipline, IAM-merge, SQS visibility timeout.
- [[resend-best-practice]] — demo-sender-to-self, verified-`from`, dedup-before-send, html+text, Cloudflare UA, 5 req/s, permanent-vs-transient, no-VPC.
