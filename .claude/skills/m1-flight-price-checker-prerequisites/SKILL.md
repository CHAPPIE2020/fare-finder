---
name: m1-flight-price-checker-prerequisites
description: One-time setup before Milestone 1 of the Flight Price Notifier course — an INTERACTIVE, agent-driven walkthrough. The Cowork agent drives the student step by step: AWS access FIRST (the one step done outside Cowork, in a Claude Code CLI), then it collects each key value from the student in chat (GitHub PAT, Supabase url/publishable-key, Travelpayouts token, Resend key) and does the rest itself — caching every key in Secrets Manager, proving the push→Vercel loop by recalling the GitHub token from the secret, and verifying Resend via a throwaway Lambda. Use when the student starts M1 for the first time, or when `m1-flight-price-checker` / `-checklist` detects the project, AWS access, the Travelpayouts token, or Resend is missing.
---

# M1 Prerequisites — interactive setup (the agent drives)

**You are the Cowork agent running this skill. Drive the student through it one part at a time** — don't dump the whole thing. For each part: tell them what's about to happen, ask for exactly the value(s) you need, **wait for their reply**, then run the AWS/git work yourself, confirm it worked, and move on. The student should only ever have to (a) do the AWS-console + CLI step once, and (b) paste you a key value when you ask. Everything else is yours.

## How to run this (read first)

- **One part at a time, conversationally.** End each part by confirming success and announcing the next part. Never ask for two different keys in the same message.
- **You run all the AWS/git commands** via the AWS API MCP + git tool. The student never runs `aws` themselves (except the one CLI credential-write in Part A, which you hand them).
- **Every AWS command:** `--region us-east-1`, `[default]` profile (no `--profile`).
- **Check-then-collect:** before creating any secret, `describe-secret` first; if it already exists, tell the student "already cached — skipping" and move on (a returning student usually has them all).
- **The order is fixed: AWS first.** Secrets Manager is an AWS service — you can't cache anything until the `[default]` profile exists.
- **Read [[aws-best-practice]] *Cowork execution constraints* once** before you deploy anything. Two facts shape this skill: (1) the common AWS connector is **`aws`-only** (no shell/`zip`/file authoring) → Lambda code deploys via **inline CFN** (small) or the **`flight-seed` S3 bridge** (big); (2) the **sandbox can't reach arbitrary hosts** (`api.resend.com` is proxy-blocked) → anything that POSTs to a third party runs **from a Lambda**, not the sandbox. This is why Resend is verified from a Lambda (Part D).

**The four secrets you'll end up with** (tell the student this up front so they know what's coming):

| Credential | Cached as | Note |
|---|---|---|
| GitHub PAT | `flight/github` `{pat}` | write-credential — real secret |
| Supabase url + publishable key | `flight/supabase` `{url, publishable_key}` | publishable key is **public** — convenience cache (never the service-role key) |
| Travelpayouts token | `flight/travelpayouts` `{token}` | real secret (the parser Lambda reads it) |
| Resend API key | `flight/resend` `{api_key, from}` | real secret (the notification Lambda reads it) |

**Opening line to the student (say something like):**
> "I'll set up everything M1 needs. First I'll grab your three M0 project links, then we do the **one step outside Cowork** — wiring your AWS credentials. After that, just paste me four keys one at a time and I'll store and test them all. Ready? First, your project links."

---

## Part 0 — Collect the M0 project links

Before anything else, get the **three URLs from the student's finished M0** — you'll reuse them throughout (the GitHub URL for the clone + push test, the Vercel URL to confirm the deploy, the Supabase URL to point them at their keys). Ask for all three in one message and wait:

> "Paste me your three M0 links:
> - **GitHub repo:** (e.g. `https://github.com/uopsdod/fly-low-alert/`)
> - **Vercel deploy URL:** (e.g. `https://fly-low-alert.vercel.app/`)
> - **Supabase project URL:** (e.g. `https://supabase.com/dashboard/project/pmvtdxbelbgglpalxype/`)"

When they reply, **echo the three back** so they can confirm you've got them right, and **remember them for the rest of this skill:**
- **GitHub repo URL** → you'll `git clone` it (Part B1) and push to it (Part B4).
- **Vercel deploy URL** → you'll open it to confirm the "V3" title shows after the push (Part B4).
- **Supabase project URL** → that dashboard's **Project Settings → API** page is where the student copies the **Project URL** + **publishable key** you'll ask for in Part B5.

> If any link is missing or looks wrong (e.g. a Supabase *table-editor* URL instead of the project URL, or a GitHub URL that 404s), ask them to re-check before continuing — a wrong repo/Vercel URL makes the Part B push test fail confusingly.

---

## Part A — AWS access (the ONE step outside Cowork)

This is the only part the student does outside Cowork — because writing `~/.aws/credentials` needs a local Claude Code CLI session (the Cowork connector then reads those creds). **Walk them through it, then wait for them to confirm AWS is live before continuing.**

**Tell the student, step by step (pause between):**

