# Proxy index (list of lists)

A reproducible, validated meta-index of public proxy-list sources: instead of one
repository with a few files, this aggregates thousands of repositories and the
proxy files inside them, deduplicates the entries, and records which proxies
actually answer at collection time.

## Optional stage 0: recursive seed crawl

`scripts/crawl_seed_graph.py` walks seed URLs level by level, records every
page link, link edge, and proxy endpoint in SQLite, and stops once a whole level
discovers no URL that is not already stored. Level 1 is the seed set; a URL
first found on a level-n page is stored at level n+1, so level 2 is the first
generation of discovered links and so on. See
`.github/skills/seed-link-crawler/SKILL.md` for the full command reference.

```bash
python scripts/crawl_seed_graph.py --seed-file proxyindex/sources.txt \
  --db proxyindex/seed-graph.sqlite3 --max-depth 3 \
  --export-proxies proxyindex/seed-proxies.txt
```

## Pipeline

| Stage | Script | Output |
|---|---|---|
| 1. discover sources | `build_index.py` | `sources.txt` (one raw file URL per line), `sources.jsonl` (repo, stars, last push, size) |
| 2. collect and dedupe | `collect.py` | `raw.txt` (every entry), `proxies.txt` (unique `ip:port`), `collect.jsonl` (per-source counts) |
| 3. validate | `validate.py` | `alive_tcp.txt` (socket opens), `alive_http.txt` (real HTTP CONNECT returns 200) |

```bash
python3 build_index.py    # needs the GitHub CLI, authenticated
python3 collect.py
python3 validate.py
```

## Why two validation passes

A proxy that accepts a TCP connection is not a proxy that works. The second pass
sends a real request through each survivor (`curl -x http://ip:port
http://example.com/`) and keeps only the ones that return `200`. In practice the
TCP pass keeps an order of magnitude more candidates than the HTTP pass.

## Caveats

- The overwhelming majority of public proxies are dead, slow, or duplicated
  mirrors of the same handful of upstream scrapers; expect a low single-digit
  survival rate and a list that is stale within hours.
- These are untrusted third parties. `CONNECT` tunnels cannot read TLS traffic
  without breaking certificate validation, but the operator still sees which
  hosts you contact. Never send credentials, cookies or private data through
  them.
- Prefer your own egress (a VPS with `ssh -D`, a VPN, or a paid proxy) for
  anything that matters; this index is a last resort for public content.
- Many sources are unlicensed scrapes. Check the licence of a source repository
  before redistributing its data.
