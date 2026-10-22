---
name: youtube-bilingual-srt
description: Download two YouTube caption tracks in any available languages and merge them into one time-aligned bilingual SRT. Use when the user asks for YouTube subtitles, translation captions, multilingual subtitles, or an SRT file.
---

# YouTube bilingual subtitles

Use the repository's standalone downloader. It runs with the existing Python standard library and `yt-dlp`; do not create a virtual environment or install Python packages for this task.

## Download and merge

On Windows, locate the current user's Anaconda installation through `$env:USERPROFILE`; this does not require activating an environment and does not hard-code the Windows username:

```powershell
& (Join-Path $env:USERPROFILE "anaconda3\python.exe") .\scripts\download_youtube_bilingual_srt.py "<YouTube URL>" --languages "<top-language>,<bottom-language>" --output-dir "D:\MyDownload"
```

The downloader retains every caption format YouTube publishes for both languages and writes aligned bilingual `.vtt`, `.srt`, and `.ass` files. Each language is requested as `vtt`, `srt`, `ttml`, `srv3`, `srv2`, `srv1`, and `json3`, and the downloaded files are kept byte-for-byte. The bilingual VTT preserves both language lines in each merged cue; the bilingual ASS applies the selected visual style. GitHub Actions uploads all generated files as an artifact.

The script retains the two downloaded caption tracks unchanged in every published format, then creates a time-aligned bilingual SRT and a styled ASS file. The merge and the ASS read the format with the most precise timing, in the order `json3`, `srv3`, `srv2`, `srv1`, `vtt`, `ttml`, `srt`; `json3`, `srv3` and `srv2` carry YouTube's own per-word offsets, so the English karaoke highlight follows the spoken word. Use `--source-video PATH` to burn into a video that is already on disk, and `--local-file LANGUAGE=PATH` to build one language from a caption file you already have. It parses VTT cue timing and uses inline word timestamps for ASS cut points when available. A blank line between a cue's timing header and caption text is allowed. For ASS only, a cue whose final inline-timed word is over three seconds before its cue end is shortened to half a second after that word; matched Chinese and English cues use one shared end. Source VTT and bilingual SRT timing remain unchanged. ASS generation groups connected rolling cues per language and reconstructs their unique text. When Chinese and English rolling groups have matching source cue boundaries, both use those shared boundaries and keep each corresponding cue's full text together instead of independently splitting by words or characters. Unpaired groups use a uniform word-rate estimate and split at later cue starts by rounding to the nearest token; when a preceding cue ends at that split, its text is prefixed to the first segment. This fallback is a coarse reading-rate heuristic, not word-level timing supplied by YouTube. It classifies CJK text above Latin text regardless of source track, centers the two language lines inside the caption area, and shrinks text that would exceed the available width. Cues with matching end times are paired in the bilingual SRT even when their start times differ; a later same-language cue replaces an overlapping earlier cue instead of creating rolling text. The default `classic` preset places bold vivid-gold Chinese (`#FFC928`) above white English; the spoken English word highlights in bright cyan (`#00D7FF`) to distinguish it from the Chinese. `--style-preset cinema` selects a sample-like serif style with warm ivory English above Chinese. Chinese is 1.2 times the English font size. The 1920×1080 area `(66,883)`–`(1852,1022)` scales proportionally with the video frame. The background box is always drawn from time zero through the full video duration, including caption gaps, to cover persistent source banners. Generation stops with an explicit error if YouTube does not provide the video duration, since a shorter fallback cannot guarantee full-video coverage. `--box-style glass-diamond` is a translucent faceted glass-like ASS treatment (not actual video blur), `--box-style presidential-blue` uses the user-selected color `#0F1647` (RGB 15, 22, 71), matching the blue listed for the U.S. presidential flag (1959–1994) in [Flag Color Codes](https://www.flagcolorcodes.com/president-of-the-united-states-of-america-usa-1959-1994), and `--box-style solid-color` draws a flat custom-color box without glass facets. The web settings provide day, night, paper, and gold-plaque color pairs with live WCAG contrast values; the settings page is the single source of truth for the homepage generator and its preview. The box color and opacity can be overridden with `--box-color` and `--box-opacity`; position and size are configurable with `--box-x`, `--box-y`, `--box-width`, and `--box-height`. Fonts, sizes, and text colors can be overridden with `--chinese-font`, `--english-font`, `--english-font-size`, `--chinese-font-scale`, `--chinese-color`, and `--english-color`.

