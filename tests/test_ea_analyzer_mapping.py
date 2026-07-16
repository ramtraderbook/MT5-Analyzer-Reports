"""
test_ea_analyzer_mapping.py - Unit tests for ea_analyzer.build_mapping_rows.

Pins the fact that build_mapping_rows attributes trades to an EA via the
full trade_matches_ea matcher (normalized-comment, alias, and magic paths),
not via exact `comment == ea_name` string equality. A mutation that swaps
the matcher call for exact equality (ea_analyzer.py line 403) must fail
these tests. See docs/design/domain-extraction.md D1/D8.
"""

import ea_analyzer


def _trade(comment, symbol="EURUSD", position_id=1):
    return {
        "position_id": position_id,
        "symbol": symbol,
        "comment": comment,
    }


def test_build_mapping_rows_matches_normalized_comment_space_vs_underscore():
    """Mapping key 'USDJPY_1104' must match a trade commented 'USDJPY 1104'.

    Under exact-equality matching this trade is excluded and trade_count
    stays 0 -> the assertion below fails, killing the mutation.
    """
    parsed_data = {
        "ea_names": ["USDJPY_1104"],
        "closed_trades": [_trade("USDJPY 1104")],
    }
    config = {"mappings": {}}

    rows = ea_analyzer.build_mapping_rows(parsed_data, config)

    assert len(rows) == 1
    assert rows[0]["name"] == "USDJPY_1104"
    assert rows[0]["trade_count"] > 0


def test_build_mapping_rows_matches_via_alias():
    """A trade commented with the configured alias (not the EA name itself)
    must be attributed to that EA. Exact-equality matching cannot see this.
    """
    parsed_data = {
        "ea_names": ["USDJPY_1104"],
        "closed_trades": [_trade("USDJPY EA")],
    }
    config = {
        "mappings": {"USDJPY_1104": {"alias": "USDJPY EA", "magic": "1104"}}
    }

    rows = ea_analyzer.build_mapping_rows(parsed_data, config)

    assert len(rows) == 1
    assert rows[0]["trade_count"] > 0


def test_build_mapping_rows_matches_via_magic():
    """A trade commented with the configured magic number (not the EA name
    itself) must be attributed to that EA. Exact-equality matching cannot
    see this.
    """
    parsed_data = {
        "ea_names": ["USDJPY_1104"],
        "closed_trades": [_trade("1104")],
    }
    config = {"mappings": {"USDJPY_1104": {"magic": "1104"}}}

    rows = ea_analyzer.build_mapping_rows(parsed_data, config)

    assert len(rows) == 1
    assert rows[0]["trade_count"] > 0


def test_build_mapping_rows_unmatched_trade_not_counted():
    """Sanity check: an unrelated comment must not be attributed."""
    parsed_data = {
        "ea_names": ["USDJPY_1104"],
        "closed_trades": [_trade("SomeOtherEA")],
    }
    config = {"mappings": {}}

    rows = ea_analyzer.build_mapping_rows(parsed_data, config)

    assert len(rows) == 1
    assert rows[0]["trade_count"] == 0
