"""
test_validator.py - Characterization tests for validator.py.

These tests PIN the CURRENT behavior of the strategy validator (magic-number
scoring engine), not the behavior described in docs/decision-logic.md. Where
the code and the docs disagree, the code's behavior is asserted and flagged
with a `# NOTE:` comment — it is not "fixed" here
(see docs/design/domain-extraction.md, D7 and D8).

All fixtures are hardcoded and hand-computed. No real report data is used.
"""

from datetime import datetime

import pytest

from validator import calculate_validator_score, get_all_validator_results


def make_trade(position_id, comment, net_pnl=10.0, close_date=2):
    """Minimal trade dict, matching the shape produced by parser.py."""
    return {
        "position_id": position_id,
        "comment": comment,
        "symbol": "EURUSD",
        "net_pnl": net_pnl,
        "close_time": datetime(2026, 1, close_date, 12, 0, 0),
        "direction": "buy",
        "duration_hours": 4.0,
    }


# ── Priority 6: dd_estado — pin the REAL behavior (validator.py:265-297) ────
#
# docs/decision-logic.md claims a "fallback" that reads either MC source
# independently. The code requires BOTH mc_r_dd and mc_t_dd to be present to
# take the MC fallback branch — with only one MC source and no BT DD data,
# the result is "N/D", not a partial fallback. These tests pin the code's
# truth, not the doc's claim.


def test_dd_estado_none_when_live_dd_missing():
    live = {"total_trades": 50, "weeks_operating": 10}
    result = calculate_validator_score(bt={}, mc_retest={}, mc_trades={}, spp={}, live=live)

    assert result["dd_estado"] == "N/D"
    assert result["dd_live"] is None
    assert result["dd_method"] == "N/D"


@pytest.mark.parametrize(
    "live_dd, expected_estado",
    [
        (10.0, "OK"),       # <= dd_limit (10.0)
        (10.01, "ALERTA"),  # > dd_limit, <= dd_limit * 1.5 (15.0)
        (15.0, "ALERTA"),   # exactly at the 1.5x boundary
        (15.01, "FUERA"),   # above the 1.5x boundary
    ],
)
def test_dd_estado_bt_worst_dd_path_boundaries(live_dd, expected_estado):
    """
    BT worst-DD path: dd_limit = worst_dd_1m * sqrt(weeks_live / 4.33).
    weeks_live=4.33 makes the sqrt factor exactly 1.0, so dd_limit ==
    worst_dd_1m == 10.0 and the 1.5x ALERTA boundary is exactly 15.0.
    """
    live = {"total_trades": 50, "weeks_operating": 4.33, "max_dd_pct": live_dd}
    bt = {"worst_dd_1m": 10.0}

    result = calculate_validator_score(bt=bt, mc_retest={}, mc_trades={}, spp={}, live=live)

    assert result["dd_estado"] == expected_estado
    assert result["dd_method"] == "sqrt(4.3sem/4.33) x 10.0%"


def test_dd_estado_is_nd_when_only_one_mc_source_present_and_no_bt_dd():
    """
    NOTE: this contradicts docs/decision-logic.md's claimed fallback, which
    describes the MC DD fallback as usable from a single MC source. The
    code (validator.py:278) requires `mc_r_dd is not None and mc_t_dd is
    not None` — with only mc_retest present and no BT worst_dd_1m, the
    result is "N/D", not a partial/single-source fallback.
    """
    live = {"total_trades": 50, "weeks_operating": 10, "max_dd_pct": 20.0}
    bt = {}  # no worst_dd_1m -> BT path not taken
    mc_retest = {"max_dd": 12.0}
    mc_trades = {}  # mc_t_dd stays None

    result = calculate_validator_score(bt=bt, mc_retest=mc_retest, mc_trades=mc_trades, spp={}, live=live)

    assert result["dd_estado"] == "N/D"
    assert result["dd_method"] == "N/D"


