#!/usr/bin/env python3
"""资源获取分析司 (resource acquisition and analysis department).

Keeps ``resources/registry.json`` honest under DIRECTIVE.md article 3:

    check     one URL per record, designated resources present, collectors real
    coverage  designated resource coverage and the shape of the registry
    list      the tracked resources, one per line

A URL resource is only "tracked" when its collector exists in
``scripts/monitoring/acquisition.py`` and points at the same URL. Harness
resources instead map to their own executable runner script.

Only the Python standard library is required.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Sequence
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = ROOT / "resources" / "registry.json"
DEFAULT_COLLECTOR = ROOT / "scripts" / "monitoring" / "acquisition.py"
SCHEMA = "gitsynonyms.resources.registry/1"
PRINCIPLE = "one-url-one-record"
OWNER = "资源获取分析司"
DESIGNATED_BY = "总控"

KINDS = {
    "youtube-channel",
    "rss-feed",
    "html-page",
    "json-api",
    "asset",
    "account",
    "harness",
}
TRUSTS = {"official", "wire", "press", "aggregator", "social", "unknown"}
STATUSES = {"active", "degraded", "retired", "successor-pending"}
RESOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:@/._-]*$")
CADENCE = re.compile(r"^(\d+)\s*([mhdw])$")
CADENCE_MINUTES = {"m": 1, "h": 60, "d": 1440, "w": 10080}
URL_TOKEN = re.compile(r"https?://", re.IGNORECASE)

# The directive names these two channels; they must stay designated and live.
REQUIRED_DESIGNATED_URLS = (
    "https://www.youtube.com/@WhiteHouse/streams",
    "https://www.youtube.com/@WhiteHouse/videos",
)


def parse_cadence(value: str) -> int:
    match = CADENCE.match((value or "").strip().lower())
    if not match:
        raise ValueError(f"invalid cadence: {value!r}")
    minutes = int(match.group(1)) * CADENCE_MINUTES[match.group(2)]
    if minutes <= 0:
        raise ValueError(f"cadence must be positive: {value!r}")
    return minutes


def load_registry(path: Path | str = DEFAULT_REGISTRY) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_collector_sources(
    script: Path | str = DEFAULT_COLLECTOR,
) -> dict[str, str]:
    """Import the acquisition module and read its source registry."""
    path = Path(script)
    spec = importlib.util.spec_from_file_location("resources_acquisition_sources", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import collector: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return {source.name: source.url for source in module.SOURCES}


def _url_problems(value: object, label: str) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return [f"{label}: url is required"]
    text = value.strip()
    problems = []
    if len(URL_TOKEN.findall(text)) != 1:
        problems.append(
            f"{label}: url must contain exactly one address (one URL per record)"
        )
    if any(mark in text for mark in (" ", "\t", "\n", ",", ";", "、")):
        problems.append(f"{label}: url contains separators or whitespace")
    parts = urlsplit(text)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        problems.append(f"{label}: url must be an absolute http(s) address")
    return problems


def _resource_problems(
    resource: object,
    *,
    known_ids: set[str],
    sources: dict[str, str],
    floor: int,
    ceiling: int,
) -> list[str]:
    if not isinstance(resource, dict):
        return ["resource entry must be an object"]
    identifier = str(resource.get("id") or "")
    label = identifier or "<missing id>"
    problems: list[str] = []
    if not RESOURCE_ID.match(identifier):
        problems.append(f"{label}: id must match {RESOURCE_ID.pattern}")
    if resource.get("kind") not in KINDS:
        problems.append(f"{label}: kind must be one of {sorted(KINDS)}")
    problems.extend(_url_problems(resource.get("url"), label))
    if not isinstance(resource.get("designated"), bool):
        problems.append(f"{label}: designated must be true or false")
    if resource.get("trust") not in TRUSTS:
        problems.append(f"{label}: trust must be one of {sorted(TRUSTS)}")
    if resource.get("status") not in STATUSES:
        problems.append(f"{label}: status must be one of {sorted(STATUSES)}")
    cadence = resource.get("cadence")
    try:
        minutes = parse_cadence(str(cadence or ""))
        if not floor <= minutes <= ceiling:
            problems.append(
                f"{label}: cadence {cadence!r} outside policy {floor}m..{ceiling}m"
            )
    except ValueError as error:
        problems.append(f"{label}: {error}")

    analysis = resource.get("analysis")
    if not isinstance(analysis, list) or not analysis:
        problems.append(f"{label}: analysis must list at least one purpose")
    else:
        for index, item in enumerate(analysis):
            if not isinstance(item, str) or not item.strip():
                problems.append(f"{label}: analysis[{index}] must be a non-empty string")

    collector = resource.get("collector")
    if not isinstance(collector, dict):
        problems.append(f"{label}: collector must be {{script, source}}")
    else:
        script = str(collector.get("script") or "")
        source_name = str(collector.get("source") or "")
        if not script:
            problems.append(f"{label}: collector.script is required")
        elif not (ROOT / script).is_file():
            problems.append(f"{label}: collector.script does not exist: {script}")
        if not source_name:
            problems.append(f"{label}: collector.source is required")
        elif resource.get("kind") == "harness":
            pass
        elif source_name not in sources:
            problems.append(
                f"{label}: collector.source {source_name!r} is not in the acquisition registry"
            )
        else:
            collected_url = sources[source_name]
            if str(resource.get("url") or "").strip() != collected_url:
                problems.append(
                    f"{label}: collector {source_name!r} collects {collected_url!r}, "
                    "which is not the registered url"
                )

    if resource.get("status") == "retired":
        successor = resource.get("successor")
        if not successor:
            problems.append(f"{label}: retired resources must name a successor")
        elif successor not in known_ids:
            problems.append(f"{label}: successor {successor!r} is not registered")
    return problems


def validate(registry: object, *, sources: dict[str, str] | None = None) -> list[str]:
    """Every registration defect, with the directive's article 3 as the rule."""
    if not isinstance(registry, dict):
        return ["registry root must be an object"]
    problems: list[str] = []
    if registry.get("schema") != SCHEMA:
        problems.append(f"schema must be {SCHEMA!r}")
    if registry.get("principle") != PRINCIPLE:
        problems.append(f"principle must be {PRINCIPLE!r}")
    if registry.get("owner") != OWNER:
        problems.append(f"owner must be {OWNER!r}")
    if registry.get("designated_by") != DESIGNATED_BY:
        problems.append(f"designated_by must be {DESIGNATED_BY!r}")

    policy = registry.get("cadence_policy")
    floor, ceiling = 5, 7 * 1440
    if not isinstance(policy, dict):
        problems.append("cadence_policy must be an object")
    else:
        try:
            floor = parse_cadence(str(policy.get("floor")))
            ceiling = parse_cadence(str(policy.get("ceiling")))
            if floor > ceiling:
                problems.append("cadence_policy floor must not exceed ceiling")
        except ValueError as error:
            problems.append(f"cadence_policy: {error}")

    resources = registry.get("resources")
    if not isinstance(resources, list) or not resources:
        return problems + ["resources must be a non-empty list"]

    if sources is None:
        try:
            sources = load_collector_sources()
        except Exception as error:  # noqa: BLE001 - report, never crash the check
            return problems + [f"cannot load collector sources: {error}"]

    known_ids = {
        str(item.get("id"))
        for item in resources
        if isinstance(item, dict) and item.get("id")
    }
    seen: set[str] = set()
    for resource in resources:
        problems.extend(
            _resource_problems(
                resource,
                known_ids=known_ids,
                sources=sources,
                floor=floor,
                ceiling=ceiling,
            )
        )
        if isinstance(resource, dict):
            identifier = resource.get("id")
            if isinstance(identifier, str) and identifier:
                if identifier in seen:
                    problems.append(f"{identifier}: duplicate resource id")
                seen.add(identifier)

    by_url = {
        str(item.get("url") or "").strip(): item
        for item in resources
        if isinstance(item, dict)
    }
    for required in REQUIRED_DESIGNATED_URLS:
        resource = by_url.get(required)
        if resource is None:
            problems.append(f"designated resource is missing: {required}")
            continue
        if resource.get("designated") is not True:
            problems.append(f"designated resource lost its flag: {required}")
        if resource.get("status") == "retired":
            problems.append(f"designated resource cannot be retired: {required}")
    return problems


