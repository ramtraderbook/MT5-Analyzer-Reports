"""
test_incubation_domain.py - Tests for incubation_domain.py.

Priority 1 is the `evaluate_ea` end-to-end regression test: it is the exact
path broken by the `reference_ready` local/module-function name collision
(incubation_domain.py:589, introduced by commit 8298cd7). Any EA with >=1
matched trade used to raise UnboundLocalError before the local variable was
renamed to `is_reference_ready` -- see FIX 1 in the review ledger.

All fixtures are hand-built (no real report data), following the idiom in
tests/conftest.py and tests/test_upload.py.
"""

from datetime import date, datetime, timedelta

import pytest

from incubation_domain import (
    build_comparison_rows,
    build_distribution_payload,
    build_monthly_performance,
    build_timeline_from_entry,
    build_verdict_card,
    checkpoint_for_trades,
    compute_spp_ratios,
    days_since_first_trade,
    evaluate_ea,
)


# ── Shared trade/config helpers ──────────────────────────────────────────


def _make_trade(position_id, comment, close_time, net_pnl, direction="buy"):
    """One realistic closed-trade dict, matching parser.py's output shape."""
    duration_hours = 2.0
    return {
        "position_id": position_id,
        "symbol": "EURUSD",
        "direction": direction,
        "volume": 0.1,
        "open_time": close_time - timedelta(hours=duration_hours),
        "close_time": close_time,
        "open_price": 1.1000,
        "close_price": 1.1010 if direction == "buy" else 1.0990,
        "sl": None,
        "tp": None,
        "commission": -1.0,
        "swap": 0.0,
        "profit": net_pnl + 1.0,
        "net_pnl": net_pnl,
        "duration_hours": duration_hours,
        "comment": comment,
    }


def _make_trades(n, comment, start=None):
    """n trades alternating win/loss, one per day starting at `start`."""
    start = start or datetime(2026, 1, 1, 10, 0, 0)
    trades = []
    for i in range(n):
        pnl = 10.0 if i % 2 == 0 else -5.0
        direction = "buy" if i % 2 == 0 else "sell"
        trades.append(_make_trade(2000 + i, comment, start + timedelta(days=i), pnl, direction))
    return trades


def _make_winning_trades(n, comment, start=None):
    """n winning trades, zero losses -> gross_loss == 0, so metrics.py's
    fmt_pf() returns the string "∞" for profit_factor/payout_ratio."""
    start = start or datetime(2026, 1, 1, 10, 0, 0)
    trades = []
    for i in range(n):
        trades.append(_make_trade(3000 + i, comment, start + timedelta(days=i), 10.0, "buy"))
    return trades


@pytest.fixture
def incubation_config():
    return {
        "mappings": {
            "IncEA": {
                "magic": "7001",
                "alias": "IncEA Test",
                "instrument": "EURUSD",
                "capital": 10000.0,
                "active": True,
            }
        }
    }


@pytest.fixture
def reference_entry_ready():
    """A minimally complete reference entry: backtest + MC95 present."""
    return {
        "backtest": {
            "win_rate": 55.0,
            "profit_factor": 1.8,
            "expectancy": 20.0,
            "payout_ratio": 1.5,
            "ret_dd_ratio": 2.0,
            "max_dd_pct": 8.0,
            "max_consec_losses": 4,
            "stagnation_days": 10,
            "total_trades": 80,
            "bt_period": "2020.01.01 - 2020.06.01",
        },
        "mc_manipulation": {
            "confidence_95": {
                "win_rate": 40.0,
                "profit_factor": 1.0,
                "max_dd_pct": 12.0,
                "max_consec_losses": 8,
            },
            "confidence_50": {
                "win_rate": 48.0,
                "profit_factor": 1.4,
                "max_dd_pct": 10.0,
                "max_consec_losses": 6,
            },
        },
    }


# ── Priority 1: evaluate_ea end-to-end (the FIX 1 regression path) ──────


