from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Dict, Iterable, List, Optional

DEFAULT_SYNONYMS: Dict[str, List[str]] = {
    "status": ["status", "st", "info"],
    "checkout": ["checkout", "co", "switch"],
    "branch": ["branch", "br", "heads"],
    "commit": ["commit", "ci", "checkpoint"],
    "log": ["log", "history", "recent"],
    "add": ["add", "stage", "track"],
    "diff": ["diff", "changes", "difference"],
    "reset": ["reset", "undo", "rewind"],
    "restore": ["restore", "revert", "recover"],
    "pull": ["pull", "update", "fetch-merge"],
    "push": ["push", "publish", "send"],
    "fetch": ["fetch", "download", "sync"],
    "merge": ["merge", "join", "combine"],
    "rebase": ["rebase", "replay"],
    "tag": ["tag", "label", "mark"],
    "clone": ["clone", "copy", "duplicate"],
    "remote": ["remote", "origin", "upstream"],
    "stash": ["stash", "temporarily-save", "shelve"],
    "cherry-pick": ["cherry-pick", "pick"],
    "blame": ["blame", "annotate"],
    "clean": ["clean", "remove-untracked"],
    "config": ["config", "settings"],
}


def normalize_term(term: str) -> str:
    """Normalize a user input into a canonical Git term."""
    if term is None:
        return ""
    value = term.strip().lower()
    value = value.replace("git ", "")
    value = value.replace("-", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _build_reverse_index(synonyms: Optional[Dict[str, List[str]]] = None) -> Dict[str, List[str]]:
    mapping = synonyms or DEFAULT_SYNONYMS
    reverse: Dict[str, List[str]] = {}
    for canonical, aliases in mapping.items():
        for alias in aliases:
            reverse.setdefault(alias.lower(), []).append(canonical)
        reverse.setdefault(canonical.lower(), []).append(canonical)
    for key, values in reverse.items():
        ordered = []
        seen = set()
        for value in values:
            if value not in seen:
                ordered.append(value)
                seen.add(value)
        reverse[key] = ordered
    return reverse


class GitSynonymResolver:
    """Resolve Git aliases and close synonyms to one or more canonical commands."""

    def __init__(self, synonyms: Optional[Dict[str, List[str]]] = None):
        self.synonyms = dict(synonyms or DEFAULT_SYNONYMS)
        self._reverse = _build_reverse_index(self.synonyms)

    def resolve(self, term: str) -> List[str]:
        normalized = normalize_term(term)
        if not normalized:
            return []
        direct = self._reverse.get(normalized, [])
        if direct:
            return direct

        candidates = []
        for key in self._reverse:
            score = SequenceMatcher(None, normalized, key).ratio()
            if normalized.startswith(key) or key.startswith(normalized):
                score += 0.4
            if normalized in key or key in normalized:
                score += 0.3
            if normalized[:2] == key[:2]:
                score += 0.15
            candidates.append((score, key))

        ranked = sorted(candidates, key=lambda item: item[0], reverse=True)
        suggestions: List[tuple[float, str]] = []
        seen = set()
        for score, key in ranked[:3]:
            if key == normalized:
                continue
            for value in self._reverse.get(key, []):
                if value not in seen:
                    suggestions.append((score, value))
                    seen.add(value)
        if suggestions:
            ordered = sorted(suggestions, key=lambda item: item[0], reverse=True)
            return [value for _, value in ordered]
        return []

    def lookup(self, term: str) -> List[str]:
        canonical = self.resolve(term)
        if not canonical:
            return []
        result = []
        seen = set()
        for name in canonical:
            options = self.synonyms.get(name, [])
            for value in options:
                if value not in seen:
                    result.append(value)
                    seen.add(value)
        return result


def find_synonyms(term: str, *, synonyms: Optional[Dict[str, List[str]]] = None) -> List[str]:
    resolver = GitSynonymResolver(synonyms=synonyms)
    return resolver.lookup(term)


def get_synonyms(term: str, *, synonyms: Optional[Dict[str, List[str]]] = None) -> List[str]:
    return find_synonyms(term, synonyms=synonyms)


def lookup(term: str, *, synonyms: Optional[Dict[str, List[str]]] = None) -> List[str]:
    return find_synonyms(term, synonyms=synonyms)


def resolve(term: str, *, synonyms: Optional[Dict[str, List[str]]] = None) -> List[str]:
    resolver = GitSynonymResolver(synonyms=synonyms)
    return resolver.resolve(term)
