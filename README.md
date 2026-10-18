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

This fetches the English transcript, translates each line to Chinese, and writes a bilingual SRT where Chinese is on top and English is underneath.

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

This prefers the automatic English original (`en-orig`) and YouTube's Simplified Chinese auto-translation (`zh-Hans`), waits 56 seconds before requesting the Chinese track, then creates an event/date/video-ID folder containing separately labeled English, Chinese, and time-aligned bilingual `.srt` files. Override track codes, wait time, output root, event title, or date with `--english`, `--chinese`, `--sleep-subtitles`, `--output-dir`, `--event`, and `--date`. It downloads subtitle files, not the video itself. Availability and access depend on YouTube and the network; no downloader can guarantee every video is accessible.

The GitHub Actions workflow defaults to the latest White House channel upload. Run **Download YouTube subtitles** manually to resolve the channel's latest video in the cloud and upload its English, Chinese, and bilingual SRTs as a downloadable Actions artifact.

### Local standalone script (no Python environment changes)

Run from any Windows PowerShell; the command locates Anaconda under the current user's profile with `$env:USERPROFILE`, without requiring an activated environment or hard-coding a username:

```powershell
& (Join-Path $env:USERPROFILE "anaconda3\python.exe") .\scripts\download_youtube_bilingual_srt.py "https://www.youtube.com/watch?v=IySF_8OArvw" --languages zh-Hans,en-orig --output-dir "D:\MyDownload"
```

The script itself uses only the Python standard library and the `yt-dlp` command already available on `PATH`. It prints each processing step and streams yt-dlp's wait/download progress directly to PowerShell, rather than hiding output until completion. It downloads the second language track first; English is requested immediately, while the Chinese track waits 56 seconds by default. Both yt-dlp subtitle files are kept unchanged as the original single-language SRTs; only the bilingual SRT is newly written. Each bottom-track cue remains an independent timed cue, matched only with a top-track cue whose time overlaps it. This preserves the English source cue boundaries and overlapping rolling transitions instead of collapsing them into a multi-line cue; non-overlapping cues are never paired merely because they are nearest in time. Source caption text and markup are preserved. The first language passed to `--languages` is above the second inside each bilingual cue. SRT players vary in how they stack overlapping cues, so verify playback in the target player. Use `yt-dlp --list-subs URL` to see the video's language codes; choose any two listed codes. Use `--output-dir`, `--event`, `--date`, and `--sleep-subtitles` to customize the Chinese-track delay.

The reusable YouTube subtitle workflow skill is in `.github/skills/youtube-bilingual-srt/SKILL.md`.

### GitHub Pages subtitle web app

After GitHub Pages is enabled with **GitHub Actions** as the source, the web app is available at `https://gitsynonyms.github.io/gitsynonyms/`. Paste a YouTube link and choose either local Anaconda or a GitHub-hosted runner. Local mode generates a PowerShell command that locates Anaconda under `$env:USERPROFILE\anaconda3`, without requiring environment activation or hard-coding a username. Runner mode generates a `gh workflow run` command; run `gh auth login` first if needed, or open Actions and manually start the workflow. No token is pasted into or stored by the page. Cloud jobs require a GitHub account with permission to run the repository workflow.

### What can SRT format

SRT carries cue numbers, start/end times, and text. Styling tags such as `<b>`, `<i>`, `<u>`, and `<font color>` are inconsistently interpreted by players and some upload validators reject them. This downloader produces plain SubRip without styling tags for broad compatibility. For styling control (colors per language, font, position, outline, speaker styling), use ASS/SSA instead of SRT.
