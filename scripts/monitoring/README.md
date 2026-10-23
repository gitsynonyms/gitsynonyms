# Scheduled monitoring setup

## Why the cron is not running yet

GitHub only runs a scheduled workflow when the workflow file exists on the
default branch under `.github/workflows/`. The automation account that maintains
this repository is a GitHub App installation without the `workflows` permission,
so every attempt to create that file was rejected:

```
remote: refusing to allow a GitHub App to create or update workflow
        `.github/workflows/monitor-whitehouse.yml` without `workflows` permission
API : "Resource not accessible by integration" (HTTP 403)
```

That is why no periodic run has happened: the workflow simply is not there.

## Enable it (one command, needs a token with `workflows`)

```bash
python3 scripts/monitoring/install_workflow.py
git add .github/workflows/monitor-whitehouse.yml
git commit -m "Add the White House video monitor workflow"
git push
```

Or grant the app the `Workflows` permission (Repository settings → GitHub Apps),
after which the same push works unchanged.

Once the file is on `main`, the schedule is `23 */3 * * *` (every three hours),
and `workflow_dispatch` allows a manual run with `force_check` / `dry_run`.

## What a run does

1. Rebuild the proxy index (`proxyindex/`) and rotate one validated proxy.
2. Poll `@WhiteHouse/videos` and `@WhiteHouse/streams` (plus the YouTube Data
   API, Google CSE and C-SPAN when keys are configured).
3. Rank uploads: official channel → event-like title → longest runtime →
   highest available resolution.
4. Verify each new event against the RollCall Factbase calendar for that day,
   GDELT, congressional records, YouTube comments, forum threads and reverse
   links; write `VERIFICATION.json` next to the output.
5. Download all seven caption formats (`vtt`, `srt`, `ttml`, `srv3`, `srv2`,
   `srv1`, `json3`) for both languages, build the ASS from `json3`, burn it in.
6. Regenerate `site/videos.html` plus the per-video pages and deploy to Pages.

Optional secrets: `YOUTUBE_API_KEY`, `GOOGLE_CSE_ID`, `GOOGLE_CSE_API_KEY`,
`GDELT_API_KEY`, `NEWSBANK_API_KEY`. Without them the two official channels and
the Factbase calendar are still polled.

## Manual run without the workflow

The monitor also runs locally:

```bash
MONITOR_PROXY=195.154.191.26:1092 python3 scripts/monitoring/comprehensive_monitor.py --dry-run
python3 scripts/generate_video_pages.py
```
