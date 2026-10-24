# Proxy full scan: sharded multi-runner handoff

The tracked source `scripts/monitoring/proxy-scan.yml` now runs the full proxy
sweep as a sharded matrix (collect × N, probe × N, one publishing job). The
active workflow `.github/workflows/proxy-scan.yml` is still the previous
single-runner flow, because writing that path needs a token with the `workflows`
permission, which this checkout does not have.

## Install

A maintainer with the `workflows` permission runs:

```bash
python scripts/monitoring/install_workflow.py proxy-scan --force
git add .github/workflows/proxy-scan.yml
git commit -m "Install the sharded proxy-scan workflow"
git push
```

`--force` is required because the active file differs from the tracked source;
the installer prints the full diff without it. After the install the two files
are byte-identical and `tests/test_proxy_scan_workflow.py` enforces that.

## What changes for the scheduler

| Job | Runners | Work |
| --- | --- | --- |
| `plan` | 1 | builds the shard list, auto-detects the next video |
| `discover` | 1 | advances the GitHub search cursor and the repo list |
| `collect` | 16 (`max-parallel` 8) | fetches its own disjoint source slice |
| `prepare` | 1 | merges the pools, cuts one probe slice per runner |
| `probe` | 16 (`max-parallel` 8) | probes only its slice, 512 TCP / 256 HTTP in flight |
| `publish` | 1 | merges every shard, ranks, commits once |
| `mirror` | 1 | downloads and mirrors the detected video |

`max-parallel` stays below the 20-job limit so the five-minute
`monitor-whitehouse` workflow keeps getting runners, and the publishing job is
the only place that commits, so two runners never race a push to `main`.

## If the installer rejects `--force`

`install_workflow.py: error: unrecognized arguments: --force` means the checkout
predates `6f215d2` ("Install workflow templates with the existing installer,
not a blind cp"), which introduced the drift check and the flag. Such a checkout
also re-creates `process-single-video.HANDOFF.md` / `.yml` as new files and
diverges from `main`, so the later `git push` is rejected with `fetch first`.
Re-align first, then install:

```bash
git fetch origin
git log --oneline -1 origin/main        # expect b1020f1 or newer
git log --oneline origin/main..HEAD     # local-only commits, inspect before dropping
git branch rescue/$(git rev-parse --short HEAD) HEAD   # keep a rescue ref
git reset --hard origin/main
python scripts/monitoring/install_workflow.py proxy-scan --force
git diff --stat                         # only .github/workflows/proxy-scan.yml
git add .github/workflows/proxy-scan.yml
git commit -m "Install the sharded proxy-scan workflow"
git push origin main
```

If the push is rejected with `fetch first` again, `main` moved while you were
working: `git pull --rebase origin main` (only the one workflow file is in the
working tree at that point), then push.
