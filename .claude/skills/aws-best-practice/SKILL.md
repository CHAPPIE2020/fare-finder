---
name: aws-best-practice
description: Hard rules and operational SOP for the AWS side of the Flight Price Notifier course — a SERVERLESS stack (Lambda + DynamoDB + SQS + S3 + API Gateway + EventBridge + Secrets Manager). Use whenever a student is packaging a Lambda, writing to DynamoDB, wiring SQS, handling the ECPay callbacks (CheckMacValue), managing secrets, or debugging an AWS-side failure in M1+. Sourced from this stack's real failure modes.
---

# AWS Best Practice (Flight Price Notifier — serverless)

This course's AWS side is **100% serverless** — there is **no EC2, no SSH, no SSM, no systemd**. The pieces are: **6 Lambdas**, **2 DynamoDB tables** (`subscriptions`, `notification_history`), **2 SQS queues**, an **S3** routes config, **API Gateway (HTTP API v2)**, **EventBridge**, and **Secrets Manager**. AWS access is the **`[default]` profile** (IAM user `admin-for-cowork` with `AdministratorAccess`), read by the **AWS API MCP** in Cowork; every command pins **`--region us-east-1`**.

When guiding a student through AWS operations, **apply these rules proactively** — stop them before they break one. Each rule maps to a real failure mode of *this* stack.

> **Multi-account variant (M3 go-live):** the AWS MCP uses **one `[default]` profile**, but the **domain's Route 53 hosted zone** can live in a **different AWS account** than the flight project (Lambdas/secrets/API Gateway). When they differ, **switch the `[default]` profile between DNS steps (domain account) and secret/Lambda steps (project account)** — see [[m3-domain-prerequisites]]. For a single-account student this doesn't arise; treat it as the advanced case.

> Adapted from a Course 2 AWS skill that was EC2/SSM-based. Those rules (SSH-vs-SSM, systemd PATH, instance tagging) **do not apply** — this stack has no servers to log into. Only the secrets-hygiene and IAM-user rules carried over; the rest below are serverless-native.

---

## Execution mode: Cowork vs CLI

The hard rules apply identically in both — only the command surface differs.

| Operation | CLI mode | Cowork mode |
|---|---|---|
| Run any AWS API call | `aws <service> <verb> ... --region us-east-1` | `call_aws <service> <verb> ...` via AWS API MCP (set region/creds in the connector) |
| Deploy a 1-file Lambda | `aws lambda create-function --zip-file fileb://fn.zip ...` | inline CFN `Code.ZipFile` (Method 1 below) — no file transfer |
| Deploy a >4096-char / layered Lambda | `zip` the dir → `--zip-file fileb://fn.zip` | S3 via the `flight-seed` base64 bridge → `Code.S3Bucket/S3Key` (Method 2 below) |
| Read a Lambda's logs | `aws logs tail /aws/lambda/<fn> --since 10m --follow` | `aws logs filter-log-events --log-group-name /aws/lambda/<fn> --query "events[].message"` (the MCP rejects `logs tail`) |
| Inspect a DynamoDB row | `aws dynamodb get-item --table-name subscriptions --key '{...}'` | `call_aws dynamodb get-item ...` |

**Profile/region:** the course uses the **`[default]`** AWS profile (no `--profile` flag needed). It has **no default region**, so **every** call passes `--region us-east-1`. In Cowork the AWS API MCP reads `~/.aws/credentials`'s `[default]` block (written via a Claude CLI session — see [[m1-flight-price-checker-prerequisites]]). Forgetting the region is the #1 "it works for me but not in the script" gap.

---

## Cowork execution constraints (read before you deploy)

In Cowork there are **two separate hosts**, and the gap between them — **not** an auth gap — is what bites:

- **The AWS API MCP** *does* have AWS access: it authenticates from `~/.aws/credentials` (the `[default]` profile written in prereqs) and runs `aws` calls. **Creds and network are fine.** But — verified on a real M1.2 run — the common connector is **`aws`-CLI-only: it executes `aws` API commands and nothing else.** It has **no shell**, so it **cannot author files, run `zip`, or `cat` an output** in any workdir. (Some connector builds *do* expose a writable workdir; **don't assume it** — see the capability fork below.)
- **The bash/build sandbox** is a *different* host. It can build a zip, but has **no AWS creds and no network route to AWS** (DNS to `*.amazonaws.com` is blackholed; s3/sts/lambda return **HTTP 000**), and **no shared path to the connector** — so it can't hand the built zip to the MCP, and can't upload to S3 itself.

So you **cannot** get a built zip to S3 by any direct path: the sandbox has the file but no network; the MCP has the network but can't author the file. The bridge that closes this gap is a tiny **`flight-seed` Lambda** (Method 2 below). Other gotchas from real runs:

1. **The zip-transfer gap (above):** no direct file→S3 path. → single small files deploy **inline** (Method 1); anything bigger goes through the **`flight-seed` base64→S3 bridge** (Method 2).
2. **The build sandbox has no AWS network:** `curl`/uploads to AWS from it return **HTTP 000**; the web-fetch tool is **GET-only**.
3. **You can't read MCP-written files.** `aws lambda invoke … out.json` writes the response body where you **can't `cat` it back**. **Verify the effect instead** — `get-item` (DynamoDB), a queue depth, or `logs filter-log-events`.
4. **The MCP's command surface ≠ the full CLI.** It runs core AWS **API operations**, not the CLI's convenience wrappers or client-side binary handling. Verified rejections/quirks (every milestone inherits these):
   - `aws logs tail` → **rejected** ("operation 'tail' does not exist"). Use **`aws logs filter-log-events --log-group-name … --query "events[].message"`** (optional `--start-time <epoch_ms>`).
   - `aws cloudformation wait …` → **rejected** ("operation 'wait' is not allowed"). **Poll** `describe-stacks --query "Stacks[0].StackStatus"` until `CREATE_COMPLETE` (sleep between calls).
   - `aws lambda invoke` → **rejects `--query` and `--cli-binary-format`.** And **`--payload` is forwarded RAW, not base64** — send plain JSON (`'{"k":"v"}'`); base64-encoding it fails with `InvalidRequestContentException`.
   - **JMESPath backtick literals fail to parse** anywhere (`SecretList[?starts_with(Name,\`flight/\`)]` → "Unknown token"). Use plain projections: `--query "SecretList[].Name"`.
   - **`s3api put-object` Body can't be inlined.** `--cli-input-json '{"Body":"…"}'` is a streaming blob the CLI **silently drops → a 0-byte object**, and there's **no `--body <file>`** to point at without a shell. This is *why* the `flight-seed` bridge exists, not a clever one-liner.
   - **`dynamodb query --scan-index-forward false` is rejected.** Use the boolean flag form **`--no-scan-index-forward`** (and `--scan-index-forward` for ascending) — the `<flag> <value>` form fails validation.
   - **`&&` (and other shell chaining) is rejected** as "prohibited operators." The MCP runs one AWS call, not a shell line — send each command separately (independent calls can go as parallel tool uses).
   - **The batch/array form of `call_aws`** (`["aws …","aws …"]`) **fails validation** — pass a single command string per call; issue multiple calls instead of one array.
5. **Git on the mounted folder fails.** `git clone`/ops in the mounted workspace folder error (`config.lock: Operation not permitted` — FUSE can't do git's locking). **Clone into a native dir** (the agent's home); treat the working copy as **ephemeral** — GitHub + Vercel are the source of truth.

**→ Deployed shape ≠ repo layout (state this up front).** Every function in this stack deploys as a **single-file `index.handler`** — *not* the `aws/<fn>/handler.py` + a `common/` package the repo sources suggest, and *not* `zip … --zip-file fileb://`. In Cowork the sandbox has **no network route to AWS**, so code goes in **inside the API call** (inline CFN, Method 1) or **via the base64→S3 bridge** (Method 2). Fold shared helpers (CMV, ddb access) **into each `index.py`** rather than importing a package. (The repo's `aws/` tree can lag the deployed code — the verified M2 sources live single-file; treat the deployed function as the source of truth for behavior.)

**→ The Cowork way to deploy Lambda code (two methods):**

**Method 1 — inline `Code.ZipFile` (single small file).** Send the code *inside* the API call — **CloudFormation with inline `Code.ZipFile`**: `aws cloudformation create-stack --template-body '<json>'`, function code inline, no file transfer. **Limits: single file, ≤4096 chars, handler `index.handler`.** This is how M1.1's one-file Lambdas (`save_subscription`, `list_subscriptions`) deploy — they use only boto3 (already in the runtime), so no extra files.

**Method 2 — S3 `Code.S3Bucket/S3Key` via the `flight-seed` bridge (>4096 chars).** When a function's code **exceeds the inline 4096-char cap** (or you ever need to land any other bytes in S3), inline won't fit and `s3api put-object` can't take an inline body (see constraint #4). **First check your connector** (capability fork):

- **`aws`-only connector (the common case):** use the **`flight-seed` bridge** below — it's the only path that works.
- **Connector with a writable shell workdir (rare):** you *may* instead author the files there, `zip`, and `aws s3 cp` directly — but if you're unsure, use the bridge; it works in both.

**The `flight-seed` base64→S3 bridge** (canonical for `aws`-only connectors):

1. **Deploy a tiny `flight-seed` Lambda once**, via inline CFN `Code.ZipFile` (it fits — it's ~6 lines):
   ```python
   import base64, boto3
   def handler(e, c):
       boto3.client("s3").put_object(
           Bucket=e["bucket"], Key=e["key"],
           Body=base64.b64decode(e["b64"]),
           ContentType=e.get("ct", "application/octet-stream"))
       return {"ok": True, "key": e["key"]}
   ```
   Its role needs **`s3:PutObject`** on the target bucket (add it to `flight-lambda-role`, or give `flight-seed` its own role).
2. **Build each zip in the sandbox** (it has `zip`), then `base64 -w0` it to a single line.
3. **Materialize each object in S3** by invoking the bridge with **raw JSON** (the MCP forwards `--payload` verbatim — do **not** base64 the payload itself):
   ```bash
   aws lambda invoke --function-name flight-seed \
     --payload '{"bucket":"flight-config-<ACCOUNT_ID>","key":"lambda/parser.zip","b64":"<BASE64_OF_ZIP>","ct":"application/zip"}' \
     /tmp/aws-api-mcp/workdir/out.json --region us-east-1
   ```
   (Use the same bridge to write `flight-routes.json`, layer zips — any bytes.)
4. **Deploy the function from S3:** `aws lambda create-function --code S3Bucket=flight-config-<ACCOUNT_ID>,S3Key=lambda/parser.zip …` (or CFN `Code:{S3Bucket,S3Key}`). Redeploy after an edit = re-seed the new zip, then `aws lambda update-function-code --s3-bucket … --s3-key …`.

> ⚠️ **Verify the uploaded zip by its S3 ETag against the local md5 — a size check is NOT enough.** Pasting a long base64 string into `--payload` corrupts the bytes in **two** ways seen on real runs: (a) a dropped 4-char quartet → object a few bytes short (a size check *would* catch this), **and** (b) a single-character substitution (`o`→`q`) that leaves the length **identical** but the content wrong → a size check **passes**, then `update-function-code` ships a broken zip. For a single-part PUT, **S3's ETag == the object's md5**, so compare it to the local zip's md5:
> ```bash
> aws s3api head-object --bucket flight-config-<ACCOUNT_ID> --key lambda/parser.zip \
>   --query "ETag" --region us-east-1     # strip the quotes → must equal `md5 -q parser.zip`
> ```
>
> **Chunk-with-ETag is the DEFAULT for any zip > ~1.5 KB — not an optional fallback.** A single long base64 `--payload` **silently corrupted 3 times in one session** (`status_notification` once, `save_subscription` twice) — each time a *same-length* one-character substitution that only the ETag-vs-md5 check caught. One fragile giant paste is the single biggest time-sink; reserve single-shot for genuinely tiny files. Recipe:
> 1. `base64 -w0 parser.zip` → split into ~600-char pieces **aligned to 4-char boundaries** (each piece decodes to whole bytes; the last carries the `=` padding).
> 2. Seed each piece to its **own** key via `flight-seed` (short pastes rarely corrupt).
> 3. **Verify each part's S3 ETag against the local md5 of that decoded piece**; re-seed only a mismatched part.
> 4. Concatenate the parts with **`flight-assemble`** (boto3-only: read each part in order, write the joined object). **Deploy `flight-assemble` once as a persistent helper alongside `flight-seed`** — it's a required piece of this stack's toolchain, not ad-hoc (it had to be recreated mid-session because it wasn't standing). Inline-CFN it the same way:
>    ```python
>    import boto3
>    def handler(e, c):
>        s3 = boto3.client("s3"); buf = b""
>        for k in e["parts"]:                      # ordered list of part keys
>            buf += s3.get_object(Bucket=e["bucket"], Key=k)["Body"].read()
>        s3.put_object(Bucket=e["bucket"], Key=e["key"], Body=buf, ContentType="application/zip")
>        return {"ok": True, "key": e["key"], "bytes": len(buf)}
>    ```
> 5. **HARD RULE: never `update-function-code`/`create-function` until the assembled-object ETag == the local-zip md5.** No exceptions — a passing size check is not enough (see the same-length-substitution case above).
>
> And a function whose create **failed** on a bad zip is stuck `State=Failed` — `update-function-code` is blocked; **delete and recreate** it once the zip verifies.

This is the standard way to land bytes in S3 from an `aws`-only connector — for **>4096-char function zips** (e.g. M1.3's `flight-fare-notification`, whose folded HTML/text renderer is ~5–6 KB *and* mixes single+double quotes, so inline CFN is doubly impossible). Note most functions **fold into a single `index.py`** (boto3/stdlib only, **no layer** anywhere in this course — see Rule 6); the reason to use S3 is the **size cap** + the ETag-verifiable artifact, not file count.

**→ Reading deployed Lambda source in Cowork: the `flight-srcdump` helper.** "The deployed function is the source of truth" (above) is only useful if you can *read* it — and in Cowork you can't: the sandbox has no AWS network (a presigned `get-function` code URL is blackholed → HTTP 000), and `aws lambda invoke`'s output file is unreadable (constraint #3). So when you need to faithfully **extend** an existing function (e.g. M2 layering `subscription_status` onto M1's `save_subscription`, or the `active`-gate onto the `parser`), deploy a third standing helper alongside `flight-seed`/`flight-assemble`:

```python
# flight-srcdump — downloads a target function's own deployment zip, extracts the .py,
# and writes the source to SSM /flight/srcdump/<fn> so you can read it inline.
import boto3, urllib.request, io, zipfile, json
def handler(e, c):
    lam, ssm = boto3.client("lambda"), boto3.client("ssm")
    url = lam.get_function(FunctionName=e["fn"])["Code"]["Location"]   # presigned; runs INSIDE AWS, so reachable
    z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(url).read()))
    src = "\n\n# ===== %s =====\n" % e["fn"] + "\n".join(
        "### FILE: %s\n%s" % (n, z.read(n).decode("utf-8", "replace"))
        for n in z.namelist() if n.endswith(".py"))
    ssm.put_parameter(Name="/flight/srcdump/%s" % e["fn"], Value=src[:4096],
                      Type="String", Overwrite=True)              # 4 KB Standard cap; use Advanced/parts if bigger
    return {"ok": True, "files": [n for n in z.namelist() if n.endswith(".py")], "bytes": len(src)}
```

- Its role needs **`lambda:GetFunction`** on the target + **`ssm:PutParameter`** on `/flight/srcdump/*` (add to `flight-lambda-role`).
- Read it back inline (this the sandbox/MCP *can* do): `aws ssm get-parameter --name /flight/srcdump/flight-save-subscription --query "Parameter.Value" --output text --region us-east-1`.
- Source over ~4 KB: write `Type=Advanced` (8 KB) or have `flight-srcdump` split into `/flight/srcdump/<fn>/<n>` parts and read them in order (same chunking idea as `flight-assemble`).
- **Inspect before you modify.** The repo's `aws/` tree can lag the live code (constraint **Deployed shape ≠ repo layout**); dump the deployed source first, edit *that*, then redeploy via Method 2 — don't extend the stale repo copy.

---

## Hard rules

### Rule 1 — Pin `--region us-east-1` on every command (the `[default]` profile has no region)

> **The rule:** No bare `aws ...` without a region. Always `--region us-east-1`. All ARNs hardcode `us-east-1` and the account ID. (No `--profile` flag — the course uses `[default]`.)

**Why:** The `[default]` profile has no region configured, so a call without `--region` either errors (`You must specify a region`) or, worse, silently hits a *different* default region where none of your resources exist — and you get `ResourceNotFoundException` for a table that demonstrably exists (in us-east-1). The same drift bites ARNs: an EventBridge rule ARN or SQS ARN with the wrong region/account silently never matches. Pin both, everywhere.

**How to apply:** In `scripts/env.sh`: `REGION=us-east-1; ACCOUNT=<your account id>` and reference `--region "$REGION"` in every script (resolve `ACCOUNT` once via `aws sts get-caller-identity --query Account --output text`). When a call returns "not found" for something you just created, **check the region first** before assuming it wasn't created.

---

### Rule 2 — Secrets Manager is the single source of truth for EVERY key — so a fresh Cowork session never re-asks

> **The rule:** **All** API keys/tokens live under **`flight/*` in Secrets Manager** — collected **once**, reused forever. The only credential that must persist *locally* is the **`[default]` AWS profile** (it's how the AWS MCP authenticates); everything else is one `get-secret-value` away. So a new Cowork session re-derives every key from AWS instead of asking you to paste them again. **No keys in code, in committed env files, or anywhere the front-end can be scraped for a *write* secret.**

**The canonical `flight/*` set:**

| Secret | Shape | Introduced | Consumed by |
|---|---|---|---|
| `flight/travelpayouts` | `{"token":"…"}` | M1.1 | **Lambda runtime** (parser) |
| `flight/resend` | `{"api_key":"…","from":"…"}` | M1.3 | **Lambda runtime** (fare-notification) |
| `flight/ecpay` | `{"merchant_id":"…","hash_key":"…","hash_iv":"…","env":"stage|prod","amount":"…"}` | M2 | **Lambda runtime** (checkout + callbacks + cancel) |
| `flight/telegram` | `{"bot_token":"…"}` | M4 | **Lambda runtime** (chat) |
| `flight/anthropic` | `{"api_key":"sk-ant-…"}` | M4 | **Lambda runtime** (chat) |
| `flight/github` | `{"pat":"github_pat_…"}` or bare string | M0 (Step 6) | **session bootstrap** — the Cowork git tool, to push (M1.1 prereq just discovers it) |

> **PAT secret name can differ** — the table's `flight/github` is the convention, but a real build had the GitHub PAT under **`github/personal-access-token`** instead. Before assuming it's missing, list the secrets and check both names: `aws secretsmanager list-secrets --query "SecretList[].Name" --region us-east-1` → grep for `github`. Use whichever exists; don't re-collect a PAT that's already stored under the other name.
| `flight/supabase` | `{"url":"…","publishable_key":"…"}` | M0 | **session recall** — the front-end build env (publishable key is **public by design**) |

**Two kinds of secret, treated the same way for storage but not for sensitivity:**
- **Runtime-read** (travelpayouts/resend/ecpay/telegram/anthropic) — a Lambda does `get_secret_value` at runtime; these are real secrets that must never reach the browser. (For `flight/ecpay`: the `merchant_id` is not secret but the `hash_key`/`hash_iv` are — keep the whole object server-side.)
- **Convenience-cache** (github/supabase) — stored so a new session recalls them without you re-finding them. The **GitHub PAT is a real write-credential** (treat it like a password; scope it to Contents:RW on the one repo). The **Supabase `publishable_key` is public by design** (it ships to the browser in `VITE_SUPABASE_PUBLISHABLE_KEY`) — caching it is for convenience, not secrecy. **Never** cache the Supabase **service-role** key.

**Why:** Every other home for a *write* key has a leak story — committed `.env` is indexed by GitHub's secret scanner instantly; a write key in client JS is visible in every visitor's network tab. Secrets Manager is KMS-encrypted, IAM-scoped, and `GetSecretValue` is CloudTrail-logged. And **DynamoDB/SQS/S3 need NO secret at all** — the Lambda's IAM role authorizes them.

**How to apply — check-then-collect (the SOP every prereq follows):** before asking the student for a key, check whether it already exists and only collect if missing:
```bash
# does it already exist? (a new session almost always: yes)
aws secretsmanager describe-secret --secret-id flight/resend --region us-east-1 --query "Name"
#   → returns "flight/resend"  ⇒ SKIP collection, it's already stored
#   → ResourceNotFoundException ⇒ collect the key, then:
aws secretsmanager create-secret --name flight/resend --secret-string '{"api_key":"re_…","from":"…"}' --region us-east-1
```
- To **update** an existing secret, `put-secret-value` (replaces the whole value — include every field).
- Scope the role's `secretsmanager:GetSecretValue` to `arn:aws:secretsmanager:us-east-1:<ACCOUNT_ID>:secret:flight/*` — **never** `Resource: "*"`.
- **Chat-retention caveat:** a key briefly appears in the transcript on its way to `create-secret`. Because it's now stored **once** (not re-pasted every session), there's far less exposure — but still rotate at course end. For real production, type values in the console.
- M3 go-live check greps the deployed front-end for `AKIA…` / `service_role` / any *write* secret → must be absent (the Supabase `publishable_key` is allowed there — it's public).

---

### Rule 3 — DynamoDB numbers are `Decimal`, not `float` — convert on the way in AND out

> **The rule:** When writing `target_price` / `price` to DynamoDB, pass a `Decimal`, not a Python `float`. When reading them back for arithmetic or JSON, convert `Decimal` → `int`/`float`/`str` explicitly.

**Why:** boto3's DynamoDB resource **rejects `float`** with `TypeError: Float types are not supported. Use Decimal types instead` — so a handler that does `{"target_price": 12000.0}` crashes on the very first `put_item`. Coming back the other way, a `Decimal` is **not JSON-serializable**, so `json.dumps(item)` throws `TypeError: Object of type Decimal is not JSON serializable` when the Lambda tries to return the row or log it. Both are first-hour M1.1 failures.

**How to apply:**
- Write: `from decimal import Decimal; item["target_price"] = Decimal(str(target_price))` (via `str` to avoid binary-float artifacts like `12000.00000001`).
- Read/return: convert before `json.dumps` — `int(item["target_price"])` for whole TWD, or a small `default=` encoder that maps `Decimal→int/float`.
- Keep this in `aws/common/ddb.py` so every handler shares one correct conversion.

---

### Rule 4 — The ECPay callbacks must verify CheckMacValue on the decoded FORM body — and keep empty-string fields (M2)

> **The rule:** In `flight-ecpay-return` / `flight-ecpay-period`, the body is **`application/x-www-form-urlencoded`, not JSON**. API Gateway HTTP API v2 may deliver it **base64-encoded** (`event["isBase64Encoded"] == True`) — decode to bytes, then `urllib.parse.parse_qs`. Recompute the CheckMacValue over the returned fields and compare. **Keep empty-string fields** (`CustomField3=`, `CustomField4=`) in the hash — drop only `CheckMacValue` itself.

**Why:** ECPay signs the exact field set it sent, **including empty CustomFields**. If you filter out `v == ""` before hashing (the single most common ECPay bug), you hash a different string and **every real callback fails verification** — the symptom looks like "ECPay isn't calling me" when you're actually rejecting a valid call. (Likewise, `json.loads`-ing a form body just yields garbage.) These are the highest-risk files in M2; wrong handling here means payments "succeed" at ECPay but never activate the subscription. See [[ecpay-best-practice]] Rule 2 for the full CMV algorithm.

**How to apply:**
```python
import base64, urllib.parse, hashlib
raw = event["body"]
if event.get("isBase64Encoded"):
    raw = base64.b64decode(raw).decode("utf-8")
params = {k: v[0] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}  # keep_blank!
# recompute CMV over params (KEEP "" values; drop only CheckMacValue) → compare → then RtnCode=="1"
```
There's **no `stripe listen` equivalent** — ECPay needs a public URL. Test via the stage 後台「模擬付款」 button or a real test-card run (see [[ecpay-best-practice]] Rules 7–8). A CMV mismatch is almost always the empty-field bug or a trailing-newline in a signed URL, not the keys.

---

### Rule 5 — SQS visibility timeout ≥ the consumer Lambda's timeout, and the consumer must be idempotent

> **The rule:** Each queue's `VisibilityTimeout` must be **≥** the timeout of the Lambda that consumes it (fare-queue → `fare_notification`, status-queue → `status_notification`). And the consumers must tolerate a message being delivered more than once.

**Why:** If the visibility timeout is shorter than the Lambda runtime, SQS makes the message visible again **while the Lambda is still processing it**, a second invocation picks it up, and the user gets a **duplicate email**. SQS is at-least-once by design, so even with correct timeouts a retry can re-deliver. The defense is the `notification_history` dedup (24h floor + re-alert thresholds) — which is exactly why dedup lives in the consumer, not the parser.

**How to apply:**
- The fare queue ships at the **default 30s**, equal to the Lambda's 30s timeout — that's **not** "≥", it's "==", and a slow send gets redelivered mid-flight. Set a concrete higher value (≈6×): `aws sqs set-queue-attributes --queue-url <url> --attributes VisibilityTimeout=180 --region us-east-1`.
- `fare_notification` checks `notification_history` **before** sending and writes a history row **after** — so a re-delivered message finds the recent row and skips.
- **The consumer role needs all three SQS actions** — `sqs:ReceiveMessage`, `sqs:DeleteMessage`, **and `sqs:GetQueueAttributes`** on the queue. The event-source mapping **fails closed and silently** (never polls, no error in the function log) if any is missing — distinct from the producer's `SendMessage`/`GetQueueUrl`.
- **Creating the event-source mapping immediately drains whatever is already on the queue.** Before you create it, **`aws sqs purge-queue --queue-url <url>`** to clear stale test messages, then seed one clean test subscriber — otherwise old/undeliverable matches burst through at once (and, on the demo sender, become a wave of `403`s that can trip Resend's rate limit). For permanent-failure protection without a full DLQ, set a redrive policy with a small `maxReceiveCount`.

---

### Rule 6 — Don't bundle boto3; DO bundle the stdlib helpers. This stack needs NO dependency layer.

> **The rule:** The Lambda runtime already includes **boto3** — never add it to the zip or layer. Everything else this course needs is **stdlib**: `travelpayouts.py` + `email_render.py` (copied into the function zip, not layered), and the ECPay CheckMacValue (`hashlib`) + the cancel POST (`urllib`). So there is **no `flight-deps` layer at all** — M2 does not add one.

**Why:** Bundling boto3 bloats the zip and can shadow the runtime's version with a subtly different one → confusing `botocore` errors. The old plan shipped a `stripe`+`requests` layer for the Stripe webhook; **ECPay removes that need** — CMV is `hashlib.sha256`, the form is built as plain strings, the cancel call is `urllib.request`. Staying layer-free means no `--platform manylinux` cross-build headaches and a smaller, faster cold start. `travelpayouts.py` and `email_render.py` are stdlib-only by design, so a plain `cp` into the function dir is all that's needed.

**How to apply (CLI mode):**
- **No `03_build_layer.sh` step** — skip it; there's no layer to publish. (If a leftover script references `flight-deps`, delete it.)
- `04_deploy_lambdas.sh`: `cp flightproxy/travelpayouts.py aws/parser/` and `cp flightproxy/email_render.py aws/fare_notification/` before zipping each function. The ECPay Lambdas (`save_subscription`, `ecpay_return`, `ecpay_period`, `cancel_subscription`) are stdlib + boto3 — nothing to copy or layer.
- If you ever add a C-extension dep later, build it with `--platform manylinux2014_x86_64` (or Docker), not on the Mac directly.

**In Cowork** the build-sandbox can't hand a zip to the MCP (see *Cowork execution constraints* above), so:
- **Small single-file Lambdas** (M1.1's `save_subscription`, `list_subscriptions`; M1.2's `flight-seed`) deploy as **inline CFN `Code.ZipFile`** with **no layer** (they only use boto3/stdlib, in the runtime). M1.2's `parser`/`wrapper` also **fold into one `index.py` each** (inline `fetch_cheapest` + routes-read) — they need no layer either.
- **Over the 4096-char inline cap** (a big handler) → land the zip in S3 via the **`flight-seed` base64 bridge** (Method 2 above), then deploy `Code:{S3Bucket,S3Key}`. The reason here is the **size cap** (and the checklist's S3-artifact check), not file count.
- **No dependency layer anywhere** (Rule 6) — M2's ECPay Lambdas are stdlib + boto3 (CMV via `hashlib`, cancel via `urllib`), so there's no `flight-deps` zip to seed.

---

### Rule 7 — Lambdas are NOT in a VPC — keep it that way

> **The rule:** Create every Lambda with **no VPC config**. They reach the public internet (Travelpayouts, ECPay, Resend) and AWS service endpoints directly.

**Why:** Putting a Lambda in a VPC removes its default internet route — outbound calls to `api.resend.com` / `payment.ecpay.com.tw` / Travelpayouts hang and time out unless you also add a NAT gateway (extra cost + setup) or VPC endpoints for every service. For this course there's no reason to be in a VPC; staying out keeps outbound HTTPS working with zero config. (DynamoDB/SQS/S3 are reached over their public endpoints via the SDK, authorized by IAM — no VPC needed.)

**How to apply:** Don't pass `--vpc-config` to `create-function`. If a student copied a VPC config from elsewhere and Resend calls start timing out, the VPC is the first suspect.

---

### Rule 8 — Use a dedicated IAM user/role; never root keys; one shared `flight-lambda-role`

> **The rule:** AWS access for the CLI/MCP uses a named IAM user (**`admin-for-cowork`**, `AdministratorAccess`) whose access key is written to the `[default]` profile — **never AWS root keys** (root is only used once, to *create* that IAM user). All Lambdas assume one **`flight-lambda-role`** scoped to exactly this project's resources.

**Why:** Root keys can't be scoped, audited per-key, or cleanly rotated — a leak means rotating everything. A named IAM user is auditable and deletable. For the Lambdas, one shared role is fine at course scale; its inline policy is scoped to the two table ARNs, the two queue ARNs, the `flight/*` secrets, the S3 routes prefix, and `lambda:InvokeFunction` on `flight-parser` — **not** `Resource: "*"`.

**How to apply:**
- If an `AKIA…` key is ever pasted into chat / a commit / a screenshot → delete it immediately and issue a new one; anyone who sees it has full access until revoked.
- Keep the role's policy in `scripts/iam-policy.json` (version-controlled), so it's reproducible.
- For a course, scoping to specific resource ARNs (not full admin on the role) is the teaching-grade floor; production would split per-Lambda least-privilege.

---

## How to debug a serverless failure (there's no server to log into)

Everything surfaces in **CloudWatch Logs**, one group per function:

```bash
# CLI mode:
aws logs tail /aws/lambda/flight-parser --since 15m --follow --region us-east-1
aws logs tail /aws/lambda/flight-fare-notification --since 15m --region us-east-1
# Cowork (the MCP rejects `logs tail`) — use filter-log-events:
aws logs filter-log-events --log-group-name /aws/lambda/flight-parser \
  --query "events[].message" --region us-east-1
```

Trace the path by following the data, not a process:
1. **Did the schedule fire?** `flight-parser-wrapper` logs (EventBridge target).
2. **Did the parser find subscribers + a cheap fare?** `flight-parser` logs (the `Scan` + `fetch_cheapest`).
3. **Did a message land on the queue?** `aws sqs get-queue-attributes --attribute-names ApproximateNumberOfMessages …`.
4. **Did the sender send / dedup?** `flight-fare-notification` logs (Resend response + "skipped (deduped)").

A silent "no email" is almost always: empty Travelpayouts result (skip), the dedup floor (working as intended), or a `Decimal`/region error in the logs of one of the four steps.

---

## Things to actively watch out for

1. **`Float types are not supported`** on `put_item` → Rule 3 (use `Decimal(str(x))`).
2. **`Object of type Decimal is not JSON serializable`** when returning a row → Rule 3 (convert before `json.dumps`).
3. **ECPay callback `CheckMacValue Error` / row never activates** → Rule 4 (decode the form body + `isBase64Encoded`, KEEP empty fields), not the keys.
4. **Resend/ECPay calls time out from inside a Lambda** → Rule 7 (the function is in a VPC; take it out).
5. **`ResourceNotFoundException` for a resource you just created** → Rule 1 (wrong region; you're hitting a default that isn't us-east-1).
6. **Duplicate alert emails** → SQS visibility timeout too short (Rule 5) and/or the consumer skipped the `notification_history` check.
7. **`AccessDeniedException` on DynamoDB/SQS/S3** → the `flight-lambda-role` inline policy is missing that ARN; re-apply `scripts/iam-policy.json`.
8. **`Runtime.ImportModuleError` after deploy** → a C-extension snuck into the Mac-built layer (Rule 6), or `travelpayouts.py`/`email_render.py` wasn't copied into the zip.
9. **CORS error in the browser console on subscribe** → set CORS on the HTTP API (`AllowOrigins`, `AllowMethods=POST,OPTIONS`, `AllowHeaders=content-type`).
10. **Travelpayouts `429`/empty** → treat as "skip this route this run," never crash the loop.

---

## Out of scope for this course (real prod, not enforced here)

- Per-Lambda least-privilege roles (course uses one shared, resource-scoped role).
- DLQs on the SQS queues + alarm on queue depth.
- Multi-stage beta/prod aliases + canary deploys.
- Customer-managed KMS keys + rotation policies.
- GSIs on the DynamoDB tables (course `Scan`s at tens-of-rows scale).

When a student asks "shouldn't we add a DLQ / split the role / add a GSI?" → "Yes, for production. The course optimizes for the minimum serverless surface that works end-to-end; hardening is a separate pass once the milestones are stable."

---

## Cross-references

- [[m1-flight-price-checker]] — the whole M1 build: DynamoDB + `save_subscription`/API GW (Part 1.1), `parser_wrapper`/`parser` + EventBridge + S3 routes (Part 1.2), `fare_notification` + SQS + `notification_history` dedup (Part 1.3).
- [[m2-ecpay-subscription]] — the recurring checkout + the two callbacks + the cancel Lambda (Rule 4 is the callback body/CMV handling).
- [[ecpay-best-practice]] — the application side of the same callbacks (CMV algorithm, empty-field bug, `1|OK`, SimulatePaid).
- [[supabase-best-practice]] — why no AWS key lives in the front-end (Supabase is the only thing the browser talks to, auth-only).
