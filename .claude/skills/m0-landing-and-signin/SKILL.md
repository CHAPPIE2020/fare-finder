---
name: m0-landing-and-signin
description: Flight Price Notifier Milestone 0 — generate the v1 landing page with Lovable, wire Supabase sign-up/sign-in, push to GitHub, deploy to Vercel. Use when the student says "啟動 M0", "start M0", "build the M0 landing page", "機票通知的入口網站", or any variant that maps to "先做出一個能登入的網站".
---

# M0 — Landing Page + Sign-in（先有一個能登入的機票通知網站）

## What this skill does

Walks the student through Milestone 0 end-to-end. By the end the student has:

1. A live URL on Vercel (e.g. `https://flight-notifier-xyz.vercel.app`)
2. A GitHub repo that auto-deploys to Vercel on every push
3. A Lovable project two-way-synced to that GitHub repo
4. A v1 landing page (`/`) for **Flight Price Notifier** with: a hero (「設定航線與目標價，機票降價就通知你」), three feature cards (盯緊熱門航線 / 達標自動通知 / 隨時取消), a top-right Sign in / 登入 button, and a footer; plus an authenticated app shell at `/app` that greets the signed-in user and shows a "dashboard coming soon" placeholder.
5. **Auth wired up** — students can register, log in, and log out. **v1 uses Lovable's default auth backend (Lovable Cloud)** for the fastest path to a working sign-up; **Step 4 swaps it to the student's own Supabase project**. Either way **auth is the ONLY thing Supabase does** — only the default `auth.users`; no app data ever lives in Supabase (subscriptions go to DynamoDB on AWS from M1.1 on). No subscribe form, no price logic, no payments yet.

**Out of scope for M0:** the subscribe UI, the DynamoDB `subscriptions` table, AWS Lambdas, the price-check loop, Resend email, and payments. Those are M1.1 and later. (Note: Supabase never gets custom tables — all app data lives in DynamoDB on AWS.)

## When to load this skill

Trigger phrases:
- "啟動 M0" / "start M0" / "begin M0"
- "幫我蓋 M0 的 landing page"
- "機票通知的入口網站"
- Any prompt referencing "先做出一個能登入的網站"

Do NOT load this skill for M1.1+ — they have their own skills.

## Execution mode: Cowork vs CLI (confirm before Step 1)

Before starting, ask: 「你是用 Cowork 還是本機 CLI 跑 Claude Code？」

- **Cowork mode** — no local shell. Every "verify with `gh ...` / `vercel ...`" line is **CLI-only**; replace with the MCP / browser equivalent noted in the same step.
- **CLI mode** — the Bash commands here apply directly.

See `m0-landing-and-signin-prerequisites` for the full Cowork-vs-CLI table. The rest of this skill assumes you've picked a mode.

## Required external accounts

| # | Service | Used for |
|---|---|---|
| 1 | GitHub | Lovable creates a repo here; Vercel imports from here |
| 2 | Lovable (`lovable.dev`) | Generates the v1 UI + scaffolds Supabase auth |
| 3 | Supabase (`supabase.com`) | `auth.users`, sign up / sign in / sign out |
| 4 | Vercel | Auto-deploys the GitHub repo |

If any of the four is missing, **stop and ask the student to register first.**

## The product being built

M0 builds **Flight Price Notifier** — fixed, no per-student variation. The v1 is a real, working signed-in SaaS: the landing page sells 「設定航線與目標價，機票降價就通知你」, and the Sign In / Sign Up button leads to a working Supabase-backed auth flow. After signing in, the user lands on a placeholder authenticated page (e.g. 「Hi {email}，你的航線追蹤儀表板即將上線」). The subscribe form and price engine come in M1.1+.

## Conversational flow

You (Claude Code) drive the student through 5 steps. Don't dump all steps at once. After each step, **wait for confirmation** before moving on.

> **Before Step 1:** confirm the one-time CLI + MCP setup is done. If not, **load `m0-landing-and-signin-prerequisites` first**.
> - **CLI mode sanity check:**
>   ```bash
>   gh auth status && vercel whoami && supabase projects list 2>/dev/null | head -1
>   ```
>   If any error out, switch to the prerequisites skill and come back.
> - **Cowork mode sanity check:** confirm `mcp__vercel__*` and a Supabase MCP are available; if missing, do the prerequisites skill first.

### Step 1 — Generate v1 in one shot: attach the rule skills + paste the full prompt

There is **no separate "create a blank project" step** — that just burns a Lovable generation for nothing. Instead, do it all in the **first** message to Lovable:

