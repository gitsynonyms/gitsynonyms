---
name: proxy-list-index
description: Build and maintain a validated "list of lists" of proxy sources. Use when a task needs many public proxies, when YouTube or another host rate-limits or blocks the local IP, or when the user asks for a proxy index, proxy pool, or meta-index of proxy lists.
---

# Proxy list index

A reproducible, validated meta-index of public proxy sources. Instead of trusting
one repository with a few files, this aggregates thousands of repositories and
the proxy files inside them, deduplicates the entries, and records which proxies
actually answer at collection time.

## Pipeline

| Stage | Script | Output |
|---|---|---|
| 1. discover sources | `proxyindex/build_index.py` | `sources.txt` (one raw file URL per line), `sources.jsonl` (repo, stars, last push, size) |
| 2. collect and dedupe | `proxyindex/collect.py` | `raw.txt` (every entry), `proxies.txt` (unique `ip:port`), `collect.jsonl` (per-source counts) |
| 3. validate | `proxyindex/validate.py` | `alive_tcp.txt` (socket opens), `alive_http.txt` (real HTTP CONNECT returns 200) |
| 4. report | `proxyindex/report.py` | `stats.md` (counts, survival rate, busiest sources) |

```bash
python3 proxyindex/build_index.py   # needs the GitHub CLI, authenticated
python3 proxyindex/collect.py
python3 proxyindex/validate.py
python3 proxyindex/report.py
```

## Discovery

`build_index.py` queries the GitHub search API over several topics and name
patterns (`topic:proxy-list`, `topic:proxies`, `topic:free-proxy`,
`topic:proxy-scraper`, `topic:proxy-checker`, `topic:socks5`,
`proxy-list in:name`, and a description query). Each candidate repository is
filtered on `archived`, `fork`, last push date and size, then its file tree is
listed to pick the files that plausibly hold proxies. The result is tens of
thousands of source files: a list of lists, where every entry is itself a list
of thousands of `ip:port` records.

Search API budget is 30 requests per minute, so the script paces itself; the
core API budget covers one tree listing per repository.

## Two validation passes

A proxy that accepts a TCP connection is not a proxy that works, so the
validator makes two passes. The first opens a socket to every entry. The second
sends a real request through each survivor:

```bash
curl -sS -o /dev/null --max-time 8 -x http://ip:port http://example.com/
```

Only the proxies answering `200` are kept. In practice the TCP pass retains an
order of magnitude more candidates than the HTTP pass, which is why a homepage
check alone is never enough.

## Using a proxy for a blocked download

The same egress must carry both the API request and the media request, because
YouTube signs media URLs against the IP it observed on the API call. A proxy
that is only used for the download therefore still fails with HTTP 403.

```bash
yt-dlp --proxy "http://195.154.191.26:1092" -f "137+140" --merge-output-format mp4 URL
```

For a single egress without a third party, tunnel through your own VPS:

```bash
ssh -N -D 1080 user@your-vps
pip install "yt-dlp[socks]"
yt-dlp --proxy socks5h://127.0.0.1:1080 -f "137+140" URL
```

`socks5h` resolves DNS remotely as well. Expose it to the repository tooling by
exporting `https_proxy`, which yt-dlp and the download scripts inherit.

## Caveats

- The overwhelming majority of public proxies are dead, slow, or duplicated
  mirrors of the same few upstream scrapers. Expect a low single-digit
  survival rate and a list that is stale within hours.
- These are untrusted third parties. A `CONNECT` tunnel cannot read TLS
  traffic without breaking certificate validation, but the operator still sees
  which hosts you contact. Never send credentials, cookies or private data
  through them.
- Prefer your own egress (a VPS with `ssh -D`, a VPN, or a paid proxy) for
  anything that matters; this index is a last resort for public content.
- Many sources are unlicensed scrapes. Check the licence of a source repository
  before redistributing its data.
