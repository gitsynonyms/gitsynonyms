#!/usr/bin/env python3
"""信息差公示处 - 生成器 (Part 1: Core types & utilities)"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "site" / "infogap"
HISTORY_DIR = OUT_DIR / "history"
REPORT_JSON = OUT_DIR / "report.json"
INDEX_HTML = OUT_DIR / "index.html"
LAST_RUN = OUT_DIR / ".last-run.json"

DEFAULT_TOPICS = [
    "White House official announcements",
    "Presidential actions and executive orders",
    "White House press briefings and gaggles",
]


@dataclass
class Finding:
    topic: str
    summary: str
    sources: list[dict[str, Any]]
    gaps: list[str]
    discrepancies: list[str]
    confidence: float
    generated_at: str


@dataclass
class Report:
    schema: str = "gitsynonyms.infogap/1"
    generated_at: str = ""
    topics: list[str] = field(default_factory=lambda: list(DEFAULT_TOPICS))
    findings: list[Finding] = field(default_factory=list)
    run_id: str = ""
    status: str = "ok"
    degraded_reasons: list[str] = field(default_factory=list)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_id() -> str:
    return hashlib.sha256(f"{utc_now_iso()}{os.urandom(8).hex()}".encode()).hexdigest()[:12]


# Part 2: Prerequisites & Research


def check_prereqs() -> list[str]:
    """Check required tools and environment."""
    problems = []
    script = os.environ.get("LAST30DAYS_SCRIPT")
    if script:
        if not Path(script).is_file():
            problems.append(f"last30days script not found: {script}")
    elif shutil.which("last30days") is None:
        problems.append("last30days CLI not in PATH; research will be unavailable")
    if shutil.which("node") is None:
        problems.append("Node.js not available; X/Twitter search may be disabled")
    return problems


def run_last30days_research(topic: str, save_dir: Path) -> tuple[bool, dict]:
    """Invoke last30days skill for a topic. Returns (success, data)."""
    script = os.environ.get("LAST30DAYS_SCRIPT")
    if script:
        cmd = [
            sys.executable,
            script,
            topic,
            "--research-only",
            "--save-dir", str(save_dir),
            "--emit=json",
        ]
    else:
        cmd = [
            "last30days",
            "research",
            topic,
            "--save-dir", str(save_dir),
            "--format", "json",
            "--max-results", "20",
            "--recency-days", "30",
        ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            try:
                data = json.loads(result.stdout)
            except json.JSONDecodeError:
                return False, {"error": "Invalid JSON from last30days"}
            if script and "sources" not in data and "results" in data:
                data["sources"] = [
                    {"url": item.get("url", ""), "title": item.get("title", "")}
                    for item in data["results"]
                    if item.get("url")
                ]
                data["findings"] = data["results"]
                data["summary"] = "\n".join(
                    cluster.get("summary", "")
                    for cluster in data.get("clusters", [])
                    if cluster.get("summary")
                )
            return True, data
        else:
            return False, {"error": result.stderr or f"exit code {result.returncode}"}
    except FileNotFoundError:
        return False, {"error": "last30days not installed"}
    except subprocess.TimeoutExpired:
        return False, {"error": "last30days timed out"}
    except Exception as e:
        return False, {"error": str(e)}


def fallback_research() -> dict:
    """Simple fallback research using available resources."""
    return {
        "topic": topic,
        "summary": "No research result was collected for this topic.",
        "sources": [],
        "findings": [],
        "note": "No result is substituted for unavailable research.",
    }


def extract_gaps_and_discrepancies(data: dict, topic: str) -> tuple[list[str], list[str]]:
    """Analyze research data to find gaps and discrepancies."""
    gaps = []
    discrepancies = []

    sources = data.get("sources", [])
    findings = data.get("findings", data.get("results", []))

    expected = {"whitehouse.gov", "youtube.com/@WhiteHouse", "gdelt", "factbase"}
    found_domains = set()
    for src in sources:
        url = src.get("url", "")
        for dom in expected:
            if dom in url:
                found_domains.add(dom)
    missing = expected - found_domains
    if missing:
        gaps.append(f"Missing expected sources: {', '.join(sorted(missing))}")

    if len(data.get("findings", data.get("results", []))) > 1:
        discrepancies.append("Multiple findings detected; manual review recommended")

    return gaps, discrepancies


async def research_topic(topic: str, run_dir: Path) -> Finding:
    """Research a single topic and return a Finding."""
    success, data = run_last30days_research(topic, run_dir)
    if not success:
        data = fallback_research()

    gaps, discrepancies = extract_gaps_and_discrepancies(data, topic)

    source_count = len(data.get("sources", []))
    has_official = any("whitehouse.gov" in s.get("url", "") for s in data.get("sources", []))
    has_youtube = any("youtube.com" in s.get("url", "") for s in data.get("sources", []))

    confidence = min(1.0, (source_count / 10.0) + (0.2 if has_official else 0) + (0.15 if has_youtube else 0))

    return Finding(
        topic=topic,
        summary=data.get("summary", data.get("answer", "No summary available")),
        sources=data.get("sources", [])[:10],
        gaps=gaps,
        discrepancies=discrepancies,
        confidence=round(confidence, 2),
        generated_at=utc_now_iso(),
    )


def build_report(
    findings: list[Finding],
    *,
    topics: list[str] | None = None,
    status: str = "ok",
    degraded_reasons: list[str] | None = None,
) -> Report:
    return Report(
        generated_at=utc_now_iso(),
        topics=list(topics) if topics is not None else list(DEFAULT_TOPICS),
        findings=findings,
        run_id=run_id(),
        status=status,
        degraded_reasons=degraded_reasons or [],
    )


def _safe_external_url(value: Any) -> str | None:
    url = str(value or "").strip()
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.username or parsed.password:
        return None
    return url


def generate_index_html(report: Report) -> str:
    findings_html = ""
    for f in report.findings:
        sources_html = ""
        for s in f.sources:
            if not isinstance(s, dict):
                continue
            url = _safe_external_url(s.get("url"))
            if url is None:
                continue
            title = escape(str(s.get("title") or url))
            sources_html += (
                f'<li><a href="{escape(url, quote=True)}" target="_blank" '
                f'rel="noopener noreferrer">{title}</a></li>'
            )

        gaps_html = "".join(f"<li>{escape(str(g))}</li>" for g in f.gaps) or "<li>无</li>"
        disc_html = "".join(f"<li>{escape(str(d))}</li>" for d in f.discrepancies) or "<li>无</li>"

        conf_color = "green" if f.confidence >= 0.7 else "orange" if f.confidence >= 0.4 else "red"
        findings_html += f"""
        <details class="finding">
          <summary>{escape(str(f.topic))} <span class="confidence" style="color:{conf_color}">置信度: {f.confidence:.0%}</span></summary>
          <div class="summary">{escape(str(f.summary))}</div>
          <h4>来源 ({len(f.sources)})</h4>
          <ul>{sources_html}</ul>
          <h4>信息缺口</h4>
          <ul>{gaps_html}</ul>
          <h4>叙述差异</h4>
          <ul>{disc_html}</ul>
          <small>生成时间: {escape(str(f.generated_at))}</small>
        </details>
        """

    status = report.status if report.status in {"ok", "degraded"} else "degraded"
    status_badge = (
        "✅ 正常"
        if status == "ok"
        else f"⚠️ 降级 ({escape(', '.join(str(reason) for reason in report.degraded_reasons))})"
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>信息差公示处 - 多源信息差分析报告</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; line-height: 1.6; max-width: 900px; margin: 0 auto; padding: 20px; color: #333; }}
    header {{ border-bottom: 2px solid #eee; padding-bottom: 15px; margin-bottom: 20px; }}
    h1 {{ margin: 0; font-size: 1.8rem; }}
    .meta {{ color: #666; font-size: 0.9rem; }}
    .status {{ padding: 8px 12px; border-radius: 4px; display: inline-block; margin: 10px 0; }}
    .status.ok {{ background: #e8f5e9; color: #2e7d32; }}
    .status.degraded {{ background: #fff3e0; color: #e65100; }}
    details.finding {{ border: 1px solid #ddd; border-radius: 8px; padding: 15px; margin: 15px 0; background: #fafafa; }}
    details summary {{ cursor: pointer; font-weight: 600; font-size: 1.1rem; }}
    .confidence {{ float: right; font-weight: bold; }}
    .summary {{ white-space: pre-wrap; margin: 10px 0; padding: 10px; background: white; border-radius: 4px; }}
    h4 {{ margin-top: 15px; margin-bottom: 5px; font-size: 0.95rem; color: #444; }}
    ul {{ margin: 5px 0; padding-left: 20px; }}
    li {{ margin: 3px 0; }}
    a {{ color: #1976d2; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    footer {{ margin-top: 40px; padding-top: 20px; border-top: 1px solid #eee; color: #888; font-size: 0.85rem; }}
  </style>
</head>
<body>
  <header>
    <h1>信息差公示处</h1>
    <p class="meta">多源信息差分析报告 · 资源获取分析司</p>
    <div class="meta">运行 ID: {escape(str(report.run_id))} · 生成时间: {escape(str(report.generated_at))} · 状态: <span class="status {status}">{status_badge}</span></div>
  </header>
  <main>
    <section>
      <h2>本期关注主题 ({len(report.findings)})</h2>
      {findings_html}
    </section>
    <section>
      <h2>历史报告</h2>
      <p>历史快照归档于 <a href="history/">history/</a> 目录，按日期命名（YYYY-MM-DD.json）。</p>
    </section>
  </main>
  <footer>
    <p>数据来源：White House 官方 RSS、YouTube 频道、GDELT、Factbase 日程等钦定资源</p>
    <p>技能引擎：<a href="https://github.com/mvanhorn/last30days-skill" target="_blank">last30days-skill</a> (MIT License)</p>
    <p>数据治理：不存储原始社交媒体内容；仅保留摘要、引用链接与置信度。本地 SQLite 仅作运行期缓存。</p>
    <p>生成时间: {escape(str(report.generated_at))} · 运行 ID: {escape(str(report.run_id))} · Schema: gitsynonyms.infogap/1</p>
  </footer>
</body>
</html>"""


