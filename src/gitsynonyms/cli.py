from __future__ import annotations

import argparse
import json
from typing import Iterable, List, Optional

from .core import DEFAULT_SYNONYMS, find_synonyms


def _render_synonyms(items: Iterable[str]) -> str:
    return ", ".join(items) if items else "No matches found."


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Look up Git command synonyms and common aliases.")
    parser.add_argument("terms", nargs="*", help="One or more Git commands or aliases to resolve.")
    parser.add_argument("--list", action="store_true", help="List the built-in set of command synonyms.")
    parser.add_argument("--json", action="store_true", help="Print output as JSON.")
    args = parser.parse_args(argv)

    if args.list:
        rows = {
            command: sorted(set(values))
            for command, values in sorted(DEFAULT_SYNONYMS.items())
        }
        if args.json:
            print(json.dumps(rows, indent=2, sort_keys=True))
        else:
            for command, values in rows.items():
                print(f"{command}: {_render_synonyms(values)}")
        return 0

    if not args.terms:
        parser.print_help()
        return 0

    results = []
    for term in args.terms:
        values = find_synonyms(term)
        results.append({"query": term, "synonyms": values})

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for item in results:
            print(f"{item['query']}: {_render_synonyms(item['synonyms'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
