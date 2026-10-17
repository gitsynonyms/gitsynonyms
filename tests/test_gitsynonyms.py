from gitsynonyms import find_synonyms, lookup, resolve


def test_find_synonyms_status():
    assert find_synonyms("status") == ["status", "st", "info"]
    assert find_synonyms("st") == ["status", "st", "info"]


def test_lookup_from_git_cli():
    assert lookup("git checkout") == ["checkout", "co", "switch"]


def test_resolve_unknown_term_suggests_close_match():
    results = resolve("stauts")
    assert results
    assert "status" in results[0]


def test_resolve_exact_branch_alias():
    assert "branch" in resolve("br")