def test_dd_estado_is_nd_when_only_mc_trades_present_and_no_bt_dd():
    """Symmetric case: only mc_trades present, mc_retest missing -> still N/D."""
    live = {"total_trades": 50, "weeks_operating": 10, "max_dd_pct": 20.0}
    bt = {}
    mc_retest = {}
    mc_trades = {"max_dd": 12.0}

    result = calculate_validator_score(bt=bt, mc_retest=mc_retest, mc_trades=mc_trades, spp={}, live=live)

    assert result["dd_estado"] == "N/D"
    assert result["dd_method"] == "N/D"


@pytest.mark.parametrize(
    "live_dd, expected_estado",
    [
        (10.0, "OK"),       # <= min(mc_r_dd, mc_t_dd) == 10.0
        (10.01, "ALERTA"),  # > min, <= max(mc_r_dd, mc_t_dd) == 14.0
        (14.0, "ALERTA"),   # exactly at the max boundary
        (14.01, "FUERA"),   # above both MC values
    ],
)
def test_dd_estado_both_mc_present_fallback_boundaries(live_dd, expected_estado):
    """
    Both MC sources present, no BT worst_dd_1m -> the MC fallback IS used:
    dd_limit_used = min(mc_r_dd, mc_t_dd) is the OK boundary, and
    max(mc_r_dd, mc_t_dd) is the ALERTA/FUERA boundary.
    """
    live = {"total_trades": 50, "weeks_operating": 10, "max_dd_pct": live_dd}
    bt = {}
    mc_retest = {"max_dd": 10.0}
    mc_trades = {"max_dd": 14.0}

    result = calculate_validator_score(bt=bt, mc_retest=mc_retest, mc_trades=mc_trades, spp={}, live=live)

    assert result["dd_estado"] == expected_estado
    assert result["dd_method"] == "MC min(Retest,Trades) 95% (fallback)"


# ── Priority 7: get_all_validator_results ───────────────────────────────────


def test_get_all_validator_results_matches_exact_comment_equality():
    """Baseline sanity check: exact comment == ea_name match works today."""
    parsed_data = {
        "closed_trades": [
            make_trade(1, "MyEA", net_pnl=10.0, close_date=2),
            make_trade(2, "MyEA", net_pnl=-5.0, close_date=5),
        ]
    }
    config = {"mappings": {"MyEA": {"magic": "9001", "alias": "MyEA Test", "active": True}}}

    results = get_all_validator_results(parsed_data, config, store={})

    assert len(results) == 1
    assert results[0]["ea_name"] == "MyEA"
    assert results[0]["live"]["total_trades"] == 2


def test_get_all_validator_results_normalized_comment_now_matches():
    """
    REGRESSION GUARD for the matcher fix (docs/design/domain-extraction.md D8).

    `get_all_validator_results` used to filter trades with
    `t.get("comment") == ea_name` — plain exact string equality, no
    normalization. A trade whose comment is "USDJPY 1104" (a real MT5-style
    comment with a space) did NOT match a mapping keyed "USDJPY_1104"
    (underscore), even though they refer to the same EA, yielding ZERO
    matched trades for a strategy that IS trading.

    The fix has landed: `get_all_validator_results` now filters with
    `trade_matches_ea(t, ea_name, config)` from trade_matching.py, the same
    normalized comment/alias/magic matcher the dashboard already used. This
    test pins the FIXED behavior — do not revert it to asserting 0.
    """
    parsed_data = {
        "closed_trades": [
            make_trade(1, "USDJPY 1104", net_pnl=10.0, close_date=2),
        ]
    }
    config = {"mappings": {"USDJPY_1104": {"magic": "1104", "alias": "USDJPY EA", "active": True}}}

    results = get_all_validator_results(parsed_data, config, store={})

    assert len(results) == 1
    assert results[0]["live"]["total_trades"] == 1


