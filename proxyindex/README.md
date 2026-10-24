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
| discover | walks the matrix through the GitHub search API with a resumable cursor (30 queries/minute authenticated); the cursor and the de-duplicated repo list are committed, so coverage accumulates across runs | `discovered.jsonl`, `discovery-state.json`, `discovered-repos.txt` |
| sources | list² expansion: every recorded file URL **plus** every repository × 33 conventional file paths | `sources.full.txt` |
| collect | fetches every source that is due, parses text/JSON/CSV shapes, merges and de-duplicates | `pool.txt`, `pool.provenance.tsv`, `collect.jsonl` |
| probe | two passes: TCP reachability for every candidate, then a real HTTPS request for every open port | `tcp-probes.tsv`, `http-probes.tsv`, `alive_tcp.txt`, `alive_http.txt` |
| merge | fan-in for a sharded run: rebuilds the canonical pool/provenance and probe ledgers from the per-runner directories, optionally cutting one disjoint probe slice per runner | canonical `pool.txt`, `pool.provenance.tsv`, `tcp-probes.tsv`, slice files |
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
`unknown`, never `ok`. `tcp_checked` counts **distinct** candidates, so a proxy
probed in several sweeps cannot inflate the coverage figure.

## Sharded multi-runner sweep (full coverage per cycle)

One runner sampling the head of the pool is not full coverage: the deduplicated
pool is hundreds of thousands to millions of candidates, so an hourly single job
with one time budget can only probe a few percent. The flow is therefore
shardable, and `.github/workflows/proxy-scan.yml` runs it as a matrix:

```text
plan (shard list, auto-detect)
├── discover  one runner, advances the search cursor
├── collect   N runners, disjoint source slices  -> artifact per shard
├── prepare   merge the pools, cut N probe slices
├── probe     N runners, disjoint candidate slices -> artifact per shard
└── publish   merge every shard, rank, single commit
```

The slice of a key is `blake2b(key) mod shard_count`, so it is stable across
runs and independent of the order of the pool: a candidate cannot drift between
runners (and be probed twice or not at all). Every runner multiplies the shard
fan-out with its own concurrency (`--tcp-concurrency`, `--probe-concurrency`,
`--workers`), and `max-parallel` caps the matrix so the five-minute monitor
still gets runners.

```bash
# one runner: collect its source slice into its own workspace
python proxyindex/full_scan.py scan --stages sources,collect \
  --shard-index 0 --shard-count 16

# fan-in: rebuild the canonical pool and cut one probe slice per runner
python proxyindex/full_scan.py merge --what pool \
  --parts-dir downloads/collect --split-count 16 --split-dir probe-input

# one probe runner: only its own candidates, all artefacts in its workspace
python proxyindex/full_scan.py scan --stages probe \
  --workspace probe/shard-0 --shard-index 0 --shard-count 16 \
  --shard-input probe-input/shard-0-of-16.txt

# fan-in: merge the probe ledgers, then rank and publish
python proxyindex/full_scan.py merge --what all --parts-dir downloads
python proxyindex/full_scan.py scan --stages rank,sync --sync
```

`--workspace` keeps the mutable artefacts of one execution separate from the
tracked inputs, so two runners never write the same file. A merge never ingests
the canonical artefact it is writing. An empty sweep does not overwrite a
previously published list: `sync` is skipped and the old `top10.json` stays.

The shard fan-out is internal execution parallelism, not extra inspection
units: one repository × one workflow file × one trigger is still one MIU.

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