class TestEvaluateEa:
    def test_no_matched_trades_returns_none(self, incubation_config):
        """No trade matches ea_name -> _incubation_load_ea_metrics finds
        nothing and evaluate_ea returns None before ever touching
        reference_ready."""
        parsed_data = {"closed_trades": _make_trades(3, "OtherEA")}

        result = evaluate_ea("IncEA", parsed_data, incubation_config, {})

        assert result is None

    def test_trades_but_reference_not_ready_returns_false(self, incubation_config):
        """>=1 matched trade + empty entry -> this already exercises the
        broken `reference_ready = reference_ready(entry)` line, since it
        runs unconditionally once metrics is not None."""
        parsed_data = {"closed_trades": _make_trades(3, "IncEA")}

        result = evaluate_ea("IncEA", parsed_data, incubation_config, {})

        assert result is not None
        assert result["reference_ready"] is False
        assert result["evaluation"] is None
        assert result["metrics"]["total_trades"] == 3
        assert result["trades"]

    def test_trades_and_reference_ready_returns_evaluation(
        self, incubation_config, reference_entry_ready
    ):
        """
        THE regression test for FIX 1 (incubation_domain.py:589).

        `reference_ready` is a module-level function; assigning to that same
        name inside evaluate_ea makes it function-local for the whole body,
        so the right-hand-side call `reference_ready(entry)` hits an
        UnboundLocalError. This test exercises the exact call path that
        every route (dashboard, strategy, force_evaluate) goes through for
        any EA with >=1 matched trade.

        Verified to FAIL with UnboundLocalError against the pre-fix code
        (`reference_ready = reference_ready(entry)`) and PASS once the local
        is renamed to `is_reference_ready`.
        """
        parsed_data = {"closed_trades": _make_trades(3, "IncEA")}

        result = evaluate_ea("IncEA", parsed_data, incubation_config, reference_entry_ready)

        assert result is not None
        assert result["reference_ready"] is True
        assert result["evaluation"] is not None
        assert result["evaluation"]["current_checkpoint"] == "PRE_CP1"
        # entry gets synced with the last evaluation
        assert result["entry"]["last_evaluation"] == result["evaluation"]

    def test_trades_and_reference_ready_cp1_checkpoint(self, incubation_config, reference_entry_ready):
        """10 trades -> CP1 range (5-19); exercises the hard-gates path
        through evaluate_incubation -> evaluate_cp1."""
        parsed_data = {"closed_trades": _make_trades(10, "IncEA")}

        result = evaluate_ea("IncEA", parsed_data, incubation_config, reference_entry_ready)

        assert result["reference_ready"] is True
        evaluation = result["evaluation"]
        assert evaluation["current_checkpoint"] == "CP1"
        assert evaluation["verdict"] in {"CONTINUAR", "ELIMINAR"}
        assert "gates" in evaluation["details"]
        # checkpoint store gets synced for CP1
        assert result["entry"]["checkpoints"]["cp1"] == evaluation


# ── build_verdict_card ───────────────────────────────────────────────────


class TestBuildVerdictCard:
    def test_no_evaluation_returns_no_data_card(self):
        card = build_verdict_card(None)

        assert card["verdict"] == "NO DATA"
        assert card["checkpoint"] == "PRE_CP1"
        assert card["hard_gates"] == []

    def test_realistic_cp1_evaluation(self, incubation_config, reference_entry_ready):
        parsed_data = {"closed_trades": _make_trades(10, "IncEA")}
        bundle = evaluate_ea("IncEA", parsed_data, incubation_config, reference_entry_ready)

        card = build_verdict_card(bundle["evaluation"])

        assert card["checkpoint"] == "CP1"
        assert card["verdict"] in {"CONTINUAR", "ELIMINAR"}
        assert isinstance(card["hard_gates"], list)
        assert card["hard_gates"]
        assert card["verdict_reading"]["message"]


# ── build_comparison_rows ────────────────────────────────────────────────


