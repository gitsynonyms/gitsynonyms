---
name: proxy-list-index
description: Build and maintain a validated "list of lists" of proxy sources, including Last30Days research across forums, social platforms and search engines for proxy synonyms, near-synonyms and word permutations. Use when a task needs many public proxies, when YouTube or another host rate-limits or blocks the local IP, or when the user asks for a proxy index, proxy pool, or meta-index of proxy lists.
---

# Proxy list index

A reproducible, validated meta-index of public proxy sources. Instead of trusting
one repository with a few files, this aggregates thousands of repositories and
the proxy files inside them, deduplicates the entries, and records which proxies
actually answer at collection time.

## Pipeline

| Stage | Script | Output |
|---|---|---|
| 0. research terms | `proxyindex/discover_terms.py` | `term_research.jsonl` (one record per term), `sources_extra.txt` (URLs found across forums, social and search) |
| 1. discover sources | `proxyindex/build_index.py` | `sources.txt` (one raw file URL per line), `sources.jsonl` (repo, stars, last push, size) |
| 2. collect and dedupe | `proxyindex/collect.py` | `raw.txt` (every entry), `proxies.txt` (unique `ip:port`), `collect.jsonl` (per-source counts) |
| 3. validate | `proxyindex/validate.py` | `alive_tcp.txt` (socket opens), `alive_http.txt` (real HTTP CONNECT returns 200) |
| 4. report | `proxyindex/report.py` | `stats.md` (counts, survival rate, busiest sources) |

```bash
python3 proxyindex/discover_terms.py --dry-run   # inspect the term plan, no network
python3 proxyindex/discover_terms.py --merge     # research terms via Last30Days, add URLs
python3 proxyindex/build_index.py                # needs the GitHub CLI, authenticated
python3 proxyindex/collect.py
python3 proxyindex/validate.py
python3 proxyindex/report.py
```

## Term coverage (synonyms, near-synonyms, permutations)

Stage 1 only sees topics and names that its query list already spells out.
Stage 0 closes that gap: it enumerates the vocabulary people actually use for
"network proxy" and researches each term with the Last30Days skill across
forums, social platforms and search engines.

| Group | Covers |
|---|---|
| `core` | proxy, proxies, proxy list, proxy pool, proxy server, proxy database, ip pool, 网络代理, 代理列表, 代理池 |
| `qualifier` | free/public/open/anonymous/elite/transparent, http/https/socks4/socks5, rotating/residential/datacenter, working/live/valid/fresh/daily, 免费/高匿/住宅/机房 ... |
| `action` | scraper, crawler, grabber, collector, aggregator, checker, validator, 抓取, 采集, 聚合, 检测, 验证 ... |
| `permutation` | qualifier x {proxy list, proxy pool, proxies} |
| `chinese` | 免费代理, 高匿代理池, 可用代理IP, 每日代理列表 ... |
| `scope` | base terms restricted to `site:reddit.com`, `site:news.ycombinator.com`, `site:v2ex.com`, `site:hostloc.com`, `site:github.com`, `forum`, `telegram` ... |

A failed term is recorded with `"ok": false` plus the error and yields no URLs;
it is never replaced by a guess, and it does not stop the remaining terms. Use
`LAST30DAYS_SCRIPT` for a local `last30days.py`, or keep the `last30days` CLI on
`PATH`. `--merge` appends only URLs that are not already in `sources.txt`.

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

When the local (direct) egress is blocked - for example the GitHub runner IP is
rate-limited or blocked by YouTube - iterate the aggregated proxy lists, probe
every candidate, and retry through the fastest one that answers:

```bash
python3 proxyindex/build_index.py   # discover lists (list of lists)
python3 proxyindex/collect.py      # merge every list into one pool
python3 proxyindex/validate.py     # keep only TCP-open + HTTP-200 proxies
```

The downloader (`scripts/download_youtube_bilingual_srt.py`) performs this
fallback automatically when a direct download fails: it probes the validated
pool first and otherwise iterates all discovered proxy lists, then retries the
failed yt-dlp invocation through the fastest working public proxy.

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
