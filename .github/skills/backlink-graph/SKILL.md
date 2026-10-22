---
name: backlink-graph
description: Discover backlinks level by level from seed URLs, record every referring page and edge in SQLite, label 一级种子 / 一级反向链接 / N 级种子, and stop when a whole level adds no new page. Use when the user asks for backlinks, reverse links, mention tracking, or a multi-level link graph across news, forums, social media, Q&A and code hosts.
---

# Backlink graph (N levels)

Use `scripts/find_backlinks.py`. It needs only the Python standard library and
stores every result in SQLite.

A **backlink** here is a page that links to, or explicitly mentions, the target
URL. Because no free provider exposes a complete backlink index, the tool
combines several public sources and (by default) fetches each candidate to
confirm whether it contains a real `<a href>` to the target, the URL as text, or
nothing at all.

## Level model

| Level | Meaning | Label |
|---|---|---|
| 1 | URLs passed with `--seed` / `--seed-file` | 一级种子 |
| 2 | Pages that link to or mention a level-1 seed | 一级反向链接 (and 二级种子 for the next hop) |
| n | Pages that link to or mention a level-(n-1) page | (n-1)级反向链接 |
| N | Deepest level reached | N 级种子 |

A URL's level is fixed when it is first stored; discovering it again at a deeper
level only records another edge. `--max-depth` is the deepest level that is both
stored and queried, so `--max-depth 2` yields 一级种子 + 一级反向链接.

## Stop rule

The crawler queries one whole level per round and stops as soon as that level
discovers **zero pages that are not already in the database**. `--max-depth`,
`--max-queries` and `--max-pages` are safety limits; when one is hit the run is
reported as `max-depth`, `max-queries` or `max-pages`, and unqueried pages stay
`pending` for a later run.

## Commands

```bash
# The White House release page, two hops, all default providers.
python scripts/find_backlinks.py \
  --seed "https://www.whitehouse.gov/releases/2026/10/president-trump-presents-national-medals-of-science-and-national-medals-of-technology-and-innovation/" \
  --db backlink-graph.sqlite3 --max-depth 2 --max-queries 20 \
  --export-nodes backlink-nodes.jsonl --export-edges backlink-edges.jsonl

# Seed list, only the fast structured providers.
python scripts/find_backlinks.py --seed-file seeds.txt \
  --providers hackernews,reddit,wikipedia,github --max-depth 3

# Plug a paid/own backlink export (JSONL or CSV source,target).
python scripts/find_backlinks.py --seed https://example.com/article \
  --providers index --index-file ahrefs-export.csv

# Inspect a previous run without network access.
python scripts/find_backlinks.py --report-only --db backlink-graph.sqlite3
```

## Providers

| Name | Source | Coverage | Notes |
|---|---|---|---|
| `bing` | Bing web search | news, blogs, forums, social pages | Exact URL queries are tokenised by Bing, so candidates are always verified |
| `hackernews` | HN Algolia API | tech forum | Story `url` must match the target exactly; comments are text-filtered |
| `reddit` | Pullpush archive API | Reddit posts and comments | Submission `url` must match; comments must contain the URL |
| `lemmy` | one Lemmy instance | federated forums | Post `url` must match; comments must contain the URL |
| `stackexchange` | Stack Exchange API | Q&A sites | Bodies are fetched with `filter=withbody` and filtered locally |
| `github` | GitHub issue search | issues and pull requests | Unauthenticated limit is 10 search requests/minute |
| `wikipedia` | MediaWiki `insource:` | encyclopedia citations | Exact wiki-text search |
| `gdelt` | GDELT DOC 2.0 | global news | Five seconds between requests |
| `searx` | your SearXNG instance | whatever it indexes | Needs `--searx-url` and its JSON API |
| `index` | local JSONL/CSV | Ahrefs/Moz/Common Crawl exports | Needs `--index-file` |

`--providers all` enables the defaults plus `searx` / `index` when configured.
DuckDuckGo, Google, Brave, Reddit's official JSON endpoint and Bluesky are not
used because they return anti-bot responses without credentials.

## Verification and relations

`--no-verify` trusts provider output. Otherwise every candidate is fetched (up
to `--max-verify`, default 40) and stored as:

- `hyperlink` — the page contains an `<a href>` to the target;
- `mention` — the URL appears in the page text;
- `unverified` — neither was found, or the page could not be fetched. Dropped
  unless `--keep-unverified` is set.

Structured providers (`hackernews` stories, `reddit` submissions, `wikipedia`,
`index`) already know the link and are marked `hyperlink` without a fetch.

Internal links (same host as the target, including `www.`) are excluded unless
`--include-internal` is set; search-engine/API hosts are always excluded.

## Database

- `nodes(url, level, kind, target, provider, relation, title, status, http_status, error, first_seen, queried_at)` — `kind` is `seed` or `backlink`; `target` is the page this backlink points at.
- `edges(source, target, level, provider, relation)` — `source` is the referring page; `level` is the source page's level.
- `queries(target, provider, level, status, results, error, fetched_at)` — provider audit trail; reruns skip completed pairs.
- `meta(key, value)` — `stop_reason`, `converged`, `max_level`, `targets_queried`, timestamps.

`--export-nodes` writes JSON Lines with `level`, `role`, `hop`, `label`
(一级种子 / 一级反向链接 / …) and `seed_label`. `--export-edges` writes one JSON
object per referring-page -> target edge.

## Limits and caveats

- No key-free source is a complete backlink index. Search APIs match tokens, not
  hyperlinks, so the strongest exact results come from structured providers and
  from `--index-file`. For production SEO work, export from Ahrefs/Moz/Semrush
  or Common Crawl's web graph and feed it through `--index-file`.
- Recursive backlinks explode combinatorially; keep `--max-depth` small, set
  `--max-queries`, and use `--include-internal` deliberately.
- Respect each provider's terms, rate limits and robots rules; the defaults are
  deliberately conservative. A candidate page is fetched only when
  `--no-verify` is absent.
- Pages behind logins, paywalls or anti-bot walls will stay `unverified`; do not
  treat an empty result as proof that no backlink exists.

