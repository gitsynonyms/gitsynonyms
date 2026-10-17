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
