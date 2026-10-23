#!/usr/bin/env python3
"""全量代理检索词矩阵：list² 展开（list of lists squared）。

The proxy index is built from a *list of lists*: thousands of repositories, each
holding several proxy files.  Discovery has the same shape -- a list of proxy
terms crossed with a list of network/protocol terms, in Chinese and English,
including synonyms and near-synonyms.  This module enumerates that matrix in
full instead of hand-picking a few queries:

    heads      proxy / 代理 / 代理ip / 代理池 / 节点 / 机场 / 梯子 ...
    networks   http / https / socks4 / socks5 / shadowsocks / vmess / trojan /
               hysteria / tuic / wireguard / residential / datacenter ...
    qualifiers free / 免费 / fast / 高速 / stable / 稳定 / verified / 已验证 ...

Every pair on every axis is generated in both orders, and every triple
(network x qualifier x head) is generated in the two canonical orders.  The
result is deterministic, deduplicated and hashable, so a scan can prove that it
covered the matrix rather than claiming it.

Discovery is rate limited by the GitHub search API (30 requests/minute for an
authenticated account), so the matrix is traversed with a resumable cursor:
each run consumes a budget of queries, records the cursor, and reports the
coverage ratio.  Nothing is sampled away; a cycle ends only when the cursor has
walked the whole list.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

SCHEMA = "gitsynonyms.proxy.query-matrix/1"


# --- axis definitions -------------------------------------------------------
# Chinese and English are kept as separate axes: a Chinese speaker searching for
# 免费代理 does not type "free proxy", and both surfaces exist on GitHub.

HEAD_TERMS: dict[str, tuple[str, ...]] = {
    "en": (
        "proxy",
        "proxies",
        "proxy list",
        "proxy-list",
        "proxylist",
        "proxy pool",
        "proxy server",
        "public proxy",
        "free proxy",
    ),
    "zh": (
        "代理",
        "代理ip",
        "ip代理",
        "代理服务器",
        "代理池",
        "代理列表",
        "免费代理",
        "公开代理",
        "节点",
        "节点订阅",
        "机场",
        "梯子",
        "加速器",
        "科学上网",
    ),
}

NETWORK_TERMS: dict[str, tuple[str, ...]] = {
    "en": (
        "http",
        "https",
        "socks4",
        "socks5",
        "socks5h",
        "socks",
        "shadowsocks",
        "ss",
        "ssr",
        "vmess",
        "vless",
        "trojan",
        "hysteria",
        "hysteria2",
        "tuic",
        "wireguard",
        "mtproto",
        "ftp",
        "ipv4",
        "ipv6",
        "residential",
        "datacenter",
        "mobile",
        "elite",
        "anonymous",
        "transparent",
        "tunnel",
        "vpn",
        "node",
        "airport",
    ),
    "zh": (
        "http代理",
        "https代理",
        "socks4代理",
        "socks5代理",
        "ss代理",
        "ssr代理",
        "vmess节点",
        "vless节点",
        "trojan节点",
        "hysteria节点",
        "wireguard节点",
        "机场",
        "节点",
        "线路",
        "原生ip",
        "家宽",
        "住宅ip",
        "机房ip",
        "移动ip",
        "匿名代理",
        "高匿代理",
        "透明代理",
        "隧道代理",
        "加速器",
        "梯子",
    ),

}

QUALIFIER_TERMS: dict[str, tuple[str, ...]] = {
    "en": (
        "free",
        "fast",
        "stable",
        "latest",
        "daily",
        "updated",
        "verified",
        "working",
        "alive",
        "valid",
        "live",
        "fresh",
        "anonymous",
        "elite",
        "residential",
        "unlimited",
        "realtime",
        "high speed",
        "paid",
        "private",
        "china",
        "usa",
        "global",
    ),
    "zh": (
        "免费",
        "高速",
        "稳定",
        "最新",
        "每日更新",
        "实时",
        "可用",
        "已验证",
        "长期",
        "匿名",
        "高匿",
        "住宅",
        "不限量",
        "国内",
        "海外",
        "全球",
        "付费",
        "私有",
        "秒连",
        "低延迟",
    ),
}

# Near-synonyms and transliterations that the literal axes above do not carry.
# These are searched as their own query family instead of being silently folded
# into an English term.
NEAR_SYNONYM_TERMS: dict[str, tuple[str, ...]] = {
    "en": (
        "proxies list",
        "proxy scraper",
        "proxy checker",
        "proxy grabber",
        "ip:port list",
        "socks proxy list",
        "https proxy list",
        "elite proxy list",
        "anonymous proxy list",
        "rotating proxy",
        "proxy subscription",
        "free vpn subscription",
        "airport subscription",
    ),
    "zh": (
        "免费节点",
        "免费机场",
        "免费订阅",
        "节点分享",
        "翻墙节点",
        "代理节点",
        "ssr订阅",
        "clash订阅",
        "v2ray订阅",
        "小火箭订阅",
        "科学上网节点",
        "国内代理",
        "ip地址代理",
    ),
}

# Pinyin spellings are how a large part of the Chinese proxy ecosystem is
# tagged on GitHub (`daili`, `jichang`, `mianfeijiedian`).
PINYIN_TOPICS: tuple[str, ...] = (
    "daili",
    "mianfeidaili",
    "jichang",
    "jiedian",
    "mianfeijiedian",
    "fq",
    "fanqiang",
    "kexueshangwang",
)

# Every pair is emitted in both orders; every triple in these two orders.
PAIR_TEMPLATES = ("{a} {b}", "{b} {a}")
TRIPLE_TEMPLATES = ("{q} {n} {h}", "{h} {n} {q}")

TOPIC_TERMS: tuple[str, ...] = (
    "proxy",
    "proxies",
    "proxy-list",
    "proxy-pool",
    "proxy-scraper",
    "proxy-checker",
    "free-proxy",
    "free-proxy-list",
    "public-proxy",
    "socks5",
    "socks5-proxy",
    "shadowsocks",
    "ssr",
    "vmess",
    "vless",
    "trojan",
    "hysteria",
    "tuic",
    "wireguard",
    "v2ray",
    "clash",
    "subscription",
    "airport",
    "vpn",
)

# Conventional paths inside a proxy-list repository.  Crossed with every
# discovered repository this is the second axis of the list² source expansion.
FILE_PATTERNS: tuple[str, ...] = (
    "http.txt",
    "https.txt",
    "socks4.txt",
    "socks5.txt",
    "socks5h.txt",
    "socks.txt",
    "all.txt",
    "proxies.txt",
    "proxy.txt",
    "proxy_list.txt",
    "list.txt",
    "working.txt",
    "checked.txt",
    "valid.txt",
    "live.txt",
    "alive.txt",
    "ip.txt",
    "data.txt",
    "raw.txt",
    "proxies/http.txt",
    "proxies/https.txt",
    "proxies/socks5.txt",
    "proxies/socks4.txt",
    "proxies/all.txt",
    "proxies/protocols/http/data.txt",
    "proxies/protocols/socks5/data.txt",
    "protocols/http/data.txt",
    "protocols/socks5/data.txt",
    "http/http.txt",
    "socks5/socks5.txt",
    "output/all.txt",
    "dist/proxies/all.txt",
    "dist/all.txt",
)


def _ordered(values: tuple[str, ...] | list[str]) -> list[str]:
    """Stable de-duplication that keeps the axis order readable."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = " ".join(value.split()).casefold()
        if key and key not in seen:
            seen.add(key)
            result.append(" ".join(value.split()))
    return result


