# White House video monitor

`.github/workflows/monitor-whitehouse.yml` checks the latest entries in
`@WhiteHouse/videos` and `@WhiteHouse/streams` every five minutes, GitHub
Actions' shortest supported schedule interval. GitHub may delay scheduled jobs
during periods of high load.
It reads flat channel listings and ignores individual metadata failures during
discovery, then downloads the selected video's captions and media.

## Proxy full scan (list of lists squared)

The monitor consumes the published result of the full proxy scan. The scan is one
fixed flow, `proxyindex/full_scan.py`: enumerate the Chinese/English synonym
matrix, walk it through the GitHub search API with a resumable cursor, expand
every repository x conventional file path, fetch every source, probe every
candidate (TCP, then a real HTTPS request), re-verify the fastest candidates and
publish the ten fastest to `proxyindex/top10.json`. See
[`proxyindex/README.md`](../../proxyindex/README.md).

Hourly scheduling and the push to `main` live in the tracked workflow source
`scripts/monitoring/proxy-scan.yml`. Files under `.github/workflows` need a
token with the `workflows` permission, so a maintainer installs it with:

```bash
python scripts/monitoring/install_workflow.py all
```

Until it is installed the scan still runs automatically: the five-minute monitor
calls `scripts/select_public_ytdlp_proxies.py`, which reuses any scan younger
than an hour and otherwise runs a bounded full scan in the same job. Publishing
the result set before that is manual:

```bash
python proxyindex/full_scan.py scan --sync
```

The workflow handles one unprocessed upload per run. It downloads both
`zh-Hans` and `en-orig` captions without cookies, burns the bilingual ASS into
an MP4 on the GitHub-hosted Windows runner, uploads the MP4 to a GitHub Release
named for the video ID, generates the archive pages, and deploys the Pages
artifact. It records the source only after the deployment succeeds, so failed
runs can retry.

The scheduled job tries direct access first, then a configured private
`YTDLP_PROXY` secret, followed by at most three proxies re-verified against the
video being downloaded. Those candidates come from the published full list²
scan (`proxyindex/top10.json`, refreshed hourly by `.github/workflows/proxy-scan.yml`)
instead of a hand-picked shortlist; the three legacy public lists are only a
fallback when the scan has published nothing. Scan results younger than one hour
are reused, so the selector does not re-crawl on every five-minute invocation.
Public proxies are untrusted and can observe connection metadata; never send
cookies or credentials through them. Manual video runs can set `video_url` to
process a specific White House video.

The proxy-scan workflow checks the White House `videos` and `streams` feeds for
unprocessed uploads. When `--upload` is enabled, it creates a ZIP containing
the source and processed files, then uploads it anonymously to Filebin,
temp.sh, and GoFile. Filebin advertises a 2 GB file limit and expiry six days
after the last upload to a bin; temp.sh advertises a 4 GB file limit and
three-day retention. GoFile's size and retention policies may change and are
reported as server-defined. Actual upload and download speeds are measured per
run; they depend on runner location and service load. Use this only for public
materials authorized for redistribution; anonymous hosts may remove links
without notice.

Successfully deployed sources are recorded as platform-neutral JSONL entries
in `.github/state/whitehouse-processed-videos.txt`. Records include the source
platform and ID, channel/account, title, full source URL, publication date and
UTC timestamp in a nine-digit fractional format, the upstream timestamp
precision, processing time, publication status, and artifact links. The
nine-digit format does not imply nanosecond-accurate upstream data: precision
is recorded separately, and unavailable publication time remains `null`.
Existing ID-only YouTube entries remain readable and are upgraded when richer
metadata for the same source becomes available.

`workflow_dispatch` can reprocess the latest upload with `force_check`. Manual
runs must target `main` to publish. The workflow requires repository permissions
to write contents and deploy GitHub Pages.

Before publishing, the workflow extracts every video frame in a 30-second
subtitle-dense sample and runs Tesseract OCR. `output/FRAME_QA.json` records
duplicate visible lines, repeated source cues, OCR confidence, minimum text
height, and safe-screen margins. Hard frame-layout or duplicate-text errors
stop publication; exact and cumulative-prefix repeats in source cues and low
OCR confidence are reported as warnings. Rolling cues with a short inter-cue
gap are merged into non-repeating text before burn-in. The check requires
FFmpeg, ffprobe, and Tesseract with English and Simplified Chinese language
data. The Chinese model is downloaded from a pinned upstream revision and
verified against its SHA-256 checksum before use.

`install_workflow.py` copies a tracked workflow source into `.github/workflows/`
and never silently overwrites a workflow that has moved on: without `--force` it
refuses and prints the diff. `monitor` and `proxy-scan` read from this directory;
`process-single-video` reads the archived template in
`.github/workflows-deploy/`. Keep each source and its active workflow in sync, and
remember `all` only installs the two settled sources.

## Module 1: material acquisition

`acquisition.py` is the evidence-gathering layer. It reads a source registry
(official RSS, GDELT, the Factbase calendar, the White House YouTube channels)
and maintains `output/materials/ledger.jsonl` plus a materialised
`manifest.json` for downstream analysis. Each record carries its source, URL,
fetch time, content checksum, revision and a quality grade:

- `A` payload captured and hashed (`--fetch-articles`, `--download`),
- `B` citable reference, no payload,
- `C` rejected as insufficient (`status` stays `rejected` for audit).

The same event seen in two sources is merged once and counted as corroboration
(`details.also_seen_in`, `events[].corroboration`). A dead source is reported
as `unavailable`/`error` and never stops the rest of the run. `sources`,
`collect` and `report` subcommands are documented in the repository README;
`tests/test_acquisition.py` covers the behaviour offline.
