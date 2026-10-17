"""Git command synonym lookup helpers."""

from .core import (
    DEFAULT_SYNONYMS,
    GitSynonymResolver,
    find_synonyms,
    get_synonyms,
    lookup,
    normalize_term,
    resolve,
)

__version__ = "0.1.0"
__all__ = [
    "DEFAULT_SYNONYMS",
    "GitSynonymResolver",
    "find_synonyms",
    "get_synonyms",
    "lookup",
    "normalize_term",
    "resolve",
]