def _record(family: str, lang: str, template: str, parts: dict[str, str]) -> dict:
    return {
        "query": template.format(**parts),
        "family": family,
        "lang": lang,
        "template": template,
        "terms": dict(parts),
    }


def build_queries() -> list[dict]:
    """Enumerate the whole matrix: pairs (both orders) then triples."""
    records: list[dict] = []
    for lang in ("en", "zh"):
        heads = _ordered(HEAD_TERMS[lang])
        networks = _ordered(NETWORK_TERMS[lang])
        qualifiers = _ordered(QUALIFIER_TERMS[lang])
        for network in networks:
            for head in heads:
                for template in PAIR_TEMPLATES:
                    records.append(
                        _record("network-head", lang, template, {"a": network, "b": head})
                    )
        for qualifier in qualifiers:
            for head in heads:
                for template in PAIR_TEMPLATES:
                    records.append(
                        _record("qualifier-head", lang, template, {"a": qualifier, "b": head})
                    )
        for network in networks:
            for qualifier in qualifiers:
                for template in PAIR_TEMPLATES:
                    records.append(
                        _record(
                            "network-qualifier", lang, template, {"a": network, "b": qualifier}
                        )
                    )
        for qualifier in qualifiers:
            for network in networks:
                for head in heads:
                    for template in TRIPLE_TEMPLATES:
                        records.append(
                            _record(
                                "network-qualifier-head",
                                lang,
                                template,
                                {"q": qualifier, "n": network, "h": head},
                            )
                        )
        for term in _ordered(NEAR_SYNONYM_TERMS[lang]):
            records.append(_record("near-synonym", lang, "{a}", {"a": term}))

    # Bilingual search: a Chinese speaker types 免费 socks5 代理, an English
    # speaker types free 代理池.  Every cross-language pair is emitted in both
    # orders, and the realistic mixed triple (zh qualifier + en network +
    # zh head) is emitted in both canonical orders.
    zh_heads, en_heads = _ordered(HEAD_TERMS["zh"]), _ordered(HEAD_TERMS["en"])
    zh_nets, en_nets = _ordered(NETWORK_TERMS["zh"]), _ordered(NETWORK_TERMS["en"])
    zh_quals = _ordered(QUALIFIER_TERMS["zh"])
    en_quals = _ordered(QUALIFIER_TERMS["en"])
    cross_pairs = (
        ("network-head", en_nets, zh_heads),
        ("network-head", zh_nets, en_heads),
        ("qualifier-head", zh_quals, en_heads),
        ("qualifier-head", en_quals, zh_heads),
        ("network-qualifier", en_nets, zh_quals),
        ("network-qualifier", zh_nets, en_quals),
    )
    for family, left, right in cross_pairs:
        for a in left:
            for b in right:
                for template in PAIR_TEMPLATES:
                    records.append(_record(family + "-mixed", "mixed", template, {"a": a, "b": b}))
    for qualifier in zh_quals:
        for network in en_nets:
            for head in zh_heads:
                for template in TRIPLE_TEMPLATES:
                    records.append(
                        _record(
                            "network-qualifier-head-mixed",
                            "mixed",
                            template,
                            {"q": qualifier, "n": network, "h": head},
                        )
                    )

    deduped: list[dict] = []
    seen: set[str] = set()
    for record in records:
        key = record["query"].casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def build_topics() -> list[str]:
    topics = [f"topic:{term}" for term in _ordered(TOPIC_TERMS)]
    topics.extend(f"topic:{term}" for term in _ordered(PINYIN_TOPICS))
    return topics


