# gitsynonyms

`gitsynonyms` is a tiny Git command lookup tool focused on common aliases and close synonyms.

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

The GitHub Actions workflow defaults to the latest White House channel upload. Run **Download YouTube subtitles** manually to resolve the channel's latest video in the cloud and upload its English, Chinese, and bilingual SRTs as a downloadable Actions artifact.

### Local standalone script (no Python environment changes)

Run from any Windows PowerShell; the command locates Anaconda under the current user's profile with `$env:USERPROFILE`, without requiring an activated environment or hard-coding a username:

```powershell
& (Join-Path $env:USERPROFILE "anaconda3\python.exe") .\scripts\download_youtube_bilingual_srt.py "https://www.youtube.com/watch?v=IySF_8OArvw" --languages zh-Hans,en-orig --output-dir "D:\MyDownload"
```

The downloader retains every caption format YouTube publishes for both languages and writes aligned bilingual `.vtt`, `.srt`, and `.ass` files. Each language is requested as `vtt`, `srt`, `ttml`, `srv3`, `srv2`, `srv1`, and `json3`; the downloaded files are kept byte-for-byte as yt-dlp writes them. The bilingual VTT preserves both language lines in each merged cue; the bilingual ASS applies the selected visual style. GitHub Actions uploads all outputs as an artifact.

