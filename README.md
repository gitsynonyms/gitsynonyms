# gitsynonyms

`gitsynonyms` is a tiny Git command lookup tool focused on common aliases and close synonyms.

> **最高指示**：本仓库的组织与巡检制度以 [`DIRECTIVE.md`](DIRECTIVE.md) 为最高效力
> 文件；登记表为 [`inspection/registry.json`](inspection/registry.json) 与
> [`resources/registry.json`](resources/registry.json)，执行入口分别为
> `python scripts/inspection/plan.py check --github` 与
> `python scripts/resources/check.py check`。任何 agent、账号、主机或
> harness 在开始工作前必须先读该文件。

## 组织与登记表

`DIRECTIVE.md` 是规定根本层级、权责原则的宪法性最高指示；
`organization/` 是依其制定的具体组织规章制度。

| 司 / 部 / 处 | 档案或登记表 | 校验入口 |
|---|---|---|
| 资源获取分析司 | `resources/registry.json`：钦定 White House 直播/视频频道与支撑来源，一条 URL 一条记录 | `python scripts/resources/check.py check` |
| 定期巡检司 / 巡检任务执行方案处 | `inspection/registry.json`：每个最小可巡检单元一条记录 | `python scripts/inspection/plan.py check --github` |
| 模块2：视频处理部 | `organization/module-2-video-processing.md`：素材接收、字幕时间、字幕区、刻录渲染、质检交接五个职责单元 | 以该规章中的实现状态和精度限制为准 |

```bash
python scripts/resources/check.py list        # 钦定与支撑资源
python scripts/resources/check.py coverage    # 钦定资源覆盖率
python scripts/inspection/plan.py plan        # 下一轮到期巡检单元
```

## Install

```bash
pip install .
```

## CLI usage

```bash
gitsynonyms status
gitsynonyms st
gitsynonyms --list
gitsynonyms checkout pull
gitsynonyms --json checkout
```

## Last30Days independent demo

The static page at [`site/last30days/`](./site/last30days/) demonstrates the upstream Last30Days skill independently, then reads the latest archived White House YouTube video and its English captions as a selectable research starting point. It generates skill invocation commands; it does not run research in the browser or connect the skill to the monitoring workflow.

## Python API

```python
from gitsynonyms import find_synonyms, lookup

find_synonyms("status")
# ['status', 'st', 'info']

lookup("st")
# ['status', 'st', 'info']
```

## YouTube subtitle export

```bash
gitsynonyms-youtube --url "https://www.youtube.com/watch?v=IySF_8OArvw" --output bilingual.srt
```

By default, this reads YouTube's auto-generated English track through yt-dlp, preferring `en-orig` over `en`; it uses a manual English track only when no matching automatic track exists. It translates each cue to Chinese and writes a bilingual SRT. To compare with the separate `youtube-transcript-api` source, pass `--source transcript-api`. The selected source is explicit: the command reports missing subtitle tracks and translation errors instead of silently substituting another source or copying untranslated English into the Chinese line.

## Organized YouTube subtitle archive

```powershell
gitsynonyms-youtube-archive --url "https://www.youtube.com/watch?v=IySF_8OArvw"
```

The command creates `output/YYYY-MM-DD_event-title_VIDEO-ID/` with separate English, Chinese, and bilingual SRT files. Filenames label the source as `manual`, `auto-generated`, or `auto-translated`; a YouTube transcript marked as manual is not independently verified as proofread. If no Chinese subtitle or YouTube auto-translation is available, the command reports an error rather than writing incorrect content. Use `--event`, `--date`, or `--output-dir` to override the folder metadata or archive location.

The organized archive command uses `yt-dlp` to list and download YouTube manual/automatic caption tracks, including YouTube-generated translations such as `zh-Hans`.

### One-command English + Chinese + bilingual SRT

```bash
gitsynonyms-youtube-srt --url "https://www.youtube.com/watch?v=IySF_8OArvw"
```

This prefers the automatic English original (`en-orig`) and YouTube's Simplified Chinese auto-translation (`zh-Hans`), waits 56 seconds before requesting the Chinese track, then creates an event/date/video-ID folder containing separately labeled English, Chinese, and time-aligned bilingual `.srt` files. Override track codes, wait time, output root, event title, or date with `--english`, `--chinese`, `--sleep-subtitles`, `--output-dir`, `--event`, and `--date`. Availability and access depend on YouTube and the network; no downloader can guarantee every video is accessible.