def matrix_digest(queries: list[dict], topics: list[str]) -> str:
    payload = json.dumps(
        {"queries": [item["query"] for item in queries], "topics": topics},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()



def build_matrix() -> dict:
    queries = build_queries()
    topics = build_topics()
    families: dict[str, int] = {}
    languages: dict[str, int] = {}
    for item in queries:
        families[item["family"]] = families.get(item["family"], 0) + 1
        languages[item["lang"]] = languages.get(item["lang"], 0) + 1
    return {
        "schema": SCHEMA,
        "generated_by": "proxyindex/query_matrix.py",
        "axes": {
            "head_terms": {lang: list(terms) for lang, terms in HEAD_TERMS.items()},
            "network_terms": {lang: list(terms) for lang, terms in NETWORK_TERMS.items()},
            "qualifier_terms": {
                lang: list(terms) for lang, terms in QUALIFIER_TERMS.items()
            },
            "near_synonym_terms": {
                lang: list(terms) for lang, terms in NEAR_SYNONYM_TERMS.items()
            },
            "pinyin_topics": list(PINYIN_TOPICS),
            "topic_terms": list(TOPIC_TERMS),
            "file_patterns": list(FILE_PATTERNS),
            "pair_templates": list(PAIR_TEMPLATES),
            "triple_templates": list(TRIPLE_TEMPLATES),
        },
        "counts": {
            "queries": len(queries),
            "topics": len(topics),
            "by_family": dict(sorted(families.items())),
            "by_language": dict(sorted(languages.items())),
            "axis_sizes": {
                lang: {
                    "heads": len(_ordered(HEAD_TERMS[lang])),
                    "networks": len(_ordered(NETWORK_TERMS[lang])),
                    "qualifiers": len(_ordered(QUALIFIER_TERMS[lang])),
                }
                for lang in ("en", "zh")
            },
        },
        "digest": matrix_digest(queries, topics),
        "queries": queries,
        "topics": topics,
    }


def write_matrix(path: Path | str) -> dict:
    matrix = build_matrix()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(matrix, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return matrix


def load_cursor(path: Path | str, total: int, digest: str) -> dict:
    """Resumable discovery cursor; a changed matrix restarts at zero."""
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    if not isinstance(state, dict) or state.get("digest") != digest:
        state = {"digest": digest, "cursor": 0, "cycles": 0}
    cursor = int(state.get("cursor") or 0)
    if total and cursor > total:
        cursor = 0
    return {
        "digest": digest,
        "cursor": cursor,
        "cycles": int(state.get("cycles") or 0),
        "total": total,
    }


def take_batch(matrix: dict, state: dict, limit: int) -> tuple[list[dict], dict]:
    """Take the next ``limit`` queries and advance the cursor, wrapping at end."""
    items: list[dict] = [
        {"query": item["query"], "kind": "keyword"} for item in matrix["queries"]
    ]
    items.extend({"query": topic, "kind": "topic"} for topic in matrix["topics"])
    total = len(items)
    if limit <= 0 or total == 0:
        return [], {**state, "total": total}
    cursor = int(state.get("cursor") or 0) % total
    batch: list[dict] = []
    cycles = int(state.get("cycles") or 0)
    for _ in range(min(limit, total)):
        batch.append(items[cursor])
        cursor += 1
        if cursor >= total:
            cursor = 0
            cycles += 1
    return batch, {
        "digest": state.get("digest", ""),
        "cursor": cursor,
        "cycles": cycles,
        "total": total,
    }


def coverage(state: dict) -> float:
    total = int(state.get("total") or 0)
    if total <= 0:
        return 0.0
    return round((int(state.get("cursor") or 0) % total) / total, 6)



def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="enumerate the proxy query matrix")
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parent / "query-matrix.json"),
        help="matrix output path",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    matrix = write_matrix(args.out)
    if args.as_json:
        print(json.dumps(matrix["counts"], ensure_ascii=False, indent=2))
        return 0
    counts = matrix["counts"]
    print(
        f"queries: {counts['queries']} topics: {counts['topics']} "
        f"digest: {matrix['digest'][:12]}"
    )
    for family, count in counts["by_family"].items():
        print(f"  {family:<26} {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
