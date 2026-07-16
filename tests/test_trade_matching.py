"""
test_trade_matching.py - Unit tests for trade_matching.py.

Covers `normalize_trade_key` (whitespace, underscores, case, punctuation,
None/empty, numeric magic) and `trade_matches_ea`'s three match paths
(comment, alias, magic) plus non-matches. See docs/design/domain-extraction.md
D1/D8 for why this module exists.
"""

from trade_matching import normalize_trade_key, trade_matches_ea


# ── normalize_trade_key ──────────────────────────────────────────────────────


def test_normalize_trade_key_lowercases():
    assert normalize_trade_key("USDJPY") == "usdjpy"


def test_normalize_trade_key_strips_spaces():
    assert normalize_trade_key("USDJPY 1104") == "usdjpy1104"


def test_normalize_trade_key_strips_underscores():
    assert normalize_trade_key("USDJPY_1104") == "usdjpy1104"


def test_normalize_trade_key_spaces_and_underscores_collide():
    """This collision is the whole point of the matcher fix (D8)."""
    assert normalize_trade_key("USDJPY 1104") == normalize_trade_key("USDJPY_1104")


def test_normalize_trade_key_strips_punctuation():
    assert normalize_trade_key("EA-1.0 [v2]") == "ea10v2"


def test_normalize_trade_key_none_returns_empty_string():
    assert normalize_trade_key(None) == ""


def test_normalize_trade_key_empty_string_returns_empty_string():
    assert normalize_trade_key("") == ""


def test_normalize_trade_key_numeric_magic():
    assert normalize_trade_key(1104) == "1104"


def test_normalize_trade_key_numeric_magic_as_string_matches_int():
    assert normalize_trade_key(1104) == normalize_trade_key("1104")


def test_normalize_trade_key_mixed_case_and_symbols():
    assert normalize_trade_key("My_EA-Test") == "myeatest"


# ── trade_matches_ea: comment path ──────────────────────────────────────────


def test_trade_matches_ea_by_exact_comment():
    trade = {"comment": "MyEA"}
    assert trade_matches_ea(trade, "MyEA") is True


def test_trade_matches_ea_by_normalized_comment_space_vs_underscore():
    trade = {"comment": "USDJPY 1104"}
    assert trade_matches_ea(trade, "USDJPY_1104") is True


def test_trade_matches_ea_by_normalized_comment_case_insensitive():
    trade = {"comment": "myea"}
    assert trade_matches_ea(trade, "MYEA") is True


def test_trade_matches_ea_comment_mismatch_returns_false():
    trade = {"comment": "SomeOtherEA"}
    assert trade_matches_ea(trade, "MyEA") is False


# ── trade_matches_ea: alias path ────────────────────────────────────────────


def test_trade_matches_ea_by_alias():
    trade = {"comment": "USDJPY EA"}
    config = {"mappings": {"USDJPY_1104": {"alias": "USDJPY EA", "magic": "1104"}}}
    assert trade_matches_ea(trade, "USDJPY_1104", config) is True


def test_trade_matches_ea_alias_mismatch_returns_false():
    trade = {"comment": "Unrelated Comment"}
    config = {"mappings": {"USDJPY_1104": {"alias": "USDJPY EA", "magic": "1104"}}}
    assert trade_matches_ea(trade, "USDJPY_1104", config) is False


def test_trade_matches_ea_alias_empty_string_is_not_a_match_source():
    """An empty alias must not match a trade with an empty/None comment."""
    trade = {"comment": ""}
    config = {"mappings": {"MyEA": {"alias": "", "magic": "9001"}}}
    assert trade_matches_ea(trade, "MyEA", config) is False


# ── trade_matches_ea: magic path ────────────────────────────────────────────


def test_trade_matches_ea_by_magic_number_as_comment():
    trade = {"comment": "1104"}
    config = {"mappings": {"USDJPY_1104": {"magic": "1104"}}}
    assert trade_matches_ea(trade, "USDJPY_1104", config) is True


def test_trade_matches_ea_magic_mismatch_returns_false():
    trade = {"comment": "9999"}
    config = {"mappings": {"USDJPY_1104": {"magic": "1104"}}}
    assert trade_matches_ea(trade, "USDJPY_1104", config) is False


def test_trade_matches_ea_magic_none_is_not_a_match_source():
    """magic=None must not be matched by a trade whose comment is empty/None."""
    trade = {"comment": ""}
    config = {"mappings": {"MyEA": {"magic": None}}}
    assert trade_matches_ea(trade, "MyEA", config) is False


# ── trade_matches_ea: no config ─────────────────────────────────────────────


def test_trade_matches_ea_without_config_only_checks_comment():
    trade = {"comment": "USDJPY EA"}
    # No config passed -> alias/magic paths are unreachable, only comment counts.
    assert trade_matches_ea(trade, "USDJPY_1104") is False


def test_trade_matches_ea_missing_ea_in_mappings_falls_back_to_comment_only():
    trade = {"comment": "MyEA"}
    config = {"mappings": {}}
    assert trade_matches_ea(trade, "MyEA", config) is True
