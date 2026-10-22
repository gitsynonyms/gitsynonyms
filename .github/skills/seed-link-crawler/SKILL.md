---
name: seed-link-crawler
description: Crawl seed URLs level by level, store every page link, link edge, and proxy endpoint in SQLite, and stop when one whole level discovers no new link. Use when the user asks for recursive link discovery, backlink levels, proxy harvesting from seed lists, or an N-level link graph labelled by seed/level.
---

# Recursive seed link crawler

Use `scripts/crawl_seed_graph.py`. It needs only the Python standard library, so
it runs under the repository Python without installing anything.

## Level model

| Level | Meaning | Label |
|---|---|---|
| 1 | URLs passed with `--seed` / `--seed-file` | 一级种子 |
| 2 | URLs first found on a level-1 seed | 一级反向链接 (and the 二级种子 for the next hop) |
| n | URLs first found on a level-(n-1) page | (n-1)级反向链接 |
| N | last level fetched | N 级种子 |

The level of a URL is fixed the first time it is seen; finding the same URL
again at a deeper level never renumbers it. Proxy endpoints are terminal
records: they are stored with the level of the page that listed them and are
never used as crawl seeds.

## Stop rule

The crawler processes one whole level per round and stops as soon as that level
discovers **zero URLs that are not already in the database**. `--max-depth` and
`--max-pages` are safety limits; when either is hit the run is reported as
`max-depth` or `max-pages` instead of `converged`, and the remaining nodes stay
`pending` so a later run resumes from them.

## Commands

```bash
# Seed from one URL and expand up to three levels.
python scripts/crawl_seed_graph.py --seed https://example.com/proxies --db proxyindex/seed-graph.sqlite3

# Seeds from a file (one URL per line, # comments allowed), export both views.
python scripts/crawl_seed_graph.py --seed-file seeds.txt \
  --export-nodes proxyindex/seed-graph.jsonl \
  --export-proxies proxyindex/seed-proxies.txt

# Re-fetch seeds that are already stored, then continue as usual.
python scripts/crawl_seed_graph.py --seed https://example.com/proxies --refresh

# Inspect a previous run without touching the network.
python scripts/crawl_seed_graph.py --report-only --db proxyindex/seed-graph.sqlite3
```

Useful filters: `--max-depth`, `--max-pages`, `--workers`, `--delay`,
`--timeout`, `--max-bytes`, `--allow-regex`, `--deny-regex`, `--allow-ip`, and
`--ignore-robots` (robots.txt is honoured by default).

## Database

SQLite, created on first use:

- `nodes(url, level, kind, parent, status, http_status, content_type, title, error, first_seen, fetched_at)` — `kind` is `seed` or `link`; `status` is `pending`, `done`, `error`, or `skipped`.
- `edges(src, dst, level)` — which page linked to which URL.
- `proxies(scheme, host, port, level, source, first_seen)` — unique `ip:port` endpoints, first level and page that mentioned them.
- `meta(key, value)` — `stop_reason`, `converged`, `max_level`, `pages_fetched`, timestamps.

`--export-nodes` writes JSON Lines with `level`, `role` (`seed` or `backlink`),
and `hop` (level minus one). `--export-proxies` writes `scheme://host:port`.

## Behaviour notes

- Relative links are resolved against the final redirect URL; fragments and
  default ports are removed before deduplication.
- Asset links (images, CSS, JS, media, archives) are recorded but never fetched;
  `http(s)` links whose host is an IP literal are skipped unless `--allow-ip` is
  given.
- `ip:port`, `scheme://ip:port`, `socks4/4a/5/5h://ip:port` are extracted from
  HTML, JSON, and plain-text list pages. IPv6 and domain-only proxies are not
  parsed.
- Honours `robots.txt` per origin (cached for the run); a missing file allows
  the crawl.
- A rerun resumes pending nodes and never duplicates rows; use `--refresh` to
  re-fetch seeds that are already `done`.

## Caveats

Crawling is subject to each site's terms of service and to local law. Keep
`--workers` low, set `--delay`, and respect robots.txt on hosts you do not own.
Treat every discovered proxy and page as untrusted third-party data: never send
credentials or private traffic through a harvested proxy, and check the licence
of a source before redistributing its entries.