def write_index_html(report: Report) -> None:
    html = generate_index_html(report)
    INDEX_HTML.write_text(html, encoding="utf-8")


def write_last_run(report: Report) -> None:
    import json
    LAST_RUN.write_text(json.dumps({
        "run_id": report.run_id,
        "generated_at": report.generated_at,
        "status": report.status,
        "degraded_reasons": report.degraded_reasons,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    import argparse, asyncio
    global OUT_DIR, HISTORY_DIR, REPORT_JSON, INDEX_HTML, LAST_RUN
    parser = argparse.ArgumentParser(description="信息差公示处 - 生成多源信息差分析报告")
    parser.add_argument("--topics", nargs="+", default=DEFAULT_TOPICS, help="Research topics")
    parser.add_argument("--output-dir", default=str(OUT_DIR), help="Output directory")
    parser.add_argument("--strict", action="store_true", help="Fail on any quality gate")
    parser.add_argument("--dry-run", action="store_true", help="Do not write output files")
    args = parser.parse_args()

    OUT_DIR = Path(args.output_dir)
    HISTORY_DIR = OUT_DIR / "history"
    REPORT_JSON = OUT_DIR / "report.json"
    INDEX_HTML = OUT_DIR / "index.html"
    LAST_RUN = OUT_DIR / ".last-run.json"

    prereq_problems = check_prereqs()
    if prereq_problems and args.strict:
        print("Prerequisite check failed:", file=sys.stderr)
        for p in prereq_problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    print(f"[INFO] Starting infogap generation at {utc_now_iso()}")
    print(f"[INFO] Topics: {args.topics}")

    run_dir = OUT_DIR / ".run" / run_id()
    run_dir.mkdir(parents=True, exist_ok=True)

    degraded_reasons = []
    if prereq_problems:
        degraded_reasons.extend(prereq_problems)

    findings = []
    for topic in args.topics:
        print(f"[INFO] Researching: {topic}")
        try:
            finding = asyncio.run(research_topic(topic, run_dir))
            if finding.confidence < 0.6:
                degraded_reasons.append(f"Low confidence on '{topic}': {finding.confidence:.0%}")
            findings.append(finding)
        except Exception as e:
            print(f"[ERROR] Research failed for '{topic}': {e}", file=sys.stderr)
            degraded_reasons.append(f"Research error on '{topic}': {e}")
            findings.append(Finding(
                topic=topic,
                summary=f"Research failed: {e}",
                sources=[],
                gaps=[f"Research error: {e}"],
                discrepancies=[],
                confidence=0.0,
                generated_at=utc_now_iso(),
            ))

    status = "ok" if not degraded_reasons else "degraded"
    report = build_report(
        findings,
        topics=args.topics,
        status=status,
        degraded_reasons=degraded_reasons,
    )

    if args.strict and degraded_reasons:
        print(f"[FAIL] Quality gate failed: {degraded_reasons}", file=sys.stderr)
        return 1

    if not args.dry_run:
        write_report(report)
        write_index_html(report)
        write_last_run(report)
        print(f"[OK] Report written to {OUT_DIR}")
    else:
        print("[DRY-RUN] Would write report to", OUT_DIR)

    print(f"[INFO] Completed at {utc_now_iso()} - Status: {status}")
    return 0


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def write_report(report: Report) -> None:
    """Write report.json and history snapshot."""
    write_json(REPORT_JSON, asdict(report))
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    hist_path = HISTORY_DIR / f"{report.generated_at[:10]}.json"
    write_json(hist_path, asdict(report))


if __name__ == "__main__":
    raise SystemExit(main())
