---
name: m0-landing-and-signin-prerequisites
description: One-time CLI + MCP setup the student needs BEFORE starting M0 of the Flight Price Notifier course. Splits by execution mode — Cowork (MCP-only, no local shell) vs CLI (local terminal with `gh` / `vercel` / `supabase`). Use when the student is about to start M0 for the first time, or when the `m0-landing-and-signin` / `-checklist` skill detects a missing CLI / MCP and refers the student back here.
---

# M0 Prerequisites — CLI + MCP Setup

## Execution mode: Cowork vs CLI (read this first)

The course supports two execution environments. Confirm which one the student is using **before** doing anything else, and keep applying the right column for the rest of M0–M4.

| | **Cowork mode** | **CLI mode** |
|---|---|---|
| What it is | Claude Code in the hosted Cowork environment — no local shell, no `brew`/`npm install -g`, no local files | Claude Code on the student's own laptop with a real terminal |
| GitHub | GitHub MCP if available, else GitHub web UI / Lovable's Git panel | `gh` CLI |
| Vercel | `mcp__vercel__*` (required) | `mcp__vercel__*` preferred, `vercel` CLI fallback |
| Supabase | Supabase MCP (required) | MCP preferred, `supabase` CLI fallback |
| Interactive `*-login` flows | Skip — auth comes via the MCP install (OAuth in the Cowork UI) | Student runs them in their own terminal |
| Sanity checks (`vercel whoami`, `supabase projects list`) | **Skip** — verify by listing MCP tools instead | Required at the end |

**Ask up front:** 「你是用 Cowork 還是本機 CLI 跑 Claude Code？」 If unsure: 「你在 Lovable 旁邊有沒有開一個 terminal、可以 `brew install` 東西？」Yes = CLI、No = Cowork。

## What this skill does

Installs/logs in the CLIs (`gh`, `vercel`, `supabase`) and configures the MCP servers (Vercel, Supabase) that the M0 build (`m0-landing-and-signin`) and verification (`-checklist`) skills need.

**Run this ONCE before M0.** M1.1+ reuse the same tools, so it doesn't need re-running between milestones — except later milestones add their OWN prerequisites (M1.1 adds AWS + Travelpayouts; M2 adds a payment provider; M4 adds its own accounts), covered in those skills.

## When to load this skill

- "M0 環境準備" / "setup CLIs for M0" / "install course tools"
- Any time the student starts M0 and you detect a missing CLI/MCP during the checklist preflight

## Tool priority

| Task | Preferred | Fallback |
|---|---|---|
| Vercel deploy/inspect | `mcp__vercel__*` | `vercel` CLI |
| Supabase project/auth | Supabase MCP | `supabase` CLI |
| GitHub repo | GitHub MCP / web UI | `gh` CLI |

## Install CLIs by OS (CLI mode only)

**macOS (Homebrew):**
```bash
brew install gh
brew install vercel-cli || npm i -g vercel
brew install supabase/tap/supabase
```
**Windows (winget / npm):**
```powershell
winget install GitHub.cli
npm i -g vercel
scoop install supabase   # or: https://github.com/supabase/cli/releases
```
**Linux (Debian/Ubuntu):**
```bash
type -p curl >/dev/null || sudo apt install -y curl
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
sudo npm i -g vercel
# supabase: download the .deb from https://github.com/supabase/cli/releases
```

## Login (student runs these in their own terminal — CLI mode)

```bash
gh auth login          # choose GitHub.com → HTTPS → browser
vercel login           # email / GitHub
supabase login         # opens browser for an access token
```

## Install MCP servers (preferred in both modes)

- **Vercel MCP** and **Supabase MCP** — install via the Claude Code MCP UI / Cowork plugin panel and complete the OAuth.
- In Cowork mode these MCPs ARE the integration (no CLI). In CLI mode they're a nicer alternative to the CLIs.

## Verify

**CLI mode — all four must succeed:**
```bash
gh auth status
vercel whoami
supabase projects list 2>/dev/null | head -3
```
(Lovable has no CLI — it's a web product; the "verify" for Lovable is simply that the student can log in at lovable.dev.)

**Cowork mode:** confirm `mcp__vercel__*` and a Supabase MCP tool appear in the session's available tools. If either is missing, install it via the Cowork MCP UI and re-check.

## Next step

Once verified, return to `m0-landing-and-signin` and start Step 1.
