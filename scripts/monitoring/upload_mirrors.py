#!/usr/bin/env python3
"""把下载产物上传到多个匿名网盘，并实测上传/下载速度与服务限制。

The download stage produces public artifacts (SRT/VTT/ASS/zip). This module
mirrors them to several anonymous file hosts -- filebin.net and five more -- and
records, for each host, the upload wall time, the measured upload and download
throughput, and the host's published limits. The result is a JSON record plus a
Markdown table next to the artifact, so a later reader can tell which link is
still alive and how fast it was at upload time.

Only public content is uploaded: no cookies, tokens, private keys or account
data ever leave the machine. Every host is used anonymously.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "gitsynonyms.uploads/1"
DEFAULT_FILEBIN_BIN = "https://filebin.net/f8l40pcd28jo8bl0"
DOWNLOAD_SAMPLE_BYTES = 256 * 1024


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def curl(args: list[str], timeout: float = 180.0) -> tuple[int, bytes, float]:
    """Run curl, returning (returncode, stdout, elapsed_seconds)."""
    command = ["curl", "-sS", "--max-time", str(int(timeout)), *args]
    started = time.monotonic()
    completed = subprocess.run(command, capture_output=True)
    elapsed = time.monotonic() - started
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(message[:300] or f"curl exited {completed.returncode}")
    return completed.returncode, completed.stdout, elapsed


def parse_json(payload: bytes) -> dict:
    try:
        data = json.loads(payload.decode("utf-8", "replace") or "{}")
    except json.JSONDecodeError as error:
        raise RuntimeError("mirror returned invalid JSON") from error
    if not isinstance(data, dict):
        raise RuntimeError("mirror returned an unexpected payload")
    return data

# ---------------------------------------------------------------------------
# Adapters: one anonymous upload call per host
# ---------------------------------------------------------------------------


def upload_filebin(path: Path, bin_url: str = DEFAULT_FILEBIN_BIN, run=curl) -> str:
    target = f"{bin_url.rstrip('/')}/{urllib.parse.quote(path.name)}"
    run(["-X", "POST", "-H", f"filename: {path.name}", "--data-binary", f"@{path}", target])
    return target


def upload_gofile(path: Path, run=curl) -> str:
    _, payload, _ = run(["https://api.gofile.io/servers"], timeout=60)
    servers = parse_json(payload).get("data", {}).get("servers") or []
    if not servers:
        raise RuntimeError("gofile returned no upload server")
    server = servers[0]["name"]
    _, payload, _ = run(["-F", f"file=@{path}", f"https://{server}.gofile.io/contents/uploadfile"])
    data = parse_json(payload).get("data") or {}
    page = data.get("downloadPage")
    if not page:
        raise RuntimeError("gofile returned no download page")
    return page


def upload_tmpfiles(path: Path, run=curl) -> str:
    _, payload, _ = run(["-F", f"file=@{path}", "https://tmpfiles.org/api/v1/upload"])
    url = (parse_json(payload).get("data") or {}).get("url")
    if not url:
        raise RuntimeError("tmpfiles returned no url")
    return url.replace("tmpfiles.org/", "tmpfiles.org/dl/", 1)


def upload_uguu(path: Path, run=curl) -> str:
    _, payload, _ = run(["-F", f"files[]=@{path}", "https://uguu.se/upload.php"])
    files = parse_json(payload).get("files") or []
    if not files:
        raise RuntimeError("uguu returned no file")
    return files[0]["url"]


def upload_temp_sh(path: Path, run=curl) -> str:
    _, payload, _ = run(["-F", f"file=@{path}", "https://temp.sh/upload"])
    url = payload.decode("utf-8", "replace").strip().splitlines()[0] if payload.strip() else ""
    if not url.startswith("http"):
        raise RuntimeError(f"temp.sh returned {url[:80]!r}")
    return url


def upload_x0(path: Path, run=curl) -> str:
    _, payload, _ = run(["-F", f"file=@{path}", "https://x0.at"])
    url = payload.decode("utf-8", "replace").strip()
    if not url.startswith("http"):
        raise RuntimeError(f"x0.at returned {url[:80]!r}")
    return url


def upload_quax(path: Path, run=curl) -> str:
    _, payload, _ = run(["-F", f"files[]=@{path}", "https://qu.ax/upload.php"])
    files = parse_json(payload).get("files") or []
    if not files:
        raise RuntimeError("qu.ax returned no file")
    return files[0]["url"]


def upload_pomf(path: Path, run=curl) -> str:
    _, payload, _ = run(["-F", f"files[]=@{path}", "https://pomf.lain.la/upload.php"])
    files = parse_json(payload).get("files") or []
    if not files:
        raise RuntimeError("pomf returned no file")
    return files[0]["url"]

# ---------------------------------------------------------------------------
# Host registry: adapter + published limits
# ---------------------------------------------------------------------------

MIRRORS: tuple[dict, ...] = (
    {
        "name": "filebin.net",
        "upload": upload_filebin,
        "direct": True,
        "limits": "单文件 2 GB；bin 在最后一次上传后 6 天过期；公开无密码；支持直链",
    },
    {
        "name": "gofile.io",
        "upload": upload_gofile,
        "direct": False,
        "limits": "匿名上传；单文件约 10 GB；文件在 10 天无下载后删除；下载需经页面",
    },
    {
        "name": "tmpfiles.org",
        "upload": upload_tmpfiles,
        "direct": True,
        "limits": "单文件 100 MB；保留 1 小时；直链需使用 /dl/ 路径",
    },
    {
        "name": "uguu.se",
        "upload": upload_uguu,
        "direct": True,
        "limits": "单文件 128 MB；保留 3 小时",
    },
    {
        "name": "temp.sh",
        "upload": upload_temp_sh,
        "direct": True,
        "limits": "单文件 4 GB；保留 3 天",
    },
    {
        "name": "x0.at",
        "upload": upload_x0,
        "direct": True,
        "limits": "单文件 512 MB；保留 30–365 天（按文件大小）",
    },
    {
        "name": "qu.ax",
        "upload": upload_quax,
        "direct": True,
        "limits": "单文件 128 MB；保留 30 天",
    },
    {
        "name": "pomf.lain.la",
        "upload": upload_pomf,
        "direct": True,
        "limits": "单文件 512 MB；保留 30 天",
    },
)


def measure_download(url: str, *, run=curl, sample: int = DOWNLOAD_SAMPLE_BYTES) -> dict:
    """Fetch the first ``sample`` bytes and return the measured throughput."""
    _, payload, elapsed = run(
        [
            "-L",
            "-r",
            f"0-{max(0, sample - 1)}",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code} %{size_download}",
            url,
        ],
        timeout=60,
    )
    line = payload.decode("utf-8", "replace").strip().splitlines()[-1:]
    parts = line[0].split() if line else []
    if len(parts) != 2:
        return {"note": "no measurement"}
    status, size = int(parts[0]), int(parts[1])
    return {
        "http_status": status,
        "bytes": size,
        "seconds": round(elapsed, 3),
        "bytes_per_second": round(size / elapsed, 1) if elapsed > 0 else 0.0,
    }


def upload_one(path: Path, mirror: dict, *, filebin_bin: str = DEFAULT_FILEBIN_BIN, run=curl) -> dict:
    size = path.stat().st_size
    result = {
        "file": path.name,
        "bytes": size,
        "mirror": mirror["name"],
        "limits": mirror["limits"],
        "status": "ok",
        "url": "",
        "error": "",
    }
    try:
        started = time.monotonic()
        if mirror["name"] == "filebin.net":
            url = mirror["upload"](path, filebin_bin, run)
        else:
            url = mirror["upload"](path, run)
        elapsed = time.monotonic() - started
    except (RuntimeError, OSError) as error:
        result.update(status="error", error=str(error)[:200])
        return result
    result.update(
        url=url,
        upload_seconds=round(elapsed, 3),
        upload_bytes_per_second=round(size / elapsed, 1) if elapsed > 0 else 0.0,
    )
    if mirror.get("direct", True):
        try:
            result["download"] = measure_download(url, run=run)
        except (RuntimeError, OSError) as error:
            result["download"] = {"error": str(error)[:200]}
    else:
        result["download"] = {"note": "页面链接，未测量直链下载"}
    return result

# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def human_rate(value: float) -> str:
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.2f} MB/s"
    if value >= 1024:
        return f"{value / 1024:.1f} KB/s"
    return f"{value:.0f} B/s"


def render_markdown(payload: dict) -> str:
    lines = [
        "# 网盘镜像上传报告 (upload mirrors)",
        "",
        f"- 生成时间: {payload['generated_at']}",
        f"- filebin bin: {payload['filebin_bin']}",
        f"- 成功: {payload['summary']['ok']} / {payload['summary']['attempted']}",
        "",
    ]
    for artifact in payload["artifacts"]:
        lines.extend(
            [
                f"## {artifact['file']} ({artifact['bytes']} bytes, {artifact['sha256'][:12]})",
                "",
                "| 网盘 | 链接 | 上传耗时 | 上传速度 | 下载速度 | 限制 |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for row in artifact["uploads"]:
            if row["status"] != "ok":
                lines.append(
                    f"| {row['mirror']} | 失败: {row['error']} | - | - | - | {row['limits']} |"
                )
                continue
            download = row.get("download") or {}
            if "bytes_per_second" in download:
                speed = human_rate(download["bytes_per_second"])
            elif download.get("note"):
                speed = download["note"]
            else:
                speed = f"失败: {download.get('error', '未测量')}"
            lines.append(
                f"| {row['mirror']} | {row['url']} | {row['upload_seconds']:.2f} s | "
                f"{human_rate(row['upload_bytes_per_second'])} | {speed} | {row['limits']} |"
            )
        lines.append("")
    lines.extend(
        [
            "## 说明",
            "",
            "- 上传速度 = 文件字节数 / 实测 curl 上传墙钟时间；下载速度 = 实测回读吞吐（最多 256 KB 采样）。",
            "- 限制列来自各服务公开说明，未逐项做上限压测；链接可能随时失效，报告只代表上传当时的状态。",
            "- 全部为匿名上传，未传输任何 Cookie、令牌或私有数据。",
            "",
        ]
    )
    return "\n".join(lines)


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def upload_artifacts(
    files: list[Path],
    *,
    filebin_bin: str = DEFAULT_FILEBIN_BIN,
    only: list[str] | None = None,
    run=curl,
    quiet: bool = False,
) -> dict:
    mirrors = [m for m in MIRRORS if not only or m["name"] in only]
    payload = {
        "schema": SCHEMA,
        "generated_at": utc_now(),
        "filebin_bin": filebin_bin,
        "artifacts": [],
        "summary": {"attempted": 0, "ok": 0},
    }
    for path in files:
        artifact = {
            "file": path.name,
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
            "uploads": [],
        }
        for mirror in mirrors:
            row = upload_one(path, mirror, filebin_bin=filebin_bin, run=run)
            artifact["uploads"].append(row)
            payload["summary"]["attempted"] += 1
            payload["summary"]["ok"] += row["status"] == "ok"
            if not quiet:
                detail = row["url"] if row["status"] == "ok" else row["error"]
                print(f"  {path.name} -> {mirror['name']}: {detail}", flush=True)
        payload["artifacts"].append(artifact)
    return payload

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="+", type=Path, help="artifacts to mirror")
    parser.add_argument(
        "--filebin-bin",
        default=DEFAULT_FILEBIN_BIN,
        help="filebin.net bin URL (default: %(default)s)",
    )
    parser.add_argument(
        "--only",
        default="",
        help="comma separated mirror names (default: every configured mirror)",
    )
    parser.add_argument("--out", default="", help="JSON report path")
    parser.add_argument("--md", default="", help="Markdown report path")
    parser.add_argument(
        "--min-ok",
        type=int,
        default=5,
        help="fail when fewer than this many uploads succeed (default 5)",
    )
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    files = [path for path in args.files if path.is_file()]
    missing = [str(path) for path in args.files if not path.is_file()]
    if missing:
        print(f"error: missing file(s): {', '.join(missing)}", file=sys.stderr)
        return 2
    only = [name.strip() for name in args.only.split(",") if name.strip()]
    try:
        payload = upload_artifacts(
            files, filebin_bin=args.filebin_bin, only=only or None, quiet=args.quiet
        )
    except (RuntimeError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else files[0].parent / "UPLOAD_MIRRORS.json"
    md = Path(args.md) if args.md else files[0].parent / "UPLOAD_MIRRORS.md"
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    md.write_text(render_markdown(payload), encoding="utf-8")
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            f"mirrors: {payload['summary']['ok']}/{payload['summary']['attempted']} uploaded; "
            f"report: {out} / {md}"
        )
    return 0 if payload["summary"]["ok"] >= max(1, args.min_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