The GitHub Actions workflow defaults to the latest White House channel upload. Run **Download YouTube subtitles** manually to resolve the channel's latest video in the cloud and upload its English, Chinese, and bilingual SRTs as a downloadable Actions artifact. YouTube requests try a direct connection first. If that fails, the downloader retries through the single authorized proxy configured as the repository Actions secret `YTDLP_PROXY`; when the secret is absent, it reports the failure instead of selecting untrusted public proxies. To run the complete archive pipeline for a specific White House video as a new upload, manually run **Monitor White House Videos** and set its `video_url` input to the video URL; for example, `https://www.youtube.com/watch?v=HEWlaWY9Qcs`. The workflow fetches and validates metadata, downloads the best available media and subtitles without cookies, burns in bilingual captions, publishes the release asset and archive page, and deploys Pages.

The scheduled White House monitor runs without YouTube cookies. Its proxy pool now comes from the **full list² scan** (see [全量代理扫描](#全量代理扫描全量--测速--同步-main)): the scan enumerates the whole Chinese/English synonym matrix, expands every repository × conventional file path, probes every candidate and publishes the ten fastest verified proxies to `proxyindex/top10.json`. The monitor re-probes those published candidates against the exact video it is about to download, keeps the three fastest, and only falls back to the three legacy public lists when the scan has published nothing. A public candidate that fails during the actual request is skipped in favor of the next one; list fetching and probing are not repeated per yt-dlp request. Public proxy operators can observe the destination and timing of connections; never send cookies or credentials through these public proxies.

Successfully deployed sources are appended to `.github/state/whitehouse-processed-videos.txt` as one JSON object per line. The ledger is platform-neutral: it records source platform and ID, channel/account, title, canonical full source URL, publication date and normalized UTC timestamp with nine fractional digits, the timestamp's actual source precision, record time, publication status, and artifact links. Nine digits provide a stable nanosecond field shape; the precision field records whether the upstream metadata was only accurate to a day, second, or finer. Existing ID-only YouTube lines remain readable and are migrated to structured entries the next time a source is recorded.

### Local standalone script (no Python environment changes)

Run from any Windows PowerShell; the command locates Anaconda under the current user's profile with `$env:USERPROFILE`, without requiring an activated environment or hard-coding a username:

```powershell
& (Join-Path $env:USERPROFILE "anaconda3\python.exe") .\scripts\download_youtube_bilingual_srt.py "https://www.youtube.com/watch?v=IySF_8OArvw" --languages zh-Hans,en-orig --output-dir "D:\MyDownload"
```

The downloader retains every caption format YouTube publishes for both languages and writes aligned bilingual `.vtt`, `.srt`, and `.ass` files. Each language is requested as `vtt`, `srt`, `ttml`, `srv3`, `srv2`, `srv1`, and `json3`; the downloaded files are kept byte-for-byte as yt-dlp writes them. The bilingual VTT preserves both language lines in each merged cue; the bilingual ASS applies the selected visual style. GitHub Actions uploads all outputs as an artifact.

The script itself uses only the Python standard library and the `yt-dlp` command already available on `PATH`. It prints each processing step and streams yt-dlp's wait/download progress directly to PowerShell, rather than hiding output until completion. It downloads the second language track first; English is requested immediately, while the Chinese track waits 56 seconds by default, and that wait is applied before every format request of the delayed track. Every downloaded caption file is kept unchanged as the original single-language track; the bilingual SRT and ASS are additional outputs. The merge and the ASS read the track format that carries the most precise timing, in the order `json3`, `srv3`, `srv2`, `srv1`, `vtt`, `ttml`, `srt`; `json3`, `srv3` and `srv2` expose YouTube's own per-word offsets, so the English karaoke highlight follows the spoken word instead of a uniform estimate. VTT cue timings are parsed, and inline word timestamps are used for ASS cut points when available. The bilingual SRT pairs cues whose end times match (to the millisecond), even if their start times differ, placing Chinese and English on separate lines in one block. A newer cue replaces an overlapping older cue in the same language, so each language contributes at most one line and rolling captions are not accumulated. Unpaired cues remain time-aligned at their source boundaries; unrelated cues are not paired merely because they are nearest in time. Original source text and markup remain unchanged in the downloaded single-language files. ASS output groups connected rolling cues in each language and reconstructs their unique text. When Chinese and English rolling groups have matching source cue boundaries, both use those shared boundaries and keep each corresponding cue's full text together instead of independently splitting by words or characters. Unpaired groups use a uniform word-rate estimate and split at later cue starts by rounding to the nearest token; if a preceding cue ends at that split, its text is prefixed to the first segment. This fallback is a coarse estimate, not YouTube-provided word timing. CJK text is placed above Latin text regardless of source track order, centered in the caption area, and reduced to fit when needed. ASS English text uses karaoke timing to highlight words in sequence, evenly distributing each caption interval's duration across its English words. The homepage reads the one shared settings record managed by the subtitle settings page rather than presenting duplicate style controls. The `classic` preset uses vivid gold Chinese (`#FFC928`) above white English; Chinese font size is 1.2 times English, with bold text and a soft shadow. English words highlight in bright cyan (`#00D7FF`) as they are spoken, clearly distinguishing the active English word from the gold Chinese line. The selectable `cinema` preset uses a sample-like serif style with English above Chinese in warm ivory. The background box is always visible from video time zero through the full video duration, including caption gaps, to cover persistent source banners. Generation stops with an explicit error if YouTube does not provide the video duration, since a shorter fallback cannot guarantee full-video coverage. `--box-style glass-diamond` gives a translucent, faceted glass-like look (an ASS-compatible visual treatment, not actual background blur); `--box-style presidential-blue` uses the user-selected color `#0F1647` (RGB 15, 22, 71), matching the blue listed for the U.S. presidential flag (1959–1994) in [Flag Color Codes](https://www.flagcolorcodes.com/president-of-the-united-states-of-america-usa-1959-1994). The default geometry remains `(66,883)`–`(1852,1022)` on a `1920x1080` canvas and scales proportionally with the frame. Use `--box-x`, `--box-y`, `--box-width`, and `--box-height` to customize position and size; `--box-color` and `--box-opacity` override the selected style. Pass `--chinese-font`, `--english-font`, `--english-font-size`, `--chinese-font-scale`, `--chinese-color`, and `--english-color` to customize typography. To retain the downloaded video and create a hard-subtitled MP4, add `--burn-in`; the source video is retained in the output folder alongside the rendered MP4. Pass `--source-video PATH` to burn the subtitles into a video that is already on disk instead of downloading it again. Pass `--local-file LANGUAGE=PATH` to build one language from a caption file you already have, for example a translation produced by `gitsynonyms-youtube`, when YouTube rate-limits that language's track. A local Intel Quick Sync example is `--burn-in --video-encoder h264_qsv --global-quality 1`; it requires an FFmpeg build with `h264_qsv` and compatible hardware. Use `yt-dlp --list-subs URL` to see the video's language codes; choose any two listed codes.

Each run also saves a bilingual `.json` provenance manifest containing the sanitized yt-dlp video metadata, subtitle-track availability, downloaded cue counts, and SHA-256 hashes of both original cue-source files. Before rendering, the script cross-checks the JSON video identity and duration against the requested video and verifies each retained source file against its parsed cues. Original downloaded caption files remain unchanged; generated bilingual cues are clipped at the video duration, and the monitor's fused ASS and precision WebVTT apply the same bound while recording clipped and discarded cue counts in `TIMING.json`. The script then checks that generated SRT and VTT text and cue boundaries match to the millisecond, validates the ASS event structure and duration, and—when burning—uses `ffprobe` to verify that the MP4 has a video stream and retains the source duration. Raw yt-dlp metadata URLs are not copied into the manifest. WebVTT and SRT keep millisecond cue boundaries; ASS timestamps are limited by the ASS format to centiseconds (10 ms), and the burned MP4 is additionally quantized to video frame boundaries, so 1 ms burn-in precision cannot be guaranteed.

If YouTube access requires an authorized network proxy, set `YTDLP_PROXY` to its proxy URL before running the standalone script. For GitHub Actions, add the URL as the repository Actions secret `YTDLP_PROXY`; the workflow passes it through the environment rather than a dispatch input, and yt-dlp error text redacts the configured URL. Do not use an untrusted public proxy or put proxy credentials in a command-line workflow input.

The VTT parser tolerates a blank line between a cue's timing header and its caption text, and snaps inline word timestamps that YouTube rounds up to 10 ms past a cue's end onto that cue boundary; timestamps further outside their cue still stop generation with an explicit error. For ASS output only, if inline word timings show that a caption's final word ends more than three seconds before the cue end, the ASS cue is shortened to half a second after that word; matching Chinese and English cues share the same shortened end. The downloaded source VTT and bilingual SRT timings are not changed by this ASS-only correction. When word-timed ASS cues overlap, each earlier cue now ends at the next cue's start instead of rendering both captions together; this prevents stale JSON3 text from being appended to the replacement caption.

ASS preserves source cue start times and gaps without lead-in or gap filling. When the source VTT contains inline word timestamps, karaoke switches each complete English word to its timestamp. Without word timestamps, the script retains an even text-based estimate; subtitle text alone cannot recover exact pronunciation timing. See the [BBC timing guidance](https://www.bbc.co.uk/accessibility/forproducts/guides/subtitles/#Timing) and [Aegisub ASS override tag reference](https://aegisub.org/docs/latest/ass_tags/).

Burn-in uses a single FFmpeg filter invocation; all typography, color, subtitle placement, and adaptive black-box styling stays in the ASS:

```powershell
ffmpeg -i "a.mp4" -vf "subtitles='transcript.ass'" -c:v h264_qsv -global_quality 1 -c:a copy "output_with_subtitle.mp4"
```

The reusable YouTube subtitle workflow skill is in `.github/skills/youtube-bilingual-srt/SKILL.md`.

### GitHub Pages subtitle web app

After GitHub Pages is enabled with **GitHub Actions** as the source, the web app is available at `https://gitsynonyms.github.io/gitsynonyms/`. Paste a YouTube link and choose either local Anaconda or a GitHub-hosted runner. Local mode generates a PowerShell command that locates Anaconda under `$env:USERPROFILE\anaconda3`, without requiring environment activation or hard-coding a username. Select either the gold Chinese-first style or the sample-like serif English-first style, then customize fonts, colors, and sizes. The settings page also offers day, night, and paper-like reading palettes, with calculated text/background contrast ratios; these are configurable suggestions, not medical claims about eye strain. Choose whether the subtitle background remains for the whole video (to cover a persistent original banner) or appears only with captions (for videos without a banner), and select a glass-diamond visual treatment or the user-selected presidential-blue color. Box color, opacity, position, and dimensions are editable. Check the burn-in option to download the source video and render a hard-subtitled MP4; local mode uses Intel Quick Sync (`h264_qsv`, quality 1), so the machine must have compatible hardware and FFmpeg support. The cloud workflow uses software encoding. Runner mode generates a `gh workflow run` command; run `gh auth login` first if needed, or open Actions and manually start the workflow. No token is pasted into or stored by the page. Cloud jobs require a GitHub account with permission to run the repository workflow.
The settings page also provides instant cross-page synchronization, a subtitle preview that follows the configured position and size, and a warm-gold plaque palette inspired by the supplied example. The comfort palette choices include the day, night, paper, and warm-gold combinations (the warm-gold pair has a 9.83:1 WCAG contrast ratio); selecting one applies a dedicated solid-color background and matching Chinese/English text colors.

### 最新成片

`https://gitsynonyms.github.io/gitsynonyms/latest.html` 展示最近一次刻录的成片：540p 预览直接在页面播放，1080p 硬字幕 MP4 作为 GitHub Release 资源（标签 `roundtable-2026-10-21`）提供下载，双语 ASS/SRT/VTT 跟随站点发布。Release 资源不适合作为页面内嵌播放源（GitHub 以附件形式返回），因此预览文件保持在 100 MB 以内并提交到 `site/media/`，符合 GitHub 单文件限制与 Pages 站点 1 GB 上限。

### Module 2: processing an already-selected video

Module 2's formal five-unit organization and responsibility boundaries are
archived in [`organization/module-2-video-processing.md`](organization/module-2-video-processing.md).

`scripts/video_processing.py` is the video-processing department. It starts
after the video and its subtitles have been selected, never downloads anything,
and turns exactly those files into a publishable burn-in. It accepts a selection
manifest or explicit paths:

```bash
python3 scripts/video_processing.py --selection selection.json
python3 scripts/video_processing.py \
  --video work/TLH3gXrPCTU.source.mp4 \
  --subtitle output/2026-10-21_.../bilingual_Chinese-English.ass \
  --video-id TLH3gXrPCTU --event-date 2026-10-21 \
  --output-dir output/2026-10-21_... --preview --poster
```

A manifest either points at an archive folder that holds YouTube's published
caption formats (preferred) or at one prepared subtitle file. Relative paths
resolve against the manifest directory first and the current directory second:

```json
{
  "video_id": "TLH3gXrPCTU",
  "title": "President Trump Participates in a Roundtable, Oct. 21, 2026",
  "event_date": "2026-10-21",
  "video": "../work/TLH3gXrPCTU.source.mp4",
  "caption_dir": "output/2026-10-21_...",
  "languages": ["zh-Hans", "en-orig"],
  "output_dir": "output/2026-10-21_...",
  "preview": true,
  "poster": true
}
```

For the same job without a manifest:

```bash
python3 scripts/video_processing.py \
  --video work/5YerQHGsX04.source.mp4 --video-id 5YerQHGsX04 \
  --caption-dir output/2026-10-19_... \
  --languages zh-Hans,en-orig --caption-box auto \
  --output-dir output/2026-10-19_... --preview --poster
```

#### Seven-format timing fusion (subtitle timing office)

With `caption_dir`, module 2 reads every published format for both languages
(`vtt`, `srt`, `ttml`, `srv3`, `srv2`, `srv1`, `json3`) and fuses them:

- `json3` is the primary per-word source (`tStartMs` + `tOffsetMs`). `srv3` and
  `srv2` repeat the offsets as XML attributes and corroborate them; `srv1`,
  `ttml`, `vtt`, and `srt` contribute cue boundaries. Cue boundaries use a
  weighted median across all matching formats; word starts keep the json3
  value and use the other formats only to fill gaps, because the XML formats
  often group several words into one segment. `srv3` offsets are relative to
  their paragraph (the parser previously treated them as absolute).
- The fused timeline is kept in **integer nanoseconds**. YouTube's sources are
  at best 1 ms, so the values are exact multiples of that resolution instead
  of invented precision; integer math keeps the medians and sums lossless.
- `TIMING.json` stores that nanosecond model with every cue, word, source list,
  and disagreement report, plus the measured quantization error.
- A per-language `*.precision.vtt` sidecar writes the same timeline at 1 ms,
  the highest resolution a normal subtitle file preserves.
- The styled ASS is generated from the fused tracks. **ASS cannot represent
  sub-centisecond time**: libass accepts exactly two fractional digits, and we
  verified at 1000 fps that `0:00:01.234` (or more digits) makes the entire
  event disappear. The ASS therefore rounds every start/end to the nearest
  10 ms; the maximum error is 5 ms and the measured error is recorded in
  `BURN.json` and the ASS header. Video frames themselves are 33–40 ms at
  25/30 fps, so this is also below the visible frame interval.

| Representation | Resolution | Purpose |
|---|---|---|
| `TIMING.json` | 1 ns integers | lossless fused model and provenance |
| `*.precision.vtt` | 1 ms | portable precise subtitles |
| `bilingual_precise_*.ass` | 10 ms | styled burn-in (libass limit) |
| burned MP4 | 33–40 ms | 25/30 fps frame timing |

#### Third-party caption comparison

When API keys are available, the same audio can be sent to AssemblyAI,
Deepgram, Gladia, Rev AI, ElevenLabs Scribe v2, and OpenAI Whisper API. Each
provider's word-level transcript is normalized to cues, aligned with a supplied
reference, and scored:

- Word Error Rate (WER) after lower-casing/punctuation removal
- median and P95 absolute word-start offset
- cue CPS, line length, duration, and gaps
- estimated cost and provider execution status

Otter.ai and other services without a usable raw word-timestamp API are recorded
as `unsupported`; missing API keys are `skipped`, not successful comparisons.
Actual audio is sent to external providers only when the operator configures
their credentials and runs the comparison. The five sub-offices, source
register, study protocol, and known limits are documented in
[`organization/subtitle-office-research.md`](organization/subtitle-office-research.md).

#### Speech model research

`scripts/speech_model_research.py` downloads the Hugging Face Open ASR
Leaderboard (English short-form) CSV snapshot and ranks models by WER and
RTFx. Open-weight filter (`--open-weights`) removes proprietary services.
The snapshot lives in `docs/subtitles/data/open_asr_english_short_latest.csv`
and can be refreshed with `--refresh`.

The ASR research register documents ten public ranking, catalog, or benchmark
sources and distinguishes their metrics; the HF English leaderboard is the
only one currently refreshed automatically by this repository. Do not merge
their raw ranks into a single score or infer Chinese performance from an
English-only leaderboard.

#### Caption box or clean text

`--caption-box auto` samples the caption band (plus an equally tall control
band above it) from the video, measures pixels that are both temporally static
and high-gradient, and treats that pattern as a persistent banner. A banner
gets the **semi-transparent presidential-blue** box (`--box-opacity`, default
0.85); a clean source gets no box, so the subtitles sit directly on the picture
with their outline and shadow. `--caption-box on` and `off` force either
choice. The decision, metrics, thresholds, and sampled region are recorded in
`BURN.json`; a failed detection falls back to the box so a banner can never
make the captions unreadable. Long videos are sampled from keyframes only.

#### Subtitle ergonomics office (human-factor compliance)

`scripts/subtitle_ergonomics.py` validates the generated ASS against published
human-factor standards:

- **Netflix** (English 20/17 CPS, Simplified Chinese 9/7 CPS; 42/16 chars/line;
  min 20 frames, max 7 s; 2-frame gap)
- **BBC/Ofcom** (160–180 WPM ceiling; >200 WPM hard to follow)
- **WCAG 2.x** contrast ≥4.5:1 for text vs. background (computed from ASS
  `PrimaryColour`/`BackColour` with alpha)
- **Layout zones** (action safe, title safe, subtitle, information/chyron,
  logo corners, face region) scaled from the 1920×1080 ASS canvas to the
  actual frame
- **Typography metrics** parsed from the ASS style table: font, size (% of
  frame height), Chinese/English ratio, outline/shadow, caption box colour &
  opacity
- **Children preset** (`--children`): English 17 CPS, Chinese 7 CPS

Outputs `ERGONOMICS.json` with per-cue measurements, summary statistics, and
violation list. Modes: `--ergonomics off|warn|strict` (strict fails the burn
on any error). Reports include per-line CPS, line length, duration, gap, and
zone overlap.

The rest of the processing:

1. probes the video with `ffprobe` and rejects files with no video stream or no
   duration;
2. inspects the generated subtitle, counts cues, reports first/last cue and
   coverage, and warns when the captions run past the end of the video
   (`--strict` makes that an error);
3. runs the ergonomics office, records its report in `BURN.json`;
4. burns the subtitle in a single `subtitles=` libass pass with libx264, audio
   copied when the container allows it and re-encoded to AAC when an MP4 target
   cannot carry the source codec. The render goes to a temporary `.part` file
   and is moved into place only after its duration is verified;
5. optionally renders a 540p preview (H.264/AAC, for the Pages site) and a
   poster frame;
6. writes `BURN.json` next to the output with the status (`burned`, `reused`,
   `dry-run`, or `failed`), input/output SHA-256 hashes, stream metadata, cue
   statistics, timing sources, banner metrics, font substitutions, ergonomics
   summary, and the exact FFmpeg command. The publishing module consumes this
   report instead of re-probing the files.

Without `caption_dir`, the module burns the one prepared subtitle file that was
supplied; in that mode the ASS layout (including any box) is already embedded
and `--caption-box` does not alter it.

Existing fresh outputs are reused unless `--force` is passed, so a retry does
not re-encode a long video. `--dry-run` performs every validation, samples the
caption band, and writes the generated subtitle hand-off, but records the
planned render commands instead of encoding the video. The command exits `0`
for `burned`/`reused`/`dry-run` and `1` for any validation or FFmpeg failure; a
failed run still writes `BURN.json` with `"status": "failed"` and the error, and
leaves neither a partial MP4 nor a `.part` file behind.
The rest of the processing:

1. probes the video with `ffprobe` and rejects files with no video stream or no
   duration;
2. inspects the generated subtitle, counts cues, reports first/last cue and
   coverage, and warns when the captions run past the end of the video
   (`--strict` makes that an error);
3. runs the ergonomics office, records its report in `BURN.json`;
4. burns the subtitle in a single `subtitles=` libass pass with libx264, audio
   copied when the container allows it and re-encoded to AAC when an MP4 target
   cannot carry the source codec. The render goes to a temporary `.part` file
   and is moved into place only after its duration is verified;
5. optionally renders a 540p preview (H.264/AAC, for the Pages site) and a
   poster frame;
6. writes `BURN.json` next to the output with the status (`burned`, `reused`,
   `dry-run`, or `failed`), input/output SHA-256 hashes, stream metadata, cue
   statistics, timing sources, banner metrics, font substitutions, ergonomics
   summary, and the exact FFmpeg command. The publishing module consumes this
   report instead of re-probing the files.

Without `caption_dir`, the module burns the one prepared subtitle file that was
supplied; in that mode the ASS layout (including any box) is already embedded
and `--caption-box` does not alter it.

Existing fresh outputs are reused unless `--force` is passed, so a retry does
not re-encode a long video. `--dry-run` performs every validation, samples the
caption band, and writes the generated subtitle hand-off, but records the
planned render commands instead of encoding the video. The command exits `0`
for `burned`/`reused`/`dry-run` and `1` for any validation or FFmpeg failure; a
failed run still writes `BURN.json` with `"status": "failed"` and the error, and
leaves neither a partial MP4 nor a `.part` file behind.

## 模块1：舆情监控级素材获取部

`scripts/monitoring/acquisition.py` 是整条流水线的第 1 个模块。它只做"取证"，
不做判断、翻译或发布：把来源登记表变成一份可引用的素材底账，交给后续分析模块。
它和下面每 5 分钟运行一次的视频监控工作流互补——监控工作流负责"发现新视频 ->
烧字幕 -> 发布"，本模块负责回答"这条素材来自哪里、什么时候取的、内容哈希是多少、odule 2 complete: 4 offices (timing, third-party, model research, ergonomics) with full test coverage)

## 全量代理扫描：全量 → 测速 → 同步 main

素材获取不再从三张手写列表各取 10 个候选，而是每次运行都走同一条固化流程
[`proxyindex/full_scan.py`](proxyindex/full_scan.py)：

```text
matrix  →  discover  →  sources  →  collect  →  probe  →  rank  →  sync
```

| 阶段 | 全量口径 |
| --- | --- |
| matrix | `proxyindex/query_matrix.py` 枚举中英文同义词矩阵：代理词 22 个 × 网络/协议词 55 个 × 修饰词 43 个，双向排列，加近义词、拼音主题、中英混排二元组与三元组；约 52,000 条检索式 + 32 个 topic |
| discover | 用 `gh` 搜索 API 按游标逐条走完矩阵（30 次/分钟），游标与覆盖率写入 `discovery-state.json` |
| sources | list² 展开：已登记文件 URL + 每个仓库 × 33 条惯用文件名，约 68,000 个来源 |
| collect | 全量抓取并合并去重；`collect.jsonl` 记录每个来源的状态，失败按 `missing` / `error` 分别记，不伪装成空 |
| probe | 两段探测：先给每个候选一个 TCP 结论，再对开放的端口发真实 HTTPS 请求；未探测记为 `unknown` |
| rank | 对最快的候选立即复验一次，只发布本轮复验通过的 10 个 |
| sync | 把 `top10.json`、`top10.txt`、`scan-report.md`、`scan-state.json` 提交并推送到云端仓库 `main` |

`scripts/monitoring/acquisition.py` 在采集前自动调用该流程（`--proxy-scan auto`，
默认复用 1 小时内的结果），`--proxy-scan full` 强制重跑，`--proxy-scan-sync` 打开
推送。扫描失败不会阻断素材采集：上一轮 top10 继续可用，失败状态照实记录。

```bash
python proxyindex/full_scan.py scan            # 全量流程（不推送）
python proxyindex/full_scan.py scan --sync     # 全量流程并同步 main
python proxyindex/full_scan.py top --json      # 当前发布的 top10
python scripts/monitoring/acquisition.py collect --proxy-scan full
```

定时执行的工作流源文件是 [`scripts/monitoring/proxy-scan.yml`](scripts/monitoring/proxy-scan.yml)
（每小时 + 手动，`--sync` 同步 main）。受令牌 `workflows` 权限限制，`.github/workflows/`
下的文件需由具备该权限的维护者用一条命令安装：

```bash
python scripts/monitoring/install_workflow.py all
```

安装前扫描仍然自动执行：现有的 `monitor-whitehouse.yml` 每 5 分钟调用
`scripts/select_public_ytdlp_proxies.py`，没有 1 小时内的扫描结果时会在同一任务内跑一遍
全量流程；该工作流本身已登记为巡检单元 `gh:gitsynonyms/monitor-whitehouse`。安装独立
工作流后再补登记 `gh:gitsynonyms/proxy-full-scan`（每个工作流 × 触发方式一条 MIU）。