def test_get_all_validator_results_matches_via_alias():
    """A trade whose comment equals the mapping's alias (not the EA key) now matches."""
    parsed_data = {
        "closed_trades": [
            make_trade(1, "USDJPY EA", net_pnl=10.0, close_date=2),
        ]
    }
    config = {"mappings": {"USDJPY_1104": {"magic": "1104", "alias": "USDJPY EA", "active": True}}}

    results = get_all_validator_results(parsed_data, config, store={})

    assert len(results) == 1
    assert results[0]["live"]["total_trades"] == 1


def test_get_all_validator_results_matches_via_magic():
    """A trade whose comment is just the magic number now matches."""
    parsed_data = {
        "closed_trades": [
            make_trade(1, "1104", net_pnl=10.0, close_date=2),
        ]
    }
    config = {"mappings": {"USDJPY_1104": {"magic": "1104", "alias": "USDJPY EA", "active": True}}}

    results = get_all_validator_results(parsed_data, config, store={})

    assert len(results) == 1
    assert results[0]["live"]["total_trades"] == 1


def test_get_all_validator_results_accepted_tradeoff_merges_identically_normalized_ea_names():
    """
    ACCEPTED TRADEOFF (docs/design/domain-extraction.md, Risks): widening the
    validator to dashboard matching semantics means two EAs whose names
    normalize to the same key ("USDJPY 1104" vs "USDJPY_1104") now merge
    trades if both mappings are active magic-having entries and a trade's
    comment matches the shared normalized key. This is intentional -
    one truth beats two - and pinned here so it is never "fixed" by
    surprise.
    """
    parsed_data = {
        "closed_trades": [
            make_trade(1, "USDJPY 1104", net_pnl=10.0, close_date=2),
        ]
    }
    config = {
        "mappings": {
            "USDJPY 1104": {"magic": "1104", "active": True},
            "USDJPY_1104": {"magic": "1105", "active": True},
        }
    }

    results = get_all_validator_results(parsed_data, config, store={})

    by_name = {r["ea_name"]: r["live"]["total_trades"] for r in results}
    assert by_name == {"USDJPY 1104": 1, "USDJPY_1104": 1}


def test_get_all_validator_results_skips_inactive_mappings():
    parsed_data = {
        "closed_trades": [
            make_trade(1, "MyEA"),
            make_trade(2, "OtherEA"),
        ]
    }
    config = {
        "mappings": {
            "MyEA": {"magic": "9001", "active": True},
            "OtherEA": {"magic": "9002", "active": False},
        }
    }

    results = get_all_validator_results(parsed_data, config, store={})

    ea_names = [r["ea_name"] for r in results]
    assert ea_names == ["MyEA"]


def test_get_all_validator_results_skips_mappings_without_magic():
    parsed_data = {"closed_trades": [make_trade(1, "MyEA")]}
    config = {
        "mappings": {
            "MyEA": {"magic": "", "active": True},
            "OtherEA": {"active": True},  # magic key entirely absent
        }
    }

    results = get_all_validator_results(parsed_data, config, store={})

    assert results == []


def test_get_all_validator_results_zero_trades_default_metrics_dict():
    """
    An EA mapping with no matching trades at all (not the comment-mismatch
    bug — genuinely zero trades in parsed_data) gets the hardcoded
    zero-value metrics dict (validator.py:581-592), not None/N-D markers.
    """
    parsed_data = {"closed_trades": [make_trade(1, "SomeOtherEA")]}
    config = {"mappings": {"GhostEA": {"magic": "5555", "active": True}}}

    results = get_all_validator_results(parsed_data, config, store={})

    assert len(results) == 1
    assert results[0]["live"] == {
        "total_trades": 0,
        "weeks_operating": 0,
        "win_rate": 0,
        "profit_factor": 0.0,
        "payout_ratio": 0.0,
        "expectancy": 0,
        "max_dd_pct": 0,
        "max_consec_losses": 0,
        "stagnation_days": 0,
        "avg_bars_live": 0.0,
    }
    assert results[0]["has_bt"] is False
    assert results[0]["analysis"] is None
