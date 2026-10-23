---
name: whitehouse-video-publish
description: "Process an authorized White House YouTube video end to end: download bilingual captions, render and publish the video and Pages archive, upload anonymous mirrors, and verify GitHub Actions results. Use when asked to test, publish, mirror, or deploy a White House video."
argument-hint: "YouTube video URL"
user-invocable: true
---

# White House Video Publish

Use the repository's tracked workflows as the canonical implementation. Do not
copy pipeline code into this skill or replace the existing processing scripts.

## Procedure

1. Confirm the input is a YouTube URL and that processing and redistribution
   are authorized. The manual monitor workflow validates that the video belongs
   to the verified White House channel.
2. Confirm `gh` is authenticated, the worktree is safe, and the current `main`
   contains the installed workflows:
   - `.github/workflows/monitor-whitehouse.yml` creates bilingual subtitles,
     renders the video, publishes its GitHub Release and Pages archive, then
     records the video after deployment succeeds.
   - `.github/workflows/proxy-scan.yml` runs a proxy scan and uploads the
     finished bundle to Filebin, temp.sh, and GoFile.
3. Dispatch both workflows against `main` with the same URL. Avoid optional
   caption-provider comparison unless specifically requested. For a test run,
   use `force_check=false`, `sync=false`, and low scan budgets so the run does
   not publish proxy-scan changes unrelated to the video.
4. Keep the returned Actions run IDs. Monitor both runs and inspect the failing
   step logs if either fails. Do not report success while a run is queued,
   running, cancelled, or failed.
5. Verify the monitor workflow completed its Release, Pages deploy, and
   mark-processed jobs. Verify the mirror workflow completed its download and
   mirror step, artifact report, and the configured minimum number of
   successful hosts. Report the actual mirror links and deployed Pages URL
   from run outputs; never invent URLs or success states.
6. If a run fails, identify the concrete cause and fix the tracked workflow or
   script, validate it, install the workflow if necessary, and rerun the
   affected workflow. Do not repeatedly dispatch an unchanged failing run.

## Dispatch commands

Replace `VIDEO_URL` with the requested, authorized video URL:

```bash
gh workflow run monitor-whitehouse.yml --ref main \
  -f video_url=VIDEO_URL -f force_check=false

gh workflow run proxy-scan.yml --ref main \
  -f video_url=VIDEO_URL -f sync=false \
  -f query_budget=1 -f probe_budget=1 -f budget_seconds=60 \
  -f upload_min_ok=2
```

Each workflow produces a distinct run. A successful Pages run is not evidence
that mirror uploads succeeded, and a successful mirror run is not evidence that
Pages deployment succeeded.