1. **Install the AWS API MCP connector** in Cowork: *Customize → Connectors → search "AWS API MCP" → Install.*
2. **In the AWS Console, as the root user** of their course account:
   - **IAM → Users → Create user** (suggest `admin-for-cowork`) → attach **`AdministratorAccess`** → create.
   - That user → **Security credentials → Create access key → "Command Line Interface (CLI)"** → copy the **Access key ID + Secret** (shown once).
3. **Open a Claude Code CLI session** (not Cowork) and paste this prompt **there** — fill in the two values:

   > I have new AWS credentials to configure. Access key ID: `<YOUR_ACCESS_KEY_ID>`. Secret access key: `<YOUR_SECRET_ACCESS_KEY>`. Detect Mac/Linux vs Windows for the right path (`~/.aws/credentials` or `%USERPROFILE%\.aws\credentials`), write the **`[default]`** profile with these values (preserving any other existing profiles), then test with `aws sts get-caller-identity`.

4. **Come back to Cowork and tell me when `aws sts get-caller-identity` worked.**

**Then YOU verify it from Cowork** before moving on:
```bash
aws sts get-caller-identity --query Account --output text --region us-east-1
```
- Returns an account ID → say "✅ AWS is wired — now I can cache your keys. Next: GitHub." and go to Part B.
- Errors → the `[default]` profile isn't written yet; have them redo step 3 in the CLI.

> ⚠️ Remind them: **root is used only once** (to make the admin user); never use root keys after. Revoke the access key at course end.

---

## Part B — Repo + GitHub (you cache it, then prove the push loop)

Now that AWS is up, cache GitHub **first** — because you'll prove the push loop by recalling the token *from the secret*, the same way every later milestone pushes.

