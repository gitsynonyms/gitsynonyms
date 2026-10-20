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

The script itself uses only the Python standard library and the `yt-dlp` command already available on `PATH`. It prints each processing step and streams yt-dlp's wait/download progress directly to PowerShell, rather than hiding output until completion. It downloads the second language track first; English is requested immediately, while the Chinese track waits 56 seconds by default. Both yt-dlp subtitle files are kept unchanged as the original single-language SRTs; the bilingual SRT and ASS are additional outputs. The bilingual timeline splits at source cue boundaries and includes only cues active during each interval. When rolling cues overlap, their text is placed in a single SRT block in original chronological order; this avoids VLC stacking overlapping blocks in reverse visual order. Non-overlapping cues are never paired merely because they are nearest in time. Source caption text and markup are preserved in the original tracks and SRT. The default ASS detects CJK lines and places them above Latin lines regardless of source track order, joining rolling lines per language, centering the text in the caption area, and reducing oversized lines to fit. Its `classic` preset uses vivid gold Chinese (`#FFC928`) above white English; Chinese font size is 1.2 times English, with bold text and a soft shadow. The selectable `cinema` preset uses a sample-like serif style with English above Chinese in warm ivory. ASS itself contains one black background rectangle from video time zero through the metadata-reported video duration (or the last caption when duration is unavailable); it does not disappear during caption gaps. The default box uses coordinates `(66,883)` to `(1852,1022)` on a `1920x1080` canvas and scales proportionally with the frame; opacity is `0.6` (60% opaque). Use `--box-x`, `--box-y`, `--box-width`, and `--box-height` to customize its position and size. Pass `--chinese-font`, `--english-font`, `--english-font-size`, `--chinese-font-scale`, `--chinese-color`, `--english-color`, `--box-color`, or `--box-opacity` to customize typography and box appearance. To retain the downloaded video and create a hard-subtitled MP4, add `--burn-in`; the source video is retained in the output folder alongside the rendered MP4. A local Intel Quick Sync example is `--burn-in --video-encoder h264_qsv --global-quality 1`; it requires an FFmpeg build with `h264_qsv` and compatible hardware. Use `yt-dlp --list-subs URL` to see the video's language codes; choose any two listed codes.

Burn-in uses a single FFmpeg filter invocation; all typography, color, subtitle placement, and adaptive black-box styling stays in the ASS:

```powershell
ffmpeg -i "a.mp4" -vf "subtitles='transcript.ass'" -c:v h264_qsv -global_quality 1 -c:a copy "output_with_subtitle.mp4"
```

The reusable YouTube subtitle workflow skill is in `.github/skills/youtube-bilingual-srt/SKILL.md`.

### GitHub Pages subtitle web app

After GitHub Pages is enabled with **GitHub Actions** as the source, the web app is available at `https://gitsynonyms.github.io/gitsynonyms/`. Paste a YouTube link and choose either local Anaconda or a GitHub-hosted runner. Local mode generates a PowerShell command that locates Anaconda under `$env:USERPROFILE\anaconda3`, without requiring environment activation or hard-coding a username. Select either the gold Chinese-first style or the sample-like serif English-first style, then customize fonts, colors, and sizes. The subtitle box color, opacity, position, and dimensions are editable; its ASS rectangle persists from the beginning to the end of the video. Check the burn-in option to download the source video and render a hard-subtitled MP4; local mode uses Intel Quick Sync (`h264_qsv`, quality 1), so the machine must have compatible hardware and FFmpeg support. The cloud workflow uses software encoding. Runner mode generates a `gh workflow run` command; run `gh auth login` first if needed, or open Actions and manually start the workflow. No token is pasted into or stored by the page. Cloud jobs require a GitHub account with permission to run the repository workflow.

### What can SRT format

SRT carries cue numbers, start/end times, and text. Styling tags such as `<b>`, `<i>`, `<u>`, and `<font color>` are inconsistently interpreted by players and some upload validators reject them. This downloader produces plain SubRip without styling tags for broad compatibility. For styling control (colors per language, font, position, outline, speaker styling), use ASS/SSA instead of SRT.
