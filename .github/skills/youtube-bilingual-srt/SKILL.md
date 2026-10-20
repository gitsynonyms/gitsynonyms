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

The script creates the two unchanged single-language SRT tracks, a time-aligned bilingual SRT, and a styled ASS file. ASS generation classifies CJK text above Latin text regardless of the source track, combines rolling lines by language, centers the bilingual lines inside the caption area, and shrinks text that would exceed the available width. The 1920×1080 area `(66,883)`–`(1852,1022)` is drawn as a rectangle inside the ASS file and scales proportionally with the video frame. Chinese appears above English in vivid gold (`#FFC928`), is 1.2 times the English font size, and both lines use bold text with a subtle shadow; English is white. The ASS box is black at 0.6 opacity, so the background appears whether the ASS is played separately or burned in. Fonts, sizes, text colors, box color, and opacity are configurable through the `--chinese-font`, `--english-font`, `--english-font-size`, `--chinese-font-scale`, `--chinese-color`, `--english-color`, `--box-color`, and `--box-opacity` options.

To also download and retain the video and render an MP4 with hard-burned subtitles, add `--burn-in`. On a local Windows computer with Intel Quick Sync and FFmpeg built with `h264_qsv`, use `--video-encoder h264_qsv --global-quality 1`. The equivalent one-line FFmpeg operation is:

```powershell
ffmpeg -i "a.mp4" -vf "subtitles='transcript.ass'" -c:v h264_qsv -global_quality 1 -c:a copy "output_with_subtitle.mp4"
```

The ASS file owns the background box, text layout, and styling; FFmpeg only renders the ASS file into the video. The GitHub Actions workflow uses software H.264 encoding because hosted runners do not provide Intel Quick Sync hardware.

The default language pair is `zh-Hans,en-orig`, producing Chinese above English. For another pair, inspect available YouTube track codes first:

```powershell
yt-dlp --list-subs "<YouTube URL>"
```

Then pass any two listed codes, for example `ja,en-orig` or `fr,de`. The first code is written on top; the second is written below it. The script selects a manual track when one exists, otherwise uses an automatic track. YouTube-generated translations are marked auto-translated in the output filename when detected.

The script prints numbered processing steps and streams yt-dlp's wait and download progress directly to PowerShell, so progress remains visible while it runs. It saves both original yt-dlp subtitle files unchanged and creates one time-aligned bilingual SRT below `D:\MyDownload\YYYY-MM-DD_event-video-id\`. Original single-language files retain yt-dlp's filenames, bytes, cue text, timing, markup, and numbering; do not parse and rewrite them in place. The bilingual timeline splits at source cue boundaries and includes only cues active during each interval. When rolling cues overlap, their caption text is placed in the same SRT block in chronological source order; this avoids VLC stacking overlapping subtitle blocks in reverse visual order. Non-overlapping cues are never paired merely because they are nearest in time. Caption text and markup are copied verbatim from source cues. The first language is above the second within each bilingual cue. For the default pair, Chinese is above English. It prints the paths of all three files. The script derives event date/title from YouTube metadata unless `--date` or `--event` is supplied. It downloads the second/output-bottom track first; English is requested without a delay, while Chinese waits 56 seconds by default (`--sleep-subtitles` changes the Chinese delay). If a track is unavailable or YouTube rate-limits/blocks the request, report that clearly and do not fabricate captions.

The GitHub Actions Windows runner installs Deno 2 and `yt-dlp[default]`, then explicitly enables Deno as yt-dlp's JavaScript runtime for YouTube extraction.

The original language files are retained byte-for-byte as yt-dlp downloads them. The bilingual SRT uses sequential block numbers and standard blank-line separation while preserving each bottom-track cue's source timing; styling markup already present in source captions is not stripped. SRT styling support varies between players; use the generated ASS for consistent font, color, position, and background-box styling.

## SRT formatting limits

SRT supports cue text and timing universally, not dependable styling. Some players interpret HTML-like tags, but those extensions can be rejected by upload validators. For rich styles (color, font, placement, outline), use ASS/SSA and mention player compatibility limitations.