**B1 — Clone the repo** (using the **GitHub repo URL from Part 0** — don't re-ask). Clone it **into a native dir** (NOT the FUSE-mounted workspace — git locking fails there with `config.lock: Operation not permitted`). Tell them the working copy is ephemeral (GitHub + Vercel are the source of truth).

**B2 — Ask for the GitHub PAT.** Say:
> "Paste me your **GitHub fine-grained PAT**. If you don't have one: github.com/settings/personal-access-tokens → *Generate new token (fine-grained)* → *Only select repositories* → this one repo → *Repository permissions → Contents → Read and write* → copy the `github_pat_…`. (Already cached it on a past run? Just say so — I'll reuse `flight/github`.)"

Wait for the token. ⚠️ It's a write-credential to their repo — don't echo it back in plaintext.

**B3 — Cache it yourself** (check-then-collect):
```bash
aws secretsmanager describe-secret --secret-id flight/github --region us-east-1 --query "Name"
# ResourceNotFoundException → create:
aws secretsmanager create-secret --name flight/github --secret-string '{"pat":"<their token>"}' --region us-east-1
# already exists → update:  aws secretsmanager put-secret-value --secret-id flight/github --secret-string '{"pat":"<their token>"}' --region us-east-1
```

**B4 — Prove the push→Vercel loop by recalling the token FROM the secret** (this is the exact flow every milestone uses — no re-pasting):
```bash
aws secretsmanager get-secret-value --secret-id flight/github --region us-east-1   # parse the "pat"
```
Use that `pat` to: change the site title to **"Flight Price Notifier V3"**, push to the **Part-0 GitHub repo**, track the Vercel deployment, and confirm the **Part-0 Vercel deploy URL** shows the new title. **Verify** it on that live URL, then revert the title. If it fails: the PAT lacks Contents:RW, or GitHub→Vercel auto-deploy is off — fix before continuing (every M1 step pushes). Tell the student "✅ push loop works — and I'll always pull your token from the secret, never ask again."

**B5 — Supabase (ask, then cache).** The Part-0 link was the Supabase **dashboard** URL — what you cache is different (the API values). Point them there and ask:
> "Open your Supabase project → **Project Settings → API** (it's under the project you linked in Part 0). Paste me two values: the **Project URL** (`https://….supabase.co`) and the **publishable key** (`sb_publishable_*` — the browser-safe key; **NOT** the service-role key)."

Wait, then cache (check-then-collect):
```bash
aws secretsmanager describe-secret --secret-id flight/supabase --region us-east-1 --query "Name"
aws secretsmanager create-secret --name flight/supabase --secret-string '{"url":"<their url>","publishable_key":"<their key>"}' --region us-east-1
# or put-secret-value if it exists
```
> The publishable key is **public by design** (it ships in the browser bundle) — caching it is pure convenience; no Lambda reads it. **If they paste a `service_role` / secret key, stop them** — never cache that (see [[supabase-best-practice]] Rule 2).

---

## Part C — Travelpayouts (ask, verify, cache)

**Ask the student for their Travelpayouts token.** Say:
> "Paste me your **Travelpayouts API token**. Get it free at travelpayouts.com → connect the **Aviasales** program → Profile → API token. (Just the token — no `marker` needed; that's an optional booking-commission add-on for later.)"

Wait, then **verify it works** by fetching this URL (a GET — the agent web-fetch can do this; it's not proxy-blocked like Resend's POST):
```
https://api.travelpayouts.com/v1/prices/cheap?origin=TPE&destination=TYO&depart_date=2026-07&currency=usd&token=<their token>
```
Confirm the JSON has `"success":true` (a `401`/`"success":false` = wrong token or Aviasales not connected — have them fix it). It works for both `twd` and `usd` — one token, no new secret.

Then **cache it** (check-then-collect):
```bash
aws secretsmanager describe-secret --secret-id flight/travelpayouts --region us-east-1 --query "Name"
aws secretsmanager create-secret --name flight/travelpayouts --secret-string '{"token":"<their token>"}' --region us-east-1
```
Tell the student "✅ Travelpayouts verified + cached."

---

## Part D — Resend (ask, cache, verify from a Lambda)

**Ask the student for their Resend key + account email.** Say:
> "Two values, please: your **Resend API key** (`re_…` — sign up free at resend.com → API Keys → Create API Key; a **Sending-access** key is enough), and the **email you signed up to Resend with**. ⚠️ On the demo sender (`onboarding@resend.dev`), Resend only delivers to that **account email** — a send anywhere else looks fine in the log but never arrives. So this email is also what we'll use as your test subscriber later."

Wait, then **cache it** (check-then-collect) — `from` is fixed, `test_to` is their account email:
```bash
aws secretsmanager describe-secret --secret-id flight/resend --region us-east-1 --query "Name"
aws secretsmanager create-secret --name flight/resend --secret-string '{"api_key":"<their key>","from":"onboarding@resend.dev","test_to":"<their account email>"}' --region us-east-1
```

**Then verify by sending from a throwaway Lambda** — the Cowork sandbox can't POST to `api.resend.com` (proxy-blocked), but a Lambda has internet. Deploy `flight-resend-test` via **inline CFN** (handler `index.handler`, python3.12, Role `arn:aws:iam::<ACCOUNT_ID>:role/flight-lambda-role`, Timeout 15), JSON-escaping this code into `Code.ZipFile`:
```python
import json, urllib.request, boto3
def handler(e, c):
    s = json.loads(boto3.client("secretsmanager").get_secret_value(SecretId="flight/resend")["SecretString"])
    body = {"from": s["from"], "to": s["test_to"], "subject": "resend test", "html": "<p>it works</p>", "text": "it works"}
    req = urllib.request.Request("https://api.resend.com/emails", data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + s["api_key"], "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0 (compatible; flight-notifier/1.0)"}, method="POST")  # UA: Resend is behind Cloudflare
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            print("RESEND_OK", r.status, r.read().decode())
    except urllib.error.HTTPError as ex:
        print("RESEND_ERR", ex.code, ex.read().decode())
    return {"done": True}
```
Poll `aws cloudformation describe-stacks --stack-name flight-resend-test --query "Stacks[0].StackStatus" --region us-east-1` until CREATE_COMPLETE (the connector can't use `cloudformation wait`). Then invoke + read the logs:
```bash
aws lambda invoke --function-name flight-resend-test --payload '{}' out.json --region us-east-1
aws logs filter-log-events --log-group-name /aws/lambda/flight-resend-test --query "events[].message" --region us-east-1
```

> ⚠️ **This needs `flight-lambda-role`**, which `m1-flight-price-checker` Step 3 creates. If the student is doing the prereq strictly before any building, **create that role now** (see the build skill Step 3) or run this Resend verify right after build-Step 3.

**Read the result + tell the student:** `RESEND_OK 200 {"id":"..."}` in the logs **and** the email in their Resend-account inbox (check spam) = ✅. Errors: `401` = wrong key; `403`/`422` = a `from` they can't send from (stay on `onboarding@resend.dev` until M3); `403` body `error code: 1010` = Cloudflare blocked the UA (the handler sets one — only bites if it was dropped). When it passes, tear down: `aws cloudformation delete-stack --stack-name flight-resend-test --region us-east-1`.

---

## Wrap-up — confirm all green, then hand off

Run a final check and report to the student:
```bash
aws sts get-caller-identity --query Account --output text --region us-east-1                 # AWS wired
aws secretsmanager list-secrets --region us-east-1 --query "SecretList[].Name"                # all four flight/* present (list-all-and-scan; no backtick JMESPath)
```
Confirm to the student, in plain language:
- ✅ **AWS** wired (`[default]` profile).
- ✅ **All four secrets cached:** `flight/github`, `flight/supabase`, `flight/travelpayouts`, `flight/resend` — *you'll never have to paste these again; I pull them from Secrets Manager every session.*
- ✅ **Push loop** proven (V3 round-trip, token recalled from the secret).
- ✅ **Travelpayouts** `"success":true`; ✅ **Resend** test email arrived.

Then say: **"Setup's done. Say『啟動 M1』and I'll build the whole notifier — subscribe, scheduled fetch, and the alert email."** Load `m1-flight-price-checker`.