class TestBuildComparisonRows:
    def test_no_metrics_returns_empty_list(self, reference_entry_ready):
        assert build_comparison_rows(None, reference_entry_ready) == []

    def test_realistic_entry_returns_all_metric_rows(self, incubation_config, reference_entry_ready):
        parsed_data = {"closed_trades": _make_trades(10, "IncEA")}
        bundle = evaluate_ea("IncEA", parsed_data, incubation_config, reference_entry_ready)

        rows = build_comparison_rows(bundle["metrics"], bundle["entry"])

        metric_names = {row["metric"] for row in rows}
        assert metric_names == {
            "Win Rate",
            "Profit Factor",
            "Expectancy",
            "Max DD%",
            "Max Consec Losses",
            "Payout Ratio",
            "SQN Score",
            "Stagnation",
            "Ret/DD",
        }
        for row in rows:
            assert "state" in row
            assert "score_band" in row

    def test_zero_loss_ea_with_reference_data_does_not_crash(
        self, incubation_config, reference_entry_ready
    ):
        """
        Regression test: metrics.calculate_ea_metrics pre-formats an
        infinite profit_factor/payout_ratio (zero losing trades) to the
        string "∞". With MC reference data present, build_comparison_rows
        used to crash inside _incubation_metric_band_state with:
            TypeError: '>=' not supported between instances of 'str' and 'float'
        because the "∞" string was compared directly against numeric MC
        bounds. Verified to FAIL with that TypeError against the pre-fix
        code.
        """
        parsed_data = {"closed_trades": _make_winning_trades(3, "IncEA")}
        bundle = evaluate_ea("IncEA", parsed_data, incubation_config, reference_entry_ready)

        rows = build_comparison_rows(bundle["metrics"], bundle["entry"])

        pf_row = next(row for row in rows if row["metric"] == "Profit Factor")
        assert pf_row["live"] == "∞"
        assert pf_row["state"]["score_band"] == "above_mc50"
        assert pf_row["state"]["class"] == "cmp-green"

    def test_zero_loss_ea_without_reference_data_does_not_crash(self, incubation_config):
        """
        Same "∞" profit_factor, but with no MC reference data at all (empty
        entry). _incubation_metric_band_state short-circuits on None MC
        values, so this path never reaches the comparison above -- it
        crashes instead inside _incubation_format_metric with:
            ValueError: Unknown format code 'f' for object of type 'str'
        because the existing `value == float("inf")` guard never matches
        the string "∞". Verified to FAIL with that ValueError against the
        pre-fix code.
        """
        parsed_data = {"closed_trades": _make_winning_trades(3, "IncEA")}
        bundle = evaluate_ea("IncEA", parsed_data, incubation_config, {})

        rows = build_comparison_rows(bundle["metrics"], {})

        pf_row = next(row for row in rows if row["metric"] == "Profit Factor")
        assert pf_row["live"] == "∞"
        assert pf_row["state"]["score_band"] is None


# ── build_timeline_from_entry ────────────────────────────────────────────


class TestBuildTimelineFromEntry:
    def test_empty_entry_returns_pending_slots(self):
        timeline = build_timeline_from_entry({})

        assert [item["key"] for item in timeline] == ["cp1", "cp2", "cp3"]
        assert all(item["verdict"] == "Pending" for item in timeline)

    def test_realistic_entry_shows_populated_cp1_slot(self, incubation_config, reference_entry_ready):
        parsed_data = {"closed_trades": _make_trades(10, "IncEA")}
        bundle = evaluate_ea("IncEA", parsed_data, incubation_config, reference_entry_ready)

        timeline = build_timeline_from_entry(bundle["entry"])
        cp1_item = next(item for item in timeline if item["key"] == "cp1")
        cp2_item = next(item for item in timeline if item["key"] == "cp2")

        assert cp1_item["verdict"] in {"CONTINUAR", "ELIMINAR"}
        assert cp1_item["trades"] == 10
        assert cp2_item["verdict"] == "Pending"


