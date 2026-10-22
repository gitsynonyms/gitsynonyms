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

The VTT parser tolerates a blank line between a cue's timing header and its caption text. For ASS output only, if inline word timings show that a caption's final word ends more than three seconds before the cue end, the ASS cue is shortened to half a second after that word; matching Chinese and English cues share the same shortened end. The downloaded source VTT and bilingual SRT timings are not changed by this ASS-only correction.

ASS preserves source cue start times and gaps without lead-in or gap filling. When the source VTT contains inline word timestamps, karaoke switches each complete English word to its timestamp. Without word timestamps, the script retains an even text-based estimate; subtitle text alone cannot recover exact pronunciation timing. See the [BBC timing guidance](https://www.bbc.co.uk/accessibility/forproducts/guides/subtitles/#Timing) and [Aegisub ASS override tag reference](https://aegisub.org/docs/latest/ass_tags/).

Burn-in uses a single FFmpeg filter invocation; all typography, color, subtitle placement, and adaptive black-box styling stays in the ASS:

```powershell
ffmpeg -i "a.mp4" -vf "subtitles='transcript.ass'" -c:v h264_qsv -global_quality 1 -c:a copy "output_with_subtitle.mp4"
```

The reusable YouTube subtitle workflow skill is in `.github/skills/youtube-bilingual-srt/SKILL.md`.

### GitHub Pages subtitle web app

After GitHub Pages is enabled with **GitHub Actions** as the source, the web app is available at `https://gitsynonyms.github.io/gitsynonyms/`. Paste a YouTube link and choose either local Anaconda or a GitHub-hosted runner. Local mode generates a PowerShell command that locates Anaconda under `$env:USERPROFILE\anaconda3`, without requiring environment activation or hard-coding a username. Select either the gold Chinese-first style or the sample-like serif English-first style, then customize fonts, colors, and sizes. The settings page also offers day, night, and paper-like reading palettes, with calculated text/background contrast ratios; these are configurable suggestions, not medical claims about eye strain. Choose whether the subtitle background remains for the whole video (to cover a persistent original banner) or appears only with captions (for videos without a banner), and select a glass-diamond visual treatment or the user-selected presidential-blue color. Box color, opacity, position, and dimensions are editable. Check the burn-in option to download the source video and render a hard-subtitled MP4; local mode uses Intel Quick Sync (`h264_qsv`, quality 1), so the machine must have compatible hardware and FFmpeg support. The cloud workflow uses software encoding. Runner mode generates a `gh workflow run` command; run `gh auth login` first if needed, or open Actions and manually start the workflow. No token is pasted into or stored by the page. Cloud jobs require a GitHub account with permission to run the repository workflow.
The settings page also provides instant cross-page synchronization, a subtitle preview that follows the configured position and size, and a warm-gold plaque palette inspired by the supplied example. The comfort palette choices include the day, night, paper, and warm-gold combinations (the warm-gold pair has a 9.83:1 WCAG contrast ratio); selecting one applies a dedicated solid-color background and matching Chinese/English text colors.

### What can SRT format

SRT carries cue numbers, start/end times, and text. Styling tags such as `<b>`, `<i>`, `<u>`, and `<font color>` are inconsistently interpreted by players and some upload validators reject them. This downloader produces plain SubRip without styling tags for broad compatibility. For styling control (colors per language, font, position, outline, speaker styling), use ASS/SSA instead of SRT.