def _counter(values) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value if value is not None else "unknown")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def coverage(registry: dict, *, sources: dict[str, str] | None = None) -> dict:
    resources = [
        resource
        for resource in registry.get("resources") or []
        if isinstance(resource, dict)
    ]
    if sources is None:
        try:
            sources = load_collector_sources()
        except Exception:  # noqa: BLE001
            sources = {}

    def trackable(resource: dict) -> bool:
        collector = resource.get("collector") or {}
        source_name = str(collector.get("source") or "")
        return (
            resource.get("status") == "active"
            and source_name in sources
            and str(resource.get("url") or "").strip() == sources[source_name]
        )

    designated = [
        resource for resource in resources if resource.get("designated") is True
    ]
    tracked = [resource for resource in designated if trackable(resource)]
    return {
        "resources": len(resources),
        "designated": len(designated),
        "trackable_designated": len(tracked),
        "designated_coverage": (
            round(len(tracked) / len(designated), 3) if designated else 0.0
        ),
        "designated_ids": sorted(str(resource.get("id")) for resource in designated),
        "by_kind": _counter(resource.get("kind") for resource in resources),
        "by_status": _counter(resource.get("status") for resource in resources),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="resources-check",
        description=(
            "资源获取分析司 - one URL per record, designated resources, "
            "collector mapping"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("check", "validate the resource registry"),
        ("coverage", "designated coverage and registry shape"),
        ("list", "list tracked resources"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument(
            "--registry", default=str(DEFAULT_REGISTRY), help="registry file"
        )
        command.add_argument("--json", action="store_true", dest="as_json")
    return parser


def _print_report(command: str, report: dict) -> None:
    info = report["coverage"]
    state = "OK" if report["ok"] else "FAILED"
    print(
        f"resources {state}: {info['resources']} records, "
        f"{info['designated']} designated, "
        f"designated coverage {info['designated_coverage']}"
    )
    if command == "list":
        for resource in report["resources"]:
            flag = "designated" if resource["designated"] else "supporting"
            print(
                f"  {resource['id']:<45} {resource['kind']:<15} "
                f"{flag:<10} {resource['status']}"
            )
    for problem in report["problems"]:
        print(f"  violation: {problem}")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        registry = load_registry(args.registry)
    except (OSError, json.JSONDecodeError) as error:
        print(f"error: cannot read registry: {error}", file=sys.stderr)
        return 2
    problems = validate(registry)
    report = {
        "ok": not problems,
        "problems": problems,
        "coverage": coverage(registry),
    }
    if args.command == "list":
        report["resources"] = [
            {
                "id": resource.get("id"),
                "kind": resource.get("kind"),
                "url": resource.get("url"),
                "designated": resource.get("designated"),
                "status": resource.get("status"),
            }
            for resource in registry.get("resources") or []
            if isinstance(resource, dict)
        ]
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_report(args.command, report)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())


