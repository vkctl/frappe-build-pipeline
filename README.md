# Frappe Build Pipeline — Setup Guide

## The weekly rhythm

| When (IST) | What happens |
|---|---|
| **Sat 00:30** | Auto-check upstream repos. If anything changed, build a new image (`16-build-DATE-N`), point `16-staging-latest` at it, deploy to staging, Telegram you. |
| **Sat anytime** | You test staging. When ready, click **Run workflow** on **Promote to Production** and approve in the GitHub mobile app. Workflow checks staging ≠ prod (skips if already same), writes `.state/pending-promotion.json`. |
| **Sun 01:00** | Auto-drain: if approved, move `16-prod-backup` → current prod, move `16-prod-latest` → the approved build image. No image data copied — pure registry pointer updates. |
| **Sun 02:30** | Auto-cleanup: delete old `16-build-*` images beyond the last 4, protecting anything currently referenced by a pointer tag. |

If you don't approve on Saturday, Sunday's drain finds nothing pending and exits silently. The next Saturday build overwrites `16-staging-latest` if upstream changed.

## File layout

```
your-repo/
├── .github/
│   └── workflows/
│       ├── detect-and-build-staging.yml   # Saturday build
│       ├── promote-to-prod.yml            # Manual approval + queue
│       ├── drain-prod-queue.yml           # Sunday pointer move
│       └── cleanup-old-tags.yml           # Sunday image pruning
├── .state/
│   ├── last-build.json                    (auto-created after each build)
│   ├── pending-promotion.json             (created on approval, deleted after drain)
│   └── history/                           (audit trail of past promotions)
├── upstream-apps.json
├── check-upstream.py
├── generate-apps.py
├── cleanup-old-tags.py
└── README.md
```

## One-time setup

### 1. Secrets (Settings → Secrets and variables → Actions)

- **`TELEGRAM_BOT_TOKEN`** — from [@BotFather](https://t.me/BotFather): `/newbot`, copy the token.
- **`TELEGRAM_CHAT_ID`** — message your bot, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` — your chat ID is in the response.
- **`DOKPLOY_SSH_HOST`**, **`DOKPLOY_SSH_USER`**, **`DOKPLOY_SSH_PRIVATE_KEY`** — SSH access to your deploy proxy.
- **`DOKPLOY_STAGE_WEBHOOK_URL`**, **`DOKPLOY_PROD_WEBHOOK_URL`** — Dokploy redeploy webhook URLs.

### 2. Workflow permissions

Settings → Actions → General → Workflow permissions → **Read and write permissions**.

### 3. Production environment (approval gate)

Settings → Environments → **New environment** → name it exactly `production`. Inside:
- Enable **Required reviewers** → add yourself.

This is what makes the Promote workflow pause until you approve in the GitHub UI or mobile app.

### 4. Verify `upstream-apps.json`

Check each `repo:` field matches the actual GitHub repository of the app you use.

## Why the "drain pattern" instead of `sleep`

GitHub Actions caps jobs at 6 hours. If you approve Saturday morning, the Sunday 00:30 IST push is 16+ hours away. So the system splits approval from execution:

1. **Promote workflow** (Saturday, after you approve) just writes `.state/pending-promotion.json` and exits in seconds.
2. **Drain workflow** (Sunday 00:30 IST, scheduled) reads that file and moves the prod pointer.

The pending file is the queue. The cron is the clock.

## Rolling window — how images are managed

There is one pool of immutable build images and three moving pointer tags:

| Tag | What it is |
|---|---|
| `16-build-YYYYMMDD-N` | Immutable build image (one per Saturday build). Never overwritten. |
| `16-staging-latest` | Pointer → most recent build. Moves every Saturday. |
| `16-prod-latest` | Pointer → currently live prod build. Moves every Sunday after approval. |
| `16-prod-backup` | Pointer → the build that was in prod just before the current one. Free rollback. |

On promotion, no image data is copied. The drain workflow simply moves `16-prod-backup` to where `16-prod-latest` currently points, then moves `16-prod-latest` to the approved build. Both are pure registry metadata operations (milliseconds, no layer transfer).

Cleanup keeps the last 3 build images. Any build currently referenced by `16-staging-latest`, `16-prod-latest`, or `16-prod-backup` is always protected regardless of age.

## Efficiency safeguards

**Hash-based catchup check (Saturday build):** Before building, the staging workflow compares the GHCR manifest digest of `16-staging-latest` vs `16-prod-latest`. If they match (prod already has the latest staging image), it skips the build and reminds you to promote first.

**Duplicate promotion skip (Promote + Drain):** Both the promote and drain workflows compare digests before doing any work. If staging and prod already point at the same image, they skip and send a Telegram notification instead of doing a no-op promotion.

## Queue correctness scenarios

- **Approve Sat → Sun 01:00 drain promotes the build you approved.** ✓
- **Change your mind after approving → delete `.state/pending-promotion.json` via the GitHub web UI and commit. Sunday's drain finds nothing, exits.** ✓
- **Approve twice → second approval overwrites the pending file. Both approvals reference the same build image anyway.** ✓
- **Don't approve → drain exits silently. Next Saturday either rebuilds (upstream changed) or skips (nothing new). You can approve any future Saturday to push current staging.** ✓
- **Staging already matches prod → promote workflow detects matching digests and skips queuing. Drain does the same check as a second guard.** ✓

## Rolling back prod

If a promotion turns out to be broken, rollback is a single pointer move using `docker buildx imagetools`:

```bash
docker buildx imagetools create \
  --tag ghcr.io/<owner>/frappe_stack:16-prod-latest \
  ghcr.io/<owner>/frappe_stack:16-prod-backup
```

Then trigger a Dokploy redeploy. `16-prod-backup` always holds the immediately-previous prod build — one command, no digging through version history.

For older rollbacks, use any `16-build-YYYYMMDD-N` tag still in GHCR (cleanup keeps the last 3 builds).

## Gotchas

**Forgot to approve, want to push mid-week.** Run **Promote to Production** (you'll need to approve again), then either wait for Sunday's drain or run **Drain Prod Queue** manually.

**Force a staging build despite no upstream changes.** Run the staging workflow manually with `force_rebuild=true`.

**First run.** No `.state/last-build.json` yet — everything counts as new. Staging builds normally.

**Editing watched apps.** Edit `upstream-apps.json`, commit, push. The next Saturday run picks up the changes automatically.

## Costs

Public repo: free. Private repo: 2000 min/month free. Weekly schedule ≈ 4 builds/month × ~20 min = ~80 min/month.
