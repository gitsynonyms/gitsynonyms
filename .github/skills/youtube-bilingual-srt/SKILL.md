---
name: youtube-bilingual-srt
description: Download two YouTube caption tracks in any available languages and merge them into one time-aligned bilingual SRT. Use when the user asks for YouTube subtitles, translation captions, multilingual subtitles, or an SRT file.
---

# YouTube bilingual SRT

Use the repository's standalone downloader. It runs with the existing Python standard library and `yt-dlp`; do not create a virtual environment or install Python packages for this task.

## Download and merge

On Windows, use the configured Anaconda Python:

```powershell
& "C:\Users\a2023\anaconda3\python.exe" .\scripts\download_youtube_bilingual_srt.py "<YouTube URL>" --languages "<top-language>,<bottom-language>" --output-dir "D:\MyDownload"
```

The default language pair is `zh-Hans,en-orig`, producing Chinese above English. For another pair, inspect available YouTube track codes first:

```powershell
yt-dlp --list-subs "<YouTube URL>"
```

Then pass any two listed codes, for example `ja,en-orig` or `fr,de`. The first code is written on top; the second is written below it. The script selects a manual track when one exists, otherwise uses an automatic track. YouTube-generated translations are marked auto-translated in the output filename when detected.

The script saves three UTF-8-with-BOM `.srt` files below `D:\MyDownload\YYYY-MM-DD_event-video-id\`: one separate file for each requested language (named with its track type and language code) and one time-aligned bilingual file. For the default pair, the archive contains separate Chinese and English subtitles plus the Chinese-above-English bilingual SRT. It prints the paths of all three files. The script derives event date/title from YouTube metadata unless `--date` or `--event` is supplied. It waits 56 seconds before requesting the second track by default (`--sleep-subtitles` changes this). If a track is unavailable or YouTube rate-limits/blocks the request, report that clearly and do not fabricate captions.

All three outputs are strict plain SRT: sequential cue numbers, `HH:MM:SS,mmm` timing, non-empty text, standard blank-line separation, UTF-8 BOM, and CRLF line endings. It removes caption markup and validates the written files. Do not add color or other player-dependent style tags to strict SRT output.

## SRT formatting limits

SRT supports cue text and timing universally, not dependable styling. Some players interpret HTML-like tags, but those extensions can be rejected by upload validators. For rich styles (color, font, placement, outline), use ASS/SSA and mention player compatibility limitations.
