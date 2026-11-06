# Bruce Power 2828 alerts

Checks https://2828.brucepower.com about every 5 minutes and sends a phone notification through [ntfy](https://ntfy.sh) when the direction for Category D staff changes.

This is a personal convenience tool, not an official Bruce Power channel. The site and your supervisor remain the source of truth, and temporary recategorization can apply.

## Notifications

| When | Priority | Title |
|---|---|---|
| Category D is told not to report | urgent | Category D: do not report to site |
| That direction's wording changes | urgent | Category D direction updated |
| Direction ends | default | Back to normal |
| Category D wording isn't recognized, or the page has no Category D section | high | Check 2828: unclear direction |
| Page unreadable for 3 checks in a row | high | Checker can't read 2828 |
| Page readable again after that | default | Checker recovered |
| Weekly | low | Still watching 2828 |

If the weekly message stops arriving, assume the checker has stopped.

## Setup

1. Install the ntfy app on your phone and subscribe to your topic name.
2. Save the same topic name as the `NTFY_TOPIC` repository secret. Treat it like a password, because anyone who has it can read and send messages on that topic.
3. Deploy `worker/worker.js` as a Cloudflare Worker with a `*/5 * * * *` cron trigger and two secrets: `NTFY_TOPIC`, and `GITHUB_TOKEN` (a fine-grained token for this repo only, with Actions read and write). GitHub's own schedule stays on as a backup, but it can run hours apart.

If the Worker can't start the checker, it sends "2828 timer can't start the checker" at most once an hour. A 401 in that message means the token has expired and needs replacing in Cloudflare.

## Common tasks

- **Pause:** Actions tab → Check 2828 → `...` → Disable workflow.
- **Run now:** `gh workflow run check.yml`
- **Pause the timer too:** Cloudflare dashboard → the Worker → Settings → Trigger Events → delete the cron trigger.
- **Run tests:** `py -m unittest`, and `node --test worker/worker.test.mjs` for the Worker
- **Preview without sending:** `py check.py --dry-run --file fixtures/feb2026_restricted.html --state preview.json`

## Limits

- Alerts can still lag the site by several minutes. Check the page directly if conditions look bad.
- Failed checks are not retried immediately. The next one runs on schedule.