To also download and retain the video and render an MP4 with hard-burned subtitles, add `--burn-in`. On a local Windows computer with Intel Quick Sync and FFmpeg built with `h264_qsv`, use `--video-encoder h264_qsv --global-quality 1`. The equivalent one-line FFmpeg operation is:

```powershell
ffmpeg -i "a.mp4" -vf "subtitles='transcript.ass'" -c:v h264_qsv -global_quality 1 -c:a copy "output_with_subtitle.mp4"
```

The ASS file owns the persistent background box, text layout, and styling; FFmpeg only renders the ASS file into the video. The GitHub Actions workflow uses software H.264 encoding because hosted runners do not provide Intel Quick Sync hardware.

The default language pair is `zh-Hans,en-orig`. In the default `classic` preset Chinese appears above English; `cinema` reverses that visual order to match the sample. For another pair, inspect available YouTube track codes first:

```powershell
yt-dlp --list-subs "<YouTube URL>"
```

Then pass any two listed codes, for example `ja,en-orig` or `fr,de`. The first code is written on top; the second is written below it. The script selects a manual track when one exists, otherwise uses an automatic track. YouTube-generated translations are marked auto-translated in the output filename when detected.

The script prints numbered processing steps and streams yt-dlp's wait and download progress directly to PowerShell, so progress remains visible while it runs. It saves both original yt-dlp subtitle files unchanged and creates one time-aligned bilingual SRT below `D:\MyDownload\YYYY-MM-DD_event-video-id\`. Original single-language WebVTT files retain yt-dlp's filenames and bytes; parsing is performed separately for the generated bilingual SRT and ASS. The bilingual SRT pairs cues whose end times match to the millisecond, even if their start times differ, with the first language above the second in one block. A newer cue replaces an overlapping older cue in the same language, preventing rolling multi-line captions; each language contributes at most one line. Unpaired cues remain aligned at source cue boundaries and are not paired merely because they are nearest in time. The original single-language tracks remain unchanged. The ASS style preset controls visual order and can reverse it. It prints the paths of all three files. The script derives event date/title from YouTube metadata unless `--date` or `--event` is supplied. It downloads the second/output-bottom track first; English is requested without a delay, while Chinese waits 56 seconds by default (`--sleep-subtitles` changes the Chinese delay). If a track is unavailable or YouTube rate-limits/blocks the request, report that clearly and do not fabricate captions.

The GitHub Actions Windows runner installs Deno 2 and `yt-dlp[default]`, then explicitly enables Deno as yt-dlp's JavaScript runtime for YouTube extraction.

ASS preserves source cue start times and gaps without lead-in or gap filling. WebVTT inline word timestamps control English word highlighting; when absent, a text-only estimate cannot exactly follow speech. Consult the [BBC subtitle timing guidance](https://www.bbc.co.uk/accessibility/forproducts/guides/subtitles/#Timing) and [Aegisub ASS override tags](https://aegisub.org/docs/latest/ass_tags/) for format details.

The original WebVTT language files are retained byte-for-byte as yt-dlp downloads them. The bilingual SRT uses sequential block numbers and standard blank-line separation while preserving each bottom-track cue's source timing; styling markup already present in source captions is not stripped. SRT styling support varies between players; use the generated ASS for consistent font, color, position, and background-box styling.

## SRT formatting limits

SRT supports cue text and timing universally, not dependable styling. Some players interpret HTML-like tags, but those extensions can be rejected by upload validators. For rich styles (color, font, placement, outline), use ASS/SSA and mention player compatibility limitations.
