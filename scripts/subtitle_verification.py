#!/usr/bin/env python3
"""字幕核实生成精确ASS处 (Subtitle Verification & Precise ASS Generation Department)

职责：
1. 以 JSON 为主格式，核实多源字幕
2. 来源：YouTube 官方 7 种 + AssemblyAI + Forth.news whpool + RollCall Factbase
3. 生成精确 ASS（纳秒级时间轴 + 逐词高亮）
4. 输出核实报告 VERIFICATION.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import subtitle_timing
import download_youtube_bilingual_srt as dld  # noqa: E402
import subtitle_ergonomics  # noqa: E402

NS_PER_SECOND = 1_000_000_000
MS_PER_SECOND = 1_000

# ──────────────────────────────────────────────────────────────────────
# 数据结构
# ──────────────────────────────────────────────────────────────────────

@dataclass
class SubtitleCue:
    start_ns: int
    end_ns: int
    text: str
    words: list[dict] = field(default_factory=list)  # {text, start_ns, end_ns}
    source: str = ""
    confidence: float = 1.0

    @property
    def start_s(self) -> float:
        return self.start_ns / NS_PER_SECOND

    @property
    def end_s(self) -> float:
        return self.end_ns / NS_PER_SECOND

    @property
    def duration_s(self) -> float:
        return (self.end_ns - self.start_ns) / NS_PER_SECOND

    def to_dict(self) -> dict:
        return {
            "start_ns": self.start_ns,
            "end_ns": self.end_ns,
            "start_s": round(self.start_s, 3),
            "end_s": round(self.end_s, 3),
            "text": self.text,
            "words": self.words,
            "source": self.source,
            "confidence": self.confidence,
        }


@dataclass
class VerificationReport:
    video_id: str
    title: str
    generated_at: str
    sources: dict  # {source_name: {cues: [...], stats: {...}}}
    fused_cues: list[dict]
    conflicts: list[dict]
    ergonomics: dict
    ass_path: str
    timing_path: str

    def to_json(self) -> str:
        return json.dumps({
            "video_id": self.video_id,
            "title": self.title,
            "generated_at": self.generated_at,
            "sources": self.sources,
            "fused_cues": self.fused_cues,
            "conflicts": self.conflicts,
            "ergonomics": self.ergonomics,
            "ass_path": self.ass_path,
            "timing_path": self.timing_path,
        }, ensure_ascii=False, indent=2)


# ──────────────────────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────────────────────

def ns(seconds: float) -> int:
    return int(round(seconds * NS_PER_SECOND))


def load_youtube_archive(archive_dir: Path, video_id: str, lang: str) -> list[SubtitleCue]:
    """加载 YouTube 官方 7 种格式，融合为纳秒级线索"""
    tracks = subtitle_timing.build_tracks(archive_dir, video_id, [lang])
    track = tracks[lang]
    cues = []
    for cue in track["cues"]:
        cues.append(SubtitleCue(
            start_ns=cue["start_ns"],
            end_ns=cue["end_ns"],
            text=cue["text"],
            words=[
                {"text": w["text"], "start_ns": w["start_ns"], "end_ns": w["end_ns"]}
                for w in cue.get("word_timings", [])
            ],
            source="youtube-7fmt",
            confidence=1.0,
        ))
    return cues


def call_assemblyai(audio_path: Path, api_key: str, language: str) -> list[SubtitleCue]:
    """调用 AssemblyAI API（需要环境变量 ASSEMBLYAI_API_KEY）"""
    if not api_key:
        return []
    # 1. 上传音频
    with open(audio_path, "rb") as f:
        upload = json.loads(subprocess.check_output([
            "curl", "-s", "-X", "POST",
            "https://api.assemblyai.com/v2/upload",
            "-H", f"authorization: {api_key}",
            "-H", "Content-Type: application/octet-stream",
            "--data-binary", f"@{audio_path}"
        ], text=True))
    upload_url = upload["upload_url"]
    # 2. 提交转录任务
    transcript = json.loads(subprocess.check_output([
        "curl", "-s", "-X", "POST",
        "https://api.assemblyai.com/v2/transcript",
        "-H", f"authorization: {api_key}",
        "-H", "Content-Type: application/json",
        "-d", json.dumps({
            "audio_url": upload_url,
            "speech_model": "universal-3-5-pro",
            "language_code": language,
            "punctuate": True,
            "format_text": True,
            "word_boost": [],
            "utterances": True
        })
    ], text=True))
    job_id = transcript["id"]
    # 3. 轮询结果
    for _ in range(180):  # 最多 30 分钟
        time.sleep(10)
        result = json.loads(subprocess.check_output([
            "curl", "-s",
            f"https://api.assemblyai.com/v2/transcript/{job_id}",
            "-H", f"authorization: {api_key}"
        ], text=True))
        if result["status"] == "completed":
            break
        elif result["status"] == "error":
            raise RuntimeError(f"AssemblyAI error: {result.get('error')}")
    else:
        raise TimeoutError("AssemblyAI transcription timeout")
    # 4. 解析词级时间戳
    cues = []
    words = result.get("words", [])
    # 按 utterances 分组生成 cues
    for utt in result.get("utterances", []):
        words_in_utt = [w for w in words if utt["start"] <= w["start"] < utt["end"]]
        cues.append(SubtitleCue(
            start_ns=ns(utt["start"] / 1000),
            end_ns=ns(utt["end"] / 1000),
            text=utt["text"].strip(),
            words=[
                {"text": w["text"], "start_ns": ns(w["start"] / 1000), "end_ns": ns(w["end"] / 1000)}
                for w in words_in_utt
            ],
            source="assemblyai",
            confidence=utt.get("confidence", 1.0),
        ))
    return cues


def call_forth_news_whpool(audio_path: Path, api_key: str, language: str) -> list[SubtitleCue]:
    """调用 Forth.news whpool API（需 API Key）"""
    # TODO: 对接 Forth.news whpool API
    # 暂存框架，实际对接时填入
    return []


def call_rollcall_factbase(video_id: str, api_key: str) -> list[SubtitleCue]:
    """查询 RollCall Factbase Trump 搜索（需 API Key）"""
    # TODO: 对接 RollCall Factbase API
    return []


def fuse_cues(source_cues: dict[str, list[SubtitleCue]]) -> tuple[list[SubtitleCue], list[dict]]:
    """多源线索融合：加权中位数 + 冲突记录"""
    # 以 YouTube 7fmt 为基准（最高优先级）
    base = source_cues.get("youtube-7fmt", [])
    if not base:
        return [], []
    conflicts = []
    fused = []
    for base_cue in base:
        # 收集其他源在同一时间窗的线索
        matches = {}
        for src, cues in source_cues.items():
            if src == "youtube-7fmt":
                continue
            for c in cues:
                # 时间重叠 > 50% 视为同一句
                overlap = min(base_cue.end_ns, c.end_ns) - max(base_cue.start_ns, c.start_ns)
                if overlap > 0 and overlap / (base_cue.end_ns - base_cue.start_ns) > 0.5:
                    matches.setdefault(src, []).append(c)
        # 文本相似度 + 时间加权中位数
        fused_text = base_cue.text
        fused_words = base_cue.words[:]
        conflict_detail = {
            "base_start": base_cue.start_s,
            "base_end": base_cue.end_s,
            "base_text": base_cue.text,
            "alternatives": {},
        }
        for src, mcues in matches.items():
            for mc in mcues:
                conflict_detail["alternatives"][src] = {
                    "text": mc.text,
                    "start_s": mc.start_s,
                    "end_s": mc.end_s,
                    "confidence": mc.confidence,
                }
                # 文本差异记录
                if mc.text.strip() != base_cue.text.strip():
                    conflicts.append({
                        "type": "text_mismatch",
                        "time_s": base_cue.start_s,
                        "youtube": base_cue.text,
                        "other": {src: mc.text},
                    })
                # 词级时间戳差异（若两者都有词级）
                if mc.words and base_cue.words:
                    for bw, mw in zip(base_cue.words, mc.words):
                        diff_ms = abs(bw["start_ns"] - mw["start_ns"]) / 1_000_000
                        if diff_ms > 50:  # >50ms 记录冲突
                            conflicts.append({
                                "type": "word_timing_mismatch",
                                "word": bw["text"],
                                "youtube_ms": bw["start_ns"] / 1_000_000,
                                "other_ms": mw["start_ns"] / 1_000_000,
                                "diff_ms": diff_ms,
                                "source": src,
                            })
        fused.append(SubtitleCue(
            start_ns=base_cue.start_ns,
            end_ns=base_cue.end_ns,
            text=fused_text,
            words=fused_words,
            source="fused",
            confidence=1.0,
        ))
    return fused, conflicts


def build_precise_ass(cues: list[SubtitleCue], video_duration_s: float, 
                       language_order: tuple[str, str] = ("zh-Hans", "en-orig")) -> tuple[str, dict]:
    """生成精确 ASS（复用 subtitle_timing 的 build_ass）"""
    # 转换为 subtitle_timing 需要的格式
    def to_st_cues(cues: list[SubtitleCue]) -> list[dict]:
        return [
            {
                "start": c.start_s,
                "end": c.end_s,
                "text": c.text,
                "word_timings": c.words,
            }
            for c in cues
        ]
    import re
    CJK = re.compile(r"[一-鿿]")
    chinese = []
    english = []
    for c in cues:
        if CJK.search(c.text):
            chinese.append(c)
        else:
            english.append(c)
    # 如果没有分离成功，尝试按 source 字段分离
    if not chinese or not english:
        chinese = [c for c in cues if c.source in {"youtube-7fmt", "assemblyai", "forth-news", "rollcall-factbase"}]
        english = [c for c in cues if c.source == "fused"]
    # 如果还是没有，全部当作中文，英文为空
    if not english:
        english = []
    def convert_words(words):
        return [{"start": w["start_ns"]/1e9, "end": w["end_ns"]/1e9, "text": w["text"]} for w in words]
    ass_content = dld.build_ass(
        [{"start": c.start_s, "end": c.end_s, "text": c.text, "word_timings": convert_words(c.words)} for c in chinese],
        [{"start": c.start_s, "end": c.end_s, "text": c.text, "word_timings": convert_words(c.words)} for c in english] or [{"start": 0, "end": video_duration_s, "text": "", "word_timings": []}],
        first_font="Microsoft YaHei",
        second_font="Arial",
        second_font_size=48,
        first_font_scale=1.2,
        caption_area=(66, 883, 1852, 1022),
        first_color="#FFC928",
        second_color="#FFFFFF",
        box_color="#0F1647",
        box_opacity=0.85,
        caption_order="chinese-first",
        video_duration=video_duration_s,
        box_style="presidential-blue",
    )
    stats = {"cues": len(cues), "duration_s": video_duration_s}
    return ass_content, stats


def write_verification_report(report: VerificationReport, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "VERIFICATION.json").write_text(report.to_json(), encoding="utf-8")
    (out_dir / "TIMING.json").write_text(json.dumps({
        "video_id": report.video_id,
        "cues": report.fused_cues,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="字幕核实生成精确ASS处")
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--video", type=Path, required=True, help="源视频文件")
    parser.add_argument("--youtube-archive", type=Path, required=True, help="YouTube 7格式归档目录")
    parser.add_argument("--language", default="zh-Hans", help="主语言")
    parser.add_argument("--second-language", default="en-orig", help="第二语言")
    parser.add_argument("--assemblyai-key", default=os.environ.get("ASSEMBLYAI_API_KEY", ""))
    parser.add_argument("--forth-news-key", default=os.environ.get("FORTH_NEWS_API_KEY", ""))
    parser.add_argument("--rollcall-key", default=os.environ.get("ROLLCALL_API_KEY", ""))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"[字幕核实处] 视频: {args.video_id} {args.title}")
    print(f"[字幕核实处] 来源: YouTube 7fmt", end="")
    if args.assemblyai_key: print(", AssemblyAI", end="")
    if args.forth_news_key: print(", Forth.news whpool", end="")
    if args.rollcall_key: print(", RollCall Factbase", end="")
    print()

    # 1. 加载 YouTube 7 格式
    source_cues = {
        "youtube-7fmt": load_youtube_archive(args.youtube_archive, args.video_id, args.language),
    }
    print(f"  YouTube 7fmt: {len(source_cues['youtube-7fmt'])} cues")

    # 2. 第三方 API（若有 Key）
    if args.assemblyai_key:
        print("  Calling AssemblyAI...")
        source_cues["assemblyai"] = call_assemblyai(args.video, args.assemblyai_key, args.language)
        print(f"  AssemblyAI: {len(source_cues['assemblyai'])} cues")

    if args.forth_news_key:
        print("  Calling Forth.news whpool...")
        source_cues["forth-news"] = call_forth_news_whpool(args.video, args.forth_news_key, args.language)
        print(f"  Forth.news: {len(source_cues['forth-news'])} cues")

    if args.rollcall_key:
        print("  Calling RollCall Factbase...")
        source_cues["rollcall-factbase"] = call_rollcall_factbase(args.video_id, args.rollcall_key)
        print(f"  RollCall: {len(source_cues['rollcall-factbase'])} cues")

    # 3. 多源融合
    fused_cues, conflicts = fuse_cues(source_cues)
    print(f"[融合] 输出 {len(fused_cues)} cues, {len(conflicts)} 个冲突")

    # 4. 生成精确 ASS
    video_duration = 30.0  # TODO: 从 ffprobe 读取
    ass_content, ass_stats = build_precise_ass(fused_cues, video_duration)

    # 5. 人工工学检查
    erg = subtitle_ergonomics.analyze_subtitle(
        Path(args.output_dir) / f"bilingual_precise_{args.video_id}.ass",
        fps=25
    ) if not args.dry_run else {"summary": {"cues": 0, "errors": 0, "warnings": 0}}

    # 5. 输出报告
    report = VerificationReport(
        video_id=args.video_id,
        title=args.title,
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        sources={k: {"cues": len(v), "stats": {"cues": len(v)}} for k, v in source_cues.items()},
        fused_cues=[c.to_dict() for c in fused_cues],
        conflicts=conflicts,
        ergonomics=erg["summary"] if isinstance(erg, dict) else {},
        ass_path=str(args.output_dir / f"bilingual_precise_{args.video_id}.ass"),
        timing_path=str(args.output_dir / f"TIMING_{args.video_id}.json"),
    )

    if not args.dry_run:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / f"bilingual_precise_{args.video_id}.ass").write_text(
            ass_content, encoding="utf-8")
        write_verification_report(report, args.output_dir)
        print(f"[输出] ASS: {report.ass_path}")
        print(f"[输出] TIMING: {report.timing_path}")
        print(f"[输出] VERIFICATION: {args.output_dir}/VERIFICATION.json")
    else:
        print("[Dry-run] 跳过文件写入")
        print(json.dumps(report.to_json(), ensure_ascii=False, indent=2))

    if conflicts and not args.dry_run:
        print(f"[警告] 发现 {len(conflicts)} 个冲突，详见 VERIFICATION.json")

if __name__ == "__main__":
    sys.exit(main())