The script itself uses only the Python standard library and the `yt-dlp` command already available on `PATH`. It prints each processing step and streams yt-dlp's wait/download progress directly to PowerShell, rather than hiding output until completion. It downloads the second language track first; English is requested immediately, while the Chinese track waits 56 seconds by default, and that wait is applied before every format request of the delayed track. Every downloaded caption file is kept unchanged as the original single-language track; the bilingual SRT and ASS are additional outputs. The merge and the ASS read the track format that carries the most precise timing, in the order `json3`, `srv3`, `srv2`, `srv1`, `vtt`, `ttml`, `srt`; `json3`, `srv3` and `srv2` expose YouTube's own per-word offsets, so the English karaoke highlight follows the spoken word instead of a uniform estimate. VTT cue timings are parsed, and inline word timestamps are used for ASS cut points when available. The bilingual SRT pairs cues whose end times match (to the millisecond), even if their start times differ, placing Chinese and English on separate lines in one block. A newer cue replaces an overlapping older cue in the same language, so each language contributes at most one line and rolling captions are not accumulated. Unpaired cues remain time-aligned at their source boundaries; unrelated cues are not paired merely because they are nearest in time. Original source text and markup remain unchanged in the downloaded single-language files. ASS output groups connected rolling cues in each language and reconstructs their unique text. When Chinese and English rolling groups have matching source cue boundaries, both use those shared boundaries and keep each corresponding cue's full text together instead of independently splitting by words or characters. Unpaired groups use a uniform word-rate estimate and split at later cue starts by rounding to the nearest token; if a preceding cue ends at that split, its text is prefixed to the first segment. This fallback is a coarse estimate, not YouTube-provided word timing. CJK text is placed above Latin text regardless of source track order, centered in the caption area, and reduced to fit when needed. ASS English text uses karaoke timing to highlight words in sequence, evenly distributing each caption interval's duration across its English words. The homepage reads the one shared settings record managed by the subtitle settings page rather than presenting duplicate style controls. The `classic` preset uses vivid gold Chinese (`#FFC928`) above white English; Chinese font size is 1.2 times English, with bold text and a soft shadow. English words highlight in bright cyan (`#00D7FF`) as they are spoken, clearly distinguishing the active English word from the gold Chinese line. The selectable `cinema` preset uses a sample-like serif style with English above Chinese in warm ivory. The background box is always visible from video time zero through the full video duration, including caption gaps, to cover persistent source banners. Generation stops with an explicit error if YouTube does not provide the video duration, since a shorter fallback cannot guarantee full-video coverage. `--box-style glass-diamond` gives a translucent, faceted glass-like look (an ASS-compatible visual treatment, not actual background blur); `--box-style presidential-blue` uses the user-selected color `#0F1647` (RGB 15, 22, 71), matching the blue listed for the U.S. presidential flag (1959–1994) in [Flag Color Codes](https://www.flagcolorcodes.com/president-of-the-united-states-of-america-usa-1959-1994). The default geometry remains `(66,883)`–`(1852,1022)` on a `1920x1080` canvas and scales proportionally with the frame. Use `--box-x`, `--box-y`, `--box-width`, and `--box-height` to customize position and size; `--box-color` and `--box-opacity` override the selected style. Pass `--chinese-font`, `--english-font`, `--english-font-size`, `--chinese-font-scale`, `--chinese-color`, and `--english-color` to customize typography. To retain the downloaded video and create a hard-subtitled MP4, add `--burn-in`; the source video is retained in the output folder alongside the rendered MP4. Pass `--source-video PATH` to burn the subtitles into a video that is already on disk instead of downloading it again. Pass `--local-file LANGUAGE=PATH` to build one language from a caption file you already have, for example a translation produced by `gitsynonyms-youtube`, when YouTube rate-limits that language's track. A local Intel Quick Sync example is `--burn-in --video-encoder h264_qsv --global-quality 1`; it requires an FFmpeg build with `h264_qsv` and compatible hardware. Use `yt-dlp --list-subs URL` to see the video's language codes; choose any two listed codes.

The VTT parser tolerates a blank line between a cue's timing header and its caption text, and snaps inline word timestamps that YouTube rounds up to 10 ms past a cue's end onto that cue boundary; timestamps further outside their cue still stop generation with an explicit error. For ASS output only, if inline word timings show that a caption's final word ends more than three seconds before the cue end, the ASS cue is shortened to half a second after that word; matching Chinese and English cues share the same shortened end. The downloaded source VTT and bilingual SRT timings are not changed by this ASS-only correction.

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

### What can SRT format

SRT carries cue numbers, start/end times, and text. Styling tags such as `<b>`, `<i>`, `<u>`, and `<font color>` are inconsistently interpreted by players and some upload validators reject them. This downloader produces plain SubRip without styling tags for broad compatibility. For styling control (colors per language, font, position, outline, speaker styling), use ASS/SSA instead of SRT.

## 模块1：舆情监控级素材获取部

`scripts/monitoring/acquisition.py` 是整条流水线的第 1 个模块。它只做“取证”，
不做判断、翻译或发布：把来源登记表变成一份可引用的素材底账，交给后续分析模块。
它和下面每 5 分钟运行一次的视频监控工作流互补——监控工作流负责“发现新视频 →
烧字幕 → 发布”，本模块负责回答“这条素材来自哪里、什么时候取的、内容哈希是多少、
够不够格用于舆情判断”。

只依赖 Python 标准库；`yt-dlp` 仅在 YouTube 来源被惰性导入。

### 输出契约

| 文件 | 作用 |
|---|---|
| `output/materials/ledger.jsonl` | 只追加的修订记录，内容每变化一次追加一行 |
| `output/materials/manifest.json` | 下游读取的物化索引：`materials`、`events`、质量分布、本次运行统计 |
| `output/materials/assets/` | `--download` 时保存的原始字节，按 SHA-256 前 16 位命名 |
| `output/materials/.http-cache.json` | `ETag` / `Last-Modified` 条件请求缓存 |
| `output/materials/.seen.json` | 每条素材最后一次被观察到的时刻 |

单条记录的关键字段：

```text
material_id   event_key   kind(video|article|page|asset)   source   trust
title  url  canonical_url  published_at  fetched_at  language  mime
byte_size  sha256  grade  status  revision  first_seen_at  last_seen_at  local_path
quality{grade, checks{provenance,time_anchor,checksum,payload,content,language}, reasons, text_chars, capture}
details{capture, summary|text, feed|api|channel, duration, also_seen_in, ...}
```

### 质量分级

- **A**：正文或文件已经落地并校验（`--fetch-articles` 抓正文、`--download` 存字节）。
- **B**：可引用的索引级素材，标题、时间、来源、内容哈希齐全，但没有取正文。
- **C**：不足以支撑舆情判断（例如只有标题没有摘要），记为 `rejected` 保留审计，
  不进入 `delivered` 集合，`quality.reasons` 说明缺哪一项。

### 去重与多源印证

- `event_key`：YouTube 一律归一为 `yt:<video_id>`（`watch`、`youtu.be`、`shorts`
  指向同一身份）；其余按去掉 `utm_*` 等追踪参数后的规范化 URL。
- `material_id = sha1(event_key|kind)`：同一事件在第二个来源出现时合并为一条记录，
  第二个来源写入 `details.also_seen_in`，并在 `events` 中计入 `corroboration`
  与 `trust_score`（official 1.0 / wire 0.8 / aggregator 0.6，多源相加封顶 1.0）。
- 只有内容哈希变化才产生新的 `revision`；URL 追踪参数和轮换的页面装饰不会制造噪音。

### 用法

```bash
# 查看来源登记表
python scripts/monitoring/acquisition.py sources

# 采集最近 24 小时（默认窗口），每个来源最多 20 条
python scripts/monitoring/acquisition.py collect

# 只跑官方 RSS 与 GDELT，并抓取条目正文以升级为 A 级
python scripts/monitoring/acquisition.py collect \
  --sources whitehouse-news,gdelt-whitehouse --since 3d --fetch-articles

# 不落盘的演练，输出机器可读的本次结果
python scripts/monitoring/acquisition.py collect --dry-run --json

# 读取上次 manifest，检查多源印证与失败来源
python scripts/monitoring/acquisition.py report --limit 15
```

`--since` / `--until` 接受 ISO 时间或 `90m` / `24h` / `7d`；`--limit` 控制每个来源
的条数，避免 `--fetch-articles` 触发对方限流；`--proxy-pool proxyindex/alive_http.txt`
按请求轮换出口，`--timeout` 与 `--retries` 控制单次请求的重试预算，响应体上限 8 MB。

### 失败语义

每个来源在 `manifest.sources` 中独立记状态：`ok`、`unchanged`（条件请求 304）、
`unavailable`（缺 `yt-dlp`、HTTP 404/429 等）、`error`（解析异常）、`disabled`。
单个来源失败不会中断其余来源；只有全部启用来源都失败时 CLI 才返回 `1`。
来源缺失不会伪造素材：宁可在报告里显示 `unavailable`，也不写入未经验证的内容。

新增来源时，在模块的 `SOURCES` 中加一条 `SourceSpec`（`kind` 取 `rss` / `json` /
`html` / `youtube` / `asset`，`json` 可用 `parser="gdelt"`）。若页面正文不在
`<main>`/`<article>` 里，用 `region` 指定容器 class 或 id 的片段（如 Factbase 的
`calendar-results`），抽取时会优先取该区域，避免把站点导航当成素材。

测试在 `tests/test_acquisition.py`，用本地 HTTP 服务与注入替身覆盖解析、分级、
去重、修订、条件请求、失败降级与 CLI，不访问外网：

```bash
python -m pytest tests/test_acquisition.py
```

## Video archive and scheduled monitoring

`site/videos.html` lists every archived bilingual burn-in, newest first by event
date, and each entry links to its own detail page under `site/videos/<id>.html`.
The detail page carries the full download list: the 1080p MP4 from the GitHub
Release, the bilingual ASS/SRT/VTT, and the source caption files in all seven
published formats (`vtt`, `srt`, `ttml`, `srv3`, `srv2`, `srv1`, `json3`) for
both languages. Pages for older videos are regenerated from the archive, so
publishing a new video never disturbs the existing ones.

`scripts/generate_video_pages.py` builds those pages from the `output/` folders;
`scripts/monitoring/` holds the scheduled monitor:

| Script | Role |
|---|---|
| `acquisition.py` | module 1: material ledger and manifest under `output/materials/` |
| `multi_source.py` | optional discovery and verification across sources |
| `comprehensive_monitor.py` | optional extended monitor; not used by the scheduled workflow |
| `resolve_channel_feed.ps1` | read both flat channel listings and select the next unprocessed upload |
| `install_workflow.py` | copy the active workflow template into `.github/workflows/` |

The optional extended monitor polls `@WhiteHouse/videos` and `@WhiteHouse/streams`, ranks the
uploads (longest and most event-like first), and for every new video:

1. confirms the event with the other sources — the RollCall Factbase calendar
   for the day's schedule, GDELT for mainstream coverage, congressional
   records, YouTube comments and forum threads, plus reverse links and a
   Google/C-SPAN search;
2. downloads all seven caption formats for both languages, treating `json3` as
   the primary timing source;
3. builds the bilingual ASS from the most precise format available;
4. burns the subtitles into the video and writes `VERIFICATION.json` next to
   the output;
5. regenerates the site pages and deploys them to GitHub Pages.

For the optional extended monitor, a missing API key disables only that source,
and a blocked host never fails the run. It keeps `output/.monitor-state.json`
so a run only acts on videos it has not archived yet, and it rotates through the
validated proxy pool so the YouTube rate limiter sees a different egress each
time. The same egress must carry both the API request and the media request,
because YouTube signs media URLs against the IP it observed on the API call.

The active workflow is `.github/workflows/monitor-whitehouse.yml`; its tracked
template is `scripts/monitoring/monitor-whitehouse.yml`. It runs every five
minutes, handles one new upload at a time, and records a video only after its
GitHub Release asset and Pages deployment succeed. GitHub may delay scheduled
runs during periods of high load.
