# Frappe Build Pipeline — Setup Guide

## The weekly rhythm

| When (IST) | What happens |
|---|---|
| **Sat 00:30** | Auto-check upstream repos. If anything changed, build staging image. Telegram you. |
| **Sat anytime** | You test staging at your leisure. When ready, click "Run workflow" on **Promote to Production** and approve in the GitHub mobile app. |
| **Sun 00:30** | Auto-drain: if you approved, prod gets the image you tested. No build, just a retag+push. |

If you don't approve on Saturday, Sunday's drain finds nothing pending and exits quietly. The staging image stays around in GHCR; you can approve a future Saturday to promote it then. The next Saturday build (if upstream changes again) overwrites what's "latest" in staging.

## File layout

```
your-repo/
├── .github/
│   └── workflows/
│       ├── detect-and-build-staging.yml
│       ├── promote-to-prod.yml
│       └── drain-prod-queue.yml
├── .state/
│   ├── last-build.json          (auto-created)
│   ├── pending-promotion.json   (auto-created when you approve, deleted after drain)
│   └── history/                 (audit trail of past promotions)
├── upstream-apps.json
├── check-upstream.py
└── README.md
```

## One-time setup

### 1. Secrets (Settings → Secrets and variables → Actions)

- **`APPS_JSON`** — full contents of your `apps.json`.
- **`TELEGRAM_BOT_TOKEN`** — from [@BotFather](https://t.me/BotFather): `/newbot`, copy the token.
- **`TELEGRAM_CHAT_ID`** — message your bot, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` — your chat ID is in the response.

### 2. Workflow permissions

Settings → Actions → General → Workflow permissions → **Read and write permissions**.

### 3. Production environment (approval gate)

Settings → Environments → **New environment** → name it exactly `production`. Inside:
- Enable **Required reviewers** → add yourself.

This is what makes the Promote workflow pause until you approve.

### 4. Verify `upstream-apps.json`

The repo paths in this file are best-guesses. Verify each `repo:` field matches the actual GitHub URL of the app you use.

## Why the "drain pattern" instead of `sleep`

GitHub Actions caps jobs at 6 hours. If you approve Saturday morning, the Sunday 00:30 IST push is 16+ hours away — way over the limit. So the system splits approval from execution:

1. **Promote workflow** (Saturday, after you approve) just writes `.state/pending-promotion.json` and exits in ~5 seconds.
2. **Drain workflow** (Sunday 00:30 IST, scheduled) reads that file and does the push.

The pending file is the queue. The cron is the clock.

## Queue correctness scenarios

- **Approve Sat morning → Sun 00:30 drain pushes the image you approved.** ✓
- **Approve Sat morning, change your mind → manually delete `.state/pending-promotion.json` (just edit it out via the GitHub web UI and commit). Sunday's drain finds nothing pending, exits.** ✓
- **Approve twice on Saturday → second approval overwrites the pending file. Sunday's drain promotes the latest. (Both approvals are for the same staging image anyway, since staging only builds Saturday, so this is harmless.)** ✓
- **Don't approve → Sunday's drain exits silently. Image stays in staging. Next Saturday either rebuilds (upstream changed) or doesn't (nothing new). You can approve next Sat to push current staging.** ✓

## The tags you'll have in GHCR

| Tag | What it points to |
|---|---|
| `16-staging-latest` | Most recent staging build (moves every Saturday) |
| `16-staging-YYYYMMDD-N` | Specific staging build (immutable, never overwritten) |
| `16-prod-latest` | Currently live prod (moves every Sunday after a successful promotion) |
| `16-prod-YYYYMMDD-N` | Specific prod release (immutable) |
| `16-prod-backup` | The prod image that was live just before the current one (auto-saved at each promotion) |

## Rolling back prod

If a Sunday promotion turns out to be broken and you want to revert to last week's prod:

```bash
docker pull ghcr.io/<your-user>/custom:16-prod-backup
docker tag ghcr.io/<your-user>/custom:16-prod-backup ghcr.io/<your-user>/custom:16-prod-latest
docker push ghcr.io/<your-user>/custom:16-prod-latest
```

Then redeploy on your server. The `:16-prod-backup` tag always holds the immediately-previous prod, so you have one-step rollback to last week's known-good image without needing to remember any specific version tag.

For older rollbacks (more than one week back), use the immutable `16-prod-YYYYMMDD-N` tags — they're never overwritten, so any past prod release is still pullable as long as you know the date and run number.

## Gotchas

**Forgot to approve, want to push mid-week.** Click "Run workflow" on the **Drain Prod Queue** workflow manually. As long as `.state/pending-promotion.json` exists, it'll push. If it doesn't exist, run **Promote to Production** first to create it (you'll need to approve again), then run drain.

**Force a staging build.** Run staging workflow manually with `force_build=true`.

**First run.** No `.state/last-build.json` yet, so everything counts as new. Staging builds normally.

**Editing watched apps.** Edit `upstream-apps.json`, commit, push. Next Saturday's run uses the new config. If you remove an app, also remove it from `apps.json` (the build secret).

## Costs

Public repo: free. Private repo: 2000 min/month free. Weekly schedule = ~4 builds/month × 20 min = 80 min/month. Very cheap.