1. 到 https://lovable.dev/ 登入，**+ New project**.
2. **Attach the two rule files as context** to this first prompt: `lovable-best-practice` and `supabase-best-practice` (drag them into Lovable's chat / attach-files). They tell Lovable the non-negotiables up front — **Supabase is auth-only (no custom tables/RPCs)**, ship a **plain Vite SPA**, PascalCase components — so v1 obeys them on the first generation instead of you fixing them after.
3. Paste the **full prompt below verbatim as that same first message** and generate. One generation → the whole landing page + auth + `/app` shell.
4. 把 Lovable project URL 貼回來（`https://lovable.dev/projects/<id>`）。

⚠️ **Don't click Connect Supabase / Connect GitHub yet.** The prompt already says "use Lovable's default backend (Lovable Cloud) for v1" — GitHub connects in Step 2, your own Supabase in Step 4. (Why Lovable Cloud first: [[lovable-best-practice]] Rule 1.) Generating with the full prompt up front is the right move — it's *one* generation, and the attached rules apply exactly when they matter most (the first build). Re-rolls burn the free quota ([[lovable-best-practice]] Tip 2).

**The full prompt (paste verbatim):**

> Build a SaaS landing page + authenticated app shell for **Flight Price Notifier (機票降價通知)**, a product that watches popular flight routes from Taipei and emails the user when the cheapest fare drops to or below their target price — targeted at budget-driven travelers who don't care exactly when they fly, they just want a ticket under their budget.
>
> The site must include:
>
> 1. A public landing page (`/`) with:
>    - Hero section: product name **"Flight Price Notifier"** prominently displayed, value prop 「設定航線與目標價，機票降價就通知你」 (English subtitle: "Set a route and a target price — we email you when the fare drops."), and a primary CTA button labeled **"Sign in / 登入"** in the top-right header.
>    - Features section with exactly 3 feature cards:
>      * Card 1: 「盯緊熱門航線 (Always-on route watching)」 — 持續監控台北出發的熱門航線（東京、首爾），自動抓最低票價。
>      * Card 2: 「達標自動通知 (Target-price email alerts)」 — 低於你設定的目標價，就寄 email 提醒你，附上立即訂購連結。
>      * Card 3: 「隨時取消 (Cancel anytime)」 — 月訂閱制，不想用隨時停，沒有綁約。
>    - Footer with copyright 「© 2026 Flight Price Notifier」.
>
> 2. Authentication using Lovable's built-in Supabase-style auth (use whatever auth backend Lovable provides by default — Lovable Cloud is fine for this v1; we'll swap to a user-owned Supabase project in a later step):
>    - Sign Up page with email + password
>    - Sign In page with email + password
>    - Sign Out functionality
>    - Email confirmation can be disabled for simplicity in this v1
>
> 3. An authenticated app shell at `/app` that the user lands on after signing in:
>    - Greets the signed-in user by email: 「Hi {user.email}」
>    - A placeholder message: 「你的航線追蹤儀表板即將上線 — 下一個里程碑會加上訂閱航線的功能。」 (English: "Your dashboard is coming soon. Route-subscription will be added in the next milestone.")
>    - A Sign Out button in the header
>
> Design requirements:
> - Modern, professional dark theme (purple/violet accent on a near-black background)
> - Use Inter or a similar sans-serif font
> - Mobile responsive
> - Tasteful subtle animations (fade-in on scroll is fine; don't overdo it)
>
> Out of scope for this v1: route-subscription form, target-price input, fare display, payment, custom database tables (do NOT create a `subscriptions` or `profiles` table — only use Supabase's default `auth.users`). Those come in later milestones. Stick to landing page + auth + placeholder dashboard.

**Why this prompt is shaped this way:** explicit `/` and `/app` routes (so auth redirect has somewhere to land), exact card copy (so you don't re-roll for wording), and the **no-custom-tables clause** (Supabase is auth-only in this course — app data lives in DynamoDB from M1.1; see [[supabase-best-practice]]).

**Verify before moving on:** Lovable preview shows the hero, the three cards, a working Sign In / Sign Up button, and signing up lands you on `/app` with 「Hi {email}」.

### Step 2 — Connect Lovable to GitHub

Tell the student:

> 在 Lovable 左下角的 **⚙ 選單圖示**（settings/menu）點開 → 選 **GitHub** → **Connect to GitHub** → 授權 → 讓 Lovable 建立一個新的 repo（名稱可用 `flight-price-notifier`）。建好後把 GitHub repo URL 貼回來。
>
> *(Lovable 的 UI 會變動：GitHub 入口曾在右上角，現在在**左下角選單**裡，跟 GitLab / Connectors / Knowledge 並列。如果位置又不同，找有 GitHub 字樣的選項即可。)*

**Verify before moving on (CLI):**
```bash
gh repo view <owner>/<repo> --json name,defaultBranchRef -q '{name,branch:.defaultBranchRef.name}'
```
**Cowork:** open the repo URL in the browser and confirm Lovable's files are there.

### Step 2.A — (Mandatory) Make sure the app is a plain Vite SPA before importing to Vercel

> **Do this BEFORE Step 3. Skipping it is the #1 M0 deploy trap.** Lovable sometimes scaffolds a **TanStack Start / SSR** app (Cloudflare-targeted) instead of a plain Vite SPA. On Vercel that builds "successfully" but **every route 404s** (or only `/` works and `/app` 404s) — a confusing failure because the build is green. *(Attaching `lovable-best-practice` in Step 1 asks for a Vite SPA up front, which usually avoids this — but verify anyway.)*

Check Lovable's project: if `package.json` / the router is **Vite + React Router** (a normal SPA), you're fine — skip to Step 3. If you see **TanStack Start**, `@tanstack/start`, an SSR server entry, or a `wrangler`/Cloudflare config, paste this into Lovable first:

**The full prompt (paste verbatim):**
> Convert this project to a plain **Vite + React single-page app (SPA)** suitable for static hosting on Vercel. Remove any TanStack Start / SSR / server-side rendering and any Cloudflare/wrangler config. Use **React Router** for client-side routing (`/`, `/app`, sign-in, sign-up). The build output must be a static SPA (`vite build` → `dist/`) with a SPA fallback so deep links like `/app` resolve client-side. Keep all existing UI, auth, and styling unchanged.

Let Lovable regenerate + sync to GitHub, then continue to Step 3.

**Verify before moving on:** the repo's `package.json` build script is `vite build` (not a TanStack/SSR build), and there's no `wrangler.toml`.

### Step 3 — Deploy to Vercel

Tell the student:

> 到 https://vercel.com/new → **Import** 你剛剛的 GitHub repo → Framework Preset 選 **Vite**（如果沒自動偵測到）→ **Deploy**。完成後把 Vercel 上線網址貼回來。

**Verify before moving on (CLI):**
```bash
curl -sS -o /dev/null -w "%{http_code}\n" https://<your>.vercel.app          # 200 for /
curl -sS -o /dev/null -w "%{http_code}\n" https://<your>.vercel.app/app      # 200 too (SPA deep-link)
```
Expect `200` on **both** — if `/` is 200 but `/app` is 404, you skipped Step 2.A (SSR build with no SPA fallback). **Cowork:** open both URLs, confirm the landing page loads and `/app` doesn't 404.

> **Note for Claude Code:** if the build fails on Vercel, the usual cause is the framework preset — Lovable SPAs are Vite/React, so set the preset to **Vite**. If the build is green but routes 404, it's the SSR-vs-SPA trap from Step 2.A. Read the Vercel build log before guessing.

### Step 4 — Swap auth from Lovable Cloud to the student's own Supabase project

v1 used **Lovable Cloud** (Lovable's managed auth). Now swap to the student's **own** Supabase project so they own the `auth.users` going into M1.1 (where `email` becomes the DynamoDB partition key).

**4.1 — Create the Supabase project + grab two values:**

> 1. 到 https://supabase.com/ → **New project**（名字例如 `flight-notifier`，region 選離你近的，例如 `Northeast Asia (Tokyo)`）。
> 2. **等到專案變成 Healthy 狀態**（建立要一兩分鐘）。
> 3. **Project Settings → API**，複製兩個值：**`Project URL`** 和 **Publishable API Key**（`sb_publishable_*` — 這是新版「anon key」，瀏覽器安全、RLS 保護）。

**4.2 — Paste this prompt into Lovable** (replace `<URL>` and `<PUBLISHABLE_KEY>` with the two values from 4.1):

> Switch this project's backend from Lovable Cloud to the user's own Supabase project. Do NOT keep any Lovable Cloud references.
>
> Specifically:
>
> 1. Find every place the project currently uses Lovable Cloud's Supabase client (Lovable's auto-provisioned `supabase` client, typically in `src/integrations/supabase/client.ts` or similar). Update it to use these credentials instead:
>
>    VITE_SUPABASE_URL= <SUPABASE_URL>
> 
>    VITE_SUPABASE_PUBLISHABLE_KEY= <PUBLISHABLE_KEY>
>
>    Note: Supabase's newer "publishable key" (`sb_publishable_*`) replaces what used to be called the "anon key". They're the same role (browser-safe, RLS-gated). If the codebase already uses `VITE_SUPABASE_ANON_KEY`, you can either:
>    - Rename to `VITE_SUPABASE_PUBLISHABLE_KEY` for consistency with current Supabase naming, OR
>    - Keep `VITE_SUPABASE_ANON_KEY` as the variable name but put the new `sb_publishable_*` value in it (works fine; it's just an env var name).
>    Pick one and apply consistently across `.env`, the client init code, and any docs.
>
> 2. Update the `.env` file (or `.env.local`) to use the values above. Remove any Lovable Cloud env vars (e.g. anything prefixed with `LOVABLE_CLOUD_*` or that points to a Lovable-owned Supabase ref).
>
> 3. Make sure the Supabase client is initialized exactly once and reads from `import.meta.env.VITE_SUPABASE_URL` and the matching key env var — no hardcoded URLs.
>
> 4. Disconnect / remove the Lovable Cloud integration if there's a UI toggle for it (Project Settings → Integrations → Lovable Cloud → disconnect). If you can't toggle it, at least make sure the code only references the new Supabase project.
>
> 5. Keep the Sign Up / Sign In / Sign Out flow exactly as it is. Only the backend target changes.
>
> After this change, sign-up should create users in the user's own Supabase `auth.users` table — verify by signing up a NEW test user in the Lovable preview, then checking the Supabase dashboard → Authentication → Users — the new email should appear there.

**4.3 — Finish the wiring outside Lovable:**

> 1. 在 Supabase **Authentication → Providers** 確認 **Email 已啟用**（demo 可關掉 email confirmation，否則測試帳號收不到確認信就卡住）。
> 2. **在 Vercel 也設同名環境變數**（Settings → Environment Variables）：`VITE_SUPABASE_URL` + `VITE_SUPABASE_PUBLISHABLE_KEY`（或你選定的 key 名），值跟 Lovable 一致，然後 **redeploy**。env 不一致是 M0 最常見的失敗原因 — 線上站還連著舊的 Lovable Cloud。

**Verify before moving on:** sign up a brand-new test email on the **live Vercel URL** (not just the Lovable preview), then check Supabase **Authentication → Users** — the new user appears in *the student's own* project (NOT Lovable Cloud).

> 📌 **Save these two values — `VITE_SUPABASE_URL` and the publishable/anon key.** You'll cache them in a `flight/supabase` Secrets Manager secret during the **M1.1 prereq** (AWS isn't set up until then), so a future Cowork session recalls them instead of you hunting them down in the Supabase dashboard again. The anon key is **public by design** (it ships in the browser bundle), so caching it is pure convenience — Supabase stays **auth-only for data**. (See [[aws-best-practice]] Rule 2.)

> **Note for Claude Code:** keep the **publishable/anon key** in the front-end (correct — RLS-protected). The **service-role key is NOT used in M0** at all; it only appears later in the AWS Lambdas (M1.1+) — and even there, DynamoDB uses IAM, not a Supabase key. If the student pastes a service-role key into the front-end, stop them (see [[supabase-best-practice]] Rule 2).

### Step 5 — Final smoke test

Walk the student through: sign up → confirm email if required → sign in → see the authenticated placeholder page → sign out. All on the live Vercel URL.

**Verify before moving on:** the full sign-up → sign-in → sign-out loop works on the deployed site, and the new user is in the student's Supabase.

## Things to watch out for (common mistakes)

1. **Don't waste a generation on a blank project** — give Lovable the **full prompt + the two attached rule skills as your very first message**. A throwaway "create a blank project" prompt just burns a generation off the daily free quota.
2. **Connecting your own Supabase too early** — do it in Step 4, after Vercel, so the swap is clean (v1 runs on Lovable Cloud).
3. **Vercel framework preset wrong** — Lovable = Vite/React; if the build fails, fix the preset first.
4. **Service-role key in the front-end** — never. M0 uses only the publishable key.
5. **Email confirmation turned on but no SMTP** — for the demo, either disable email confirmation in Supabase Auth settings or use the magic-link flow; otherwise the test user can't finish sign-up.
6. **Forgetting to test sign-OUT** — the loop must close; a broken sign-out hides session bugs.
7. **Custom Supabase tables creeping in** — Supabase is auth-only, forever. App data (the `subscriptions` table) lives in DynamoDB on AWS, starting M1.1. Don't create Supabase data tables.
8. **SSR-vs-SPA trap (Step 2.A)** — if Lovable emitted TanStack Start / SSR, the Vercel build is green but `/app` 404s. Convert to a plain Vite SPA *before* importing. Symptom: `/` works, deep links don't. (Attaching `lovable-best-practice` in Step 1 usually prevents it.)
9. **Don't rename the product in M0** — "Flight Price Notifier" / 「機票降價通知」 is referenced across later milestones and the booking/email copy. Keep it consistent.

## Expected duration

30–60 minutes for a first-timer (most of it waiting on Lovable/Vercel builds and creating the four accounts).

## Next step

When `m0-landing-and-signin-checklist` is green, tell the student:
「M0 完成了！你現在有一個能註冊登入的線上網站。準備好的話跟我說『啟動 M1.1』，我們來讓使用者真的訂閱一條航線。」
Then load `m1-flight-price-checker` (run `m1-flight-price-checker-prerequisites` first).

## Reference

- Lovable: https://docs.lovable.dev/
- Supabase Auth: https://supabase.com/docs/guides/auth
- Vercel deploys: https://vercel.com/docs/deployments
