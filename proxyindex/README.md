# Proxy index (list of lists)

A reproducible, validated meta-index of public proxy-list sources: instead of one
repository with a few files, this aggregates thousands of repositories and the
proxy files inside them, deduplicates the entries, and records which proxies
actually answer at collection time.

## Fixed full-scan flow (`full_scan.py`)

`full_scan.py` is the flow the material-acquisition code calls on every run. It
replaces the old "three hand-picked lists, ten candidates each" selector.

| Stage | What it does | Output |
|---|---|---|
| matrix | enumerates the whole Chinese/English synonym matrix (`query_matrix.py`): proxy heads × network/protocol terms × qualifiers, both orders, plus near-synonyms, pinyin topics and mixed-language pairs/triples | `query-matrix.json`, `queries.txt` |
| discover | walks the matrix through the GitHub search API with a resumable cursor (30 queries/minute authenticated) | `discovered.jsonl`, `discovery-state.json` |
| sources | list² expansion: every recorded file URL **plus** every repository × 33 conventional file paths | `sources.full.txt` |
| collect | fetches every source that is due, parses text/JSON/CSV shapes, merges and de-duplicates | `pool.txt`, `pool.provenance.tsv`, `collect.jsonl` |
| probe | two passes: TCP reachability for every candidate, then a real HTTPS request for every open port | `tcp-probes.tsv`, `http-probes.tsv`, `alive_tcp.txt`, `alive_http.txt` |
| rank | re-probes the fastest candidates fresh and publishes the ten fastest verified proxies | `top10.json`, `top10.txt`, `scan-report.md` |
| sync | commits the published files and pushes to the cloud repository's `main` | commit on `origin/main` |

```bash
python proxyindex/full_scan.py scan                 # full flow, no push
python proxyindex/full_scan.py scan --sync          # ... then push to main
python proxyindex/full_scan.py scan --stages matrix,sources
python proxyindex/full_scan.py top --json           # current published top list
python proxyindex/full_scan.py status               # last run, per-stage summary
```

Every stage is resumable and budgeted. The matrix has ~52,000 queries and the
source list ~68,000 URLs, so a single run consumes part of the work and records
exactly how far the cursor got (`matrix_coverage`, `candidate_coverage`). The
report always states what was **not** observed: an unprobed candidate is
`unknown`, never `ok`.

`proxyindex/query_matrix.py` can be run alone to inspect the matrix sizes:

```bash
python proxyindex/query_matrix.py
```

## Legacy stages

| Stage | Script | Output |
|---|---|---|
| 1. discover sources | `build_index.py` | `sources.txt` (one raw file URL per line), `sources.jsonl` (repo, stars, last push, size) |
| 2. collect and dedupe | `collect.py` | `raw.txt` (every entry), `proxies.txt` (unique `ip:port`), `collect.jsonl` (per-source counts) |
| 3. validate | `validate.py` | `alive_tcp.txt` (socket opens), `alive_http.txt` (real HTTP CONNECT returns 200) |

## Why two validation passes

A proxy that accepts a TCP connection is not a proxy that works. The second pass
sends a real request through each survivor and keeps only the ones that answer.
In practice the TCP pass keeps an order of magnitude more candidates than the
HTTP pass. The published top ten is then re-probed a third time immediately
before it is written, so a row that appears in `top10.json` answered **after**
ranking.

## Security boundary

The scan is read-only towards the public lists and probes a single fixed
connectivity URL. It never sends cookies, credentials or repository secrets
through a public proxy, and only `http://ip:port` HTTP proxies are collected:
SOCKS and subscription payloads are intentionally ignored.

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