# ── build_distribution_payload / build_monthly_performance ─────────────


class TestBuildDistributionPayload:
    def test_realistic_metrics_produce_full_payload(self, incubation_config, reference_entry_ready):
        parsed_data = {"closed_trades": _make_trades(10, "IncEA")}
        bundle = evaluate_ea("IncEA", parsed_data, incubation_config, reference_entry_ready)

        payload = build_distribution_payload(bundle["metrics"])

        assert len(payload["pnl_list"]) == 10
        assert len(payload["streak_data"]) == 10
        assert len(payload["weekday_pnl"]) == 7
        assert len(payload["hour_pnl"]) == 24
        assert payload["long_short"]["long_count"] + payload["long_short"]["short_count"] == 10


class TestBuildMonthlyPerformance:
    def test_realistic_trades_group_by_year_month(self, incubation_config, reference_entry_ready):
        parsed_data = {"closed_trades": _make_trades(10, "IncEA")}
        bundle = evaluate_ea("IncEA", parsed_data, incubation_config, reference_entry_ready)

        monthly = build_monthly_performance(bundle["metrics"]["trades"])

        assert len(monthly) == 1
        assert monthly[0]["year"] == 2026
        # January (index 0) should have a value since all 10 trades close in Jan 2026
        assert monthly[0]["months"][0] is not None


# ── checkpoint_for_trades / days_since_first_trade boundaries ──────────


class TestCheckpointForTrades:
    @pytest.mark.parametrize(
        "total_trades, expected_key",
        [
            (0, "pre_cp1"),
            (4, "pre_cp1"),
            (5, "cp1"),
            (19, "cp1"),
            (20, "cp2"),
            (39, "cp2"),
            (40, "cp3"),
            (100, "cp3"),
        ],
    )
    def test_boundaries(self, total_trades, expected_key):
        key, _label, _css_class = checkpoint_for_trades(total_trades)

        assert key == expected_key


class TestDaysSinceFirstTrade:
    def test_no_trades_returns_zero(self):
        assert days_since_first_trade([]) == 0

    def test_trades_without_close_time_returns_zero(self):
        assert days_since_first_trade([{"close_time": None}]) == 0

    def test_first_trade_today_returns_zero(self):
        trades = [{"close_time": datetime.combine(date.today(), datetime.min.time())}]

        assert days_since_first_trade(trades) == 0

    def test_first_trade_n_days_ago_returns_n(self):
        n = 7
        close_time = datetime.combine(date.today() - timedelta(days=n), datetime.min.time())
        trades = [{"close_time": close_time}]

        assert days_since_first_trade(trades) == n

    def test_uses_earliest_close_time_among_multiple_trades(self):
        earlier = datetime.combine(date.today() - timedelta(days=10), datetime.min.time())
        later = datetime.combine(date.today() - timedelta(days=2), datetime.min.time())
        trades = [{"close_time": later}, {"close_time": earlier}]

        assert days_since_first_trade(trades) == 10

    def test_accepts_iso_string_close_time(self):
        n = 3
        close_time_str = datetime.combine(
            date.today() - timedelta(days=n), datetime.min.time()
        ).isoformat()
        trades = [{"close_time": close_time_str}]

        assert days_since_first_trade(trades) == n


# ── compute_spp_ratios (design item 8: cheap golden assertion) ─────────


class TestComputeSppRatios:
    def test_ratio_of_spp_median_over_backtest_value(self):
        bt_data = {"net_profit": 1000.0}
        spp_data = {"median_net_profit": 500.0}

        ratios = compute_spp_ratios(bt_data, spp_data)

        # compute_spp_ratios computes (bt_value / spp_value) * 100
        assert ratios["median_net_profit"] == 200.0

    def test_missing_spp_value_yields_none_ratio(self):
        bt_data = {"net_profit": 1000.0}
        spp_data = {}

        ratios = compute_spp_ratios(bt_data, spp_data)

        assert ratios["median_net_profit"] is None
