# White House video monitor

`.github/workflows/monitor-whitehouse.yml` checks the latest entries in
`@WhiteHouse/videos` and `@WhiteHouse/streams` every five minutes, GitHub
Actions' shortest supported schedule interval. GitHub may delay scheduled jobs
during periods of high load.
It reads flat channel listings and ignores individual metadata failures during
discovery, then downloads the selected video's captions and media.

The workflow handles one unprocessed upload per run. It downloads both
`zh-Hans` and `en-orig` captions, burns the bilingual ASS into an MP4 on the
GitHub-hosted Windows runner, uploads the MP4 to a GitHub Release named for the
video ID, generates the archive pages, and deploys the Pages artifact. It
records an ID only after the deployment succeeds, so failed runs can retry.
The processed ID list is `.github/state/whitehouse-processed-videos.txt`.

YouTube may require authentication from hosted runners. To enable processing,
add a repository Actions secret named `YOUTUBE_COOKIES_B64` containing the
Base64-encoded contents of an authorized yt-dlp Netscape cookies file. Without
the secret, the five-minute scan continues but skips download, burn-in, and
publishing. When configured, cookies are written only to the runner's temporary
directory and removed after the processing job.

`workflow_dispatch` can reprocess the latest upload with `force_check`. Manual
runs must target `main` to publish. The workflow requires repository permissions
to write contents and deploy GitHub Pages.

`install_workflow.py` copies the tracked workflow template into
`.github/workflows/`; the template and installed workflow should be kept in sync.

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

