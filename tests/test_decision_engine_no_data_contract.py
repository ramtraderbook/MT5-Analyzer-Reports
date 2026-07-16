"""
test_decision_engine_no_data_contract.py

Regression pins for docs/design/decision-engine-no-data-contract.md.

These fixtures are empirically verified against the implementation (see the
design doc §8 for the source list of proven flips). Each test is numbered to
match the design's "Test plan" section. A final section pins the explicit
"must not change" list (design §9) so the SIN DATOS work cannot regress
weight sums, interpolation continuity, checkpoint boundaries, or verdict
cutoffs.
"""

import math

import pytest

from incubation_validator import (
    _binomial_p_value,
    _mc_source_bundle,
    _score_metric,
    _spp_confidence,
    calculate_monthly_frequency,
    evaluate_cp1,
    evaluate_cp2,
    evaluate_cp3,
    evaluate_incubation,
)
from incubation_domain import evaluate_ea, metric_summary_for_tooltip
from validator import CONFIG, calculate_validator_score


# ── 1. Exact binomial left-tail CDF (C1) ────────────────────────────────────


def test_binomial_exact_pin_wins2_n10_p05():
    """Shipped normal approximation gave 0.0289 (FAILs the 0.03 gate). The
    exact CDF gives 0.0547, which PASSes it -- this is the proven flip."""
    assert _binomial_p_value(2, 10, 0.5) == pytest.approx(0.0546875)


def test_binomial_exact_pin_wins1_n6_p06():
    """Shipped approximation gave 0.01513; exact CDF gives 0.04096."""
    assert _binomial_p_value(1, 6, 0.6) == pytest.approx(0.04096)


@pytest.mark.parametrize("n", [5, 10, 20, 30])
@pytest.mark.parametrize("p", [0.3, 0.5, 0.6, 0.8])
def test_binomial_exact_matches_math_comb_reference_sum(n, p):
    """Assert the implementation's CDF equals a hand-summed math.comb
    reference for every wins value, across a sweep of (n, p)."""
    for wins in range(n + 1):
        expected = sum(math.comb(n, k) * (p ** k) * ((1 - p) ** (n - k)) for k in range(wins + 1))
        assert _binomial_p_value(wins, n, p) == pytest.approx(expected)


@pytest.mark.parametrize("n,p", [(10, 0.5), (20, 0.6), (30, 0.4)])
def test_binomial_left_tail_is_monotone_increasing_in_wins(n, p):
    """Left-tail direction pin: P(X <= wins) must strictly increase as wins
    increases (design's MUST-NOT-CHANGE: binomial LEFT-tail direction)."""
    values = [_binomial_p_value(w, n, p) for w in range(n + 1)]
    assert values == sorted(values)
    assert values[0] < values[-1]


# ── 2. _mc_section_values: no cross-confidence aliasing (C2) ───────────────


def test_mc_section_values_no_aliasing_between_confidence_levels():
    reference_data = {
        "mc_manipulation": {"confidence_95": {"max_dd_pct": 10.0}},
    }
    bundle = _mc_source_bundle(reference_data, "confidence_50")
    assert bundle["mc_manipulation"] == {}
    assert bundle["worst"] == {}


def test_cp3_with_mc50_absent_is_sin_datos_never_a_65_band_score():
    """CP3 with MC50 entirely absent -> SIN DATOS listing mc50.* keys, never
    a scored 65-band result (design §8, item 2)."""
    reference_data = {
        "date_added": "2020-01-01",
        "backtest": {
            "win_rate": 55, "profit_factor": 1.8, "expectancy": 20.0, "payout_ratio": 1.5,
            "ret_dd_ratio": 2.0, "max_dd_pct": 8.0, "max_consec_losses": 4, "stagnation_days": 10,
            "total_trades": 300, "bt_period": "2015.01.01 - 2020.01.01",
        },
        "mc_manipulation": {
            "confidence_95": {
                "win_rate": 40, "profit_factor": 1.0, "expectancy": 10, "avg_trade": 10,
                "payout_ratio": 1.0, "ret_dd_ratio": 1.0, "max_dd_pct": 12,
                "max_consec_losses": 8, "stagnation_days": 30,
            },
            # confidence_50 entirely absent
        },
    }
    live_metrics = {
        "total_trades": 80,
        "win_rate": 55, "profit_factor": 1.8, "expectancy": 20.0, "payout_ratio": 1.5,
        "ret_dd": 2.0, "max_dd_pct": 8.0, "max_consec_losses": 4, "stagnation_days": 10,
    }

    result = evaluate_cp3(live_metrics, reference_data)

    assert result["verdict"] == "SIN DATOS"
    assert result["score"] is None
    assert result["sin_datos"] is True
    assert "mc50.win_rate" in result["missing"]


# ── 3. Hard-gate partial mc95 -> SIN DATOS naming the exact missing field ──


def _cp1_reference(mc95_overrides):
    return {
        "date_added": "2020-01-01",
        "backtest": {"win_rate": 60.0, "total_trades": 300, "bt_period": "2015.01.01 - 2020.01.01"},
        "mc_manipulation": {"confidence_95": mc95_overrides},
    }


def test_hard_gate_partial_mc95_missing_max_consec_losses_names_it():
    reference_data = _cp1_reference({"max_dd_pct": 12.0})
    live_metrics = {"total_trades": 8, "max_dd_pct": 3.0, "max_consec_losses": 2, "win_rate": 62.5}

    result = evaluate_cp1(live_metrics, reference_data)

    assert result["verdict"] == "SIN DATOS"
    assert result["missing"] == ["mc95.max_consec_losses"]


def test_hard_gate_partial_mc95_missing_max_dd_pct_names_it():
    """Mirror case: max_dd_pct blank, max_consec_losses filled."""
    reference_data = _cp1_reference({"max_consec_losses": 5})
    live_metrics = {"total_trades": 8, "max_dd_pct": 3.0, "max_consec_losses": 2, "win_rate": 62.5}

    result = evaluate_cp1(live_metrics, reference_data)

    assert result["verdict"] == "SIN DATOS"
    assert result["missing"] == ["mc95.max_dd_pct"]


# ── 4. Validator flip pin: the audited MONITOREAR 63.1 fixture ─────────────

_VALIDATOR_LIVE = {
    "total_trades": 60, "weeks_operating": 20, "win_rate": 50.0, "profit_factor": 1.05,
    "payout_ratio": 0.95, "expectancy": 8.0, "max_dd_pct": 6.0, "max_consec_losses": 6,
    "stagnation_days": 18, "avg_bars_live": 14.5,
}
_VALIDATOR_BT_FULL = {
    "win_rate": 60.0, "profit_factor": 1.7, "payout_ratio": 1.25, "expectancy": 27.0,
    "avg_bars": 10.0, "max_dd_pct": 9.0, "max_consec_losses": 4, "trades_total": 300,
    "months": 48, "worst_dd_1m": 5.0, "stagnation_days": 70,
}


def test_validator_full_reference_preserves_monitorear_63_1():
    result = calculate_validator_score(
        bt=_VALIDATOR_BT_FULL,
        mc_retest={"max_dd": 12},
        mc_trades={"max_dd": 14},
        spp={"expectancy_median": 10.0},
        live=_VALIDATOR_LIVE,
    )

    assert result["score"] == 63.1
    assert result["veredicto"] == "MONITOREAR"
    assert result["sin_datos"] is False


def test_validator_missing_dd_and_spp_reference_is_sin_datos_not_eliminar():
    """Same live fixture, but worst_dd_1m/stagnation_days removed and no
    MC/SPP reference at all: was ELIMINAR 38.1, must now be SIN DATOS with
    score=None and a populated missing list."""
    bt_partial = {
        "win_rate": 60.0, "profit_factor": 1.7, "payout_ratio": 1.25, "expectancy": 27.0,
        "avg_bars": 10.0, "max_dd_pct": 9.0, "max_consec_losses": 4, "trades_total": 300,
        "months": 48,
    }

    result = calculate_validator_score(
        bt=bt_partial, mc_retest={}, mc_trades={}, spp={}, live=_VALIDATOR_LIVE
    )

    assert result["veredicto"] == "SIN DATOS"
    assert result["score"] is None
    assert result["sin_datos"] is True
    assert "bt.worst_dd_1m" in result["missing"]
    assert "mc_retest.max_dd" in result["missing"]
    assert "mc_trades.max_dd" in result["missing"]
    assert "spp.expectancy_median" in result["missing"]


# ── 5. CP3 missing reference never scores 100 (C5) ─────────────────────────


def test_score_metric_all_none_refs_fails_loudly_instead_of_scoring_100():
    """_score_metric must not silently coerce None -> 0.0 and return 100 for
    an all-missing reference; it must fail loudly (TypeError)."""
    with pytest.raises(TypeError):
        _score_metric(None, None, None, None, higher_is_better=True)


def test_cp3_missing_one_backtest_field_is_sin_datos_never_scored():
    reference_data = {
        "date_added": "2020-01-01",
        "backtest": {
            "win_rate": 55, "expectancy": 20.0, "payout_ratio": 1.5,
            "ret_dd_ratio": 2.0, "max_dd_pct": 8.0, "max_consec_losses": 4, "stagnation_days": 10,
            "total_trades": 300, "bt_period": "2015.01.01 - 2020.01.01",
            # profit_factor intentionally absent
        },
        "mc_manipulation": {
            "confidence_95": {
                "win_rate": 40, "profit_factor": 1.0, "expectancy": 10, "avg_trade": 10,
                "payout_ratio": 1.0, "ret_dd_ratio": 1.0, "max_dd_pct": 12,
                "max_consec_losses": 8, "stagnation_days": 30,
            },
            "confidence_50": {
                "win_rate": 48, "profit_factor": 1.4, "expectancy": 15, "avg_trade": 15,
                "payout_ratio": 1.2, "ret_dd_ratio": 1.5, "max_dd_pct": 10,
                "max_consec_losses": 6, "stagnation_days": 20,
            },
        },
    }
    live_metrics = {
        "total_trades": 80,
        "win_rate": 55, "profit_factor": 1.8, "expectancy": 20.0, "payout_ratio": 1.5,
        "ret_dd": 2.0, "max_dd_pct": 8.0, "max_consec_losses": 4, "stagnation_days": 10,
    }

    result = evaluate_cp3(live_metrics, reference_data)

    assert result["verdict"] == "SIN DATOS"
    assert result["score"] is None
    assert "backtest.profit_factor" in result["missing"]


# ── 6. CP2 partial MC -> SIN DATOS; full CP2 -> CONTINUAR unchanged ────────

_CP2_REFERENCE_FULL = {
    "date_added": "2020-01-01",
    "backtest": {"win_rate": 55, "total_trades": 300, "bt_period": "2015.01.01 - 2020.01.01"},
    "mc_manipulation": {
        "confidence_95": {
            "win_rate": 40, "profit_factor": 1.0, "expectancy": 10, "avg_trade": 10,
            "max_dd_pct": 12, "max_consec_losses": 8, "payout_ratio": 1.0,
        },
        "confidence_50": {
            "win_rate": 48, "profit_factor": 1.4, "expectancy": 15, "avg_trade": 15,
            "max_dd_pct": 10, "max_consec_losses": 6, "payout_ratio": 1.2,
        },
    },
}
_CP2_LIVE_HEALTHY = {
    "total_trades": 25, "win_rate": 55, "profit_factor": 1.8, "expectancy": 20.0,
    "payout_ratio": 1.5, "max_dd_pct": 8.0, "max_consec_losses": 4,
}


def test_cp2_full_reference_healthy_live_continuar_unchanged():
    result = evaluate_cp2(_CP2_LIVE_HEALTHY, _CP2_REFERENCE_FULL)

    assert result["verdict"] == "CONTINUAR"
    assert result["score"] is None  # CP2 is band-based, not scored


def test_cp2_partial_mc50_is_sin_datos_not_eliminar():
    import copy

    reference_data = copy.deepcopy(_CP2_REFERENCE_FULL)
    del reference_data["mc_manipulation"]["confidence_50"]["payout_ratio"]

    result = evaluate_cp2(_CP2_LIVE_HEALTHY, reference_data)

    assert result["verdict"] == "SIN DATOS"
    assert result["score"] is None
    assert "mc50.payout_ratio" in result["missing"]


# ── 7. PRE_CP1: date_added-based incubation clock (C7) ──────────────────────


def test_pre_cp1_zero_trade_ea_deadline_exceeded_via_date_added_clock():
    from datetime import date, timedelta

    bt_period = "2015.01.01 - 2020.01.01"
    live_metrics = {"total_trades": 0, "trades": []}
    old_date = (date.today() - timedelta(days=400)).isoformat()
    reference_data = {"date_added": old_date, "backtest": {"total_trades": 300, "bt_period": bt_period}}

    result = evaluate_incubation("EA", live_metrics, reference_data)

    assert result["verdict"] == "ELIMINAR"
    assert result["days_incubating"] == 400
    assert result["details"]["freq_deadline"] is True


def test_pre_cp1_zero_trade_ea_fresh_date_added_is_pending():
    from datetime import date

    bt_period = "2015.01.01 - 2020.01.01"
    live_metrics = {"total_trades": 0, "trades": []}
    reference_data = {
        "date_added": date.today().isoformat(),
        "backtest": {"total_trades": 300, "bt_period": bt_period},
    }

    result = evaluate_incubation("EA", live_metrics, reference_data)

    assert result["verdict"] == "PENDING"
    assert result["days_incubating"] == 0


def test_pre_cp1_unparseable_bt_period_is_sin_datos_not_perpetual_pending():
    from datetime import date, timedelta

    live_metrics = {"total_trades": 0, "trades": []}
    old_date = (date.today() - timedelta(days=400)).isoformat()
    reference_data = {
        "date_added": old_date,
        "backtest": {"total_trades": 300, "bt_period": "not a real period"},
    }

    result = evaluate_incubation("EA", live_metrics, reference_data)

    assert result["verdict"] == "SIN DATOS"
    assert "backtest.bt_period" in result["missing"]


# ── 8. below_mc95 blocker with full data (C8) ───────────────────────────────


def test_below_mc95_blocks_aprobar_with_full_data_not_missing_mc50():
    """live_dd 14.0 vs mc95_dd 10.0, ALL data present (mc50 included) ->
    below_mc95 still fires and blocks APROBAR down to OBSERVAR. Distinct
    from the old bug where the gate only worked when mc50 happened to be
    None for that metric."""
    reference_data = {
        "date_added": "2020-01-01",
        "backtest": {
            "win_rate": 55, "profit_factor": 1.8, "expectancy": 20.0, "payout_ratio": 1.5,
            "ret_dd_ratio": 2.0, "max_dd_pct": 8.0, "max_consec_losses": 4, "stagnation_days": 10,
            "total_trades": 300, "bt_period": "2015.01.01 - 2020.01.01",
        },
        "mc_manipulation": {
            "confidence_95": {
                "win_rate": 40, "profit_factor": 1.0, "expectancy": 10, "avg_trade": 10,
                "payout_ratio": 1.0, "ret_dd_ratio": 1.0, "max_dd_pct": 10.0,
                "max_consec_losses": 8, "stagnation_days": 30,
            },
            "confidence_50": {
                "win_rate": 48, "profit_factor": 1.4, "expectancy": 15, "avg_trade": 15,
                "payout_ratio": 1.2, "ret_dd_ratio": 1.5, "max_dd_pct": 9.0,
                "max_consec_losses": 6, "stagnation_days": 20,
            },
        },
    }
    live_metrics = {
        "total_trades": 80,
        "win_rate": 55, "profit_factor": 1.8, "expectancy": 20.0, "payout_ratio": 1.5,
        "ret_dd": 2.0, "max_dd_pct": 14.0, "max_consec_losses": 4, "stagnation_days": 10,
        "monthly_frequency": 300 / ((5 * 365) / 30.44),
    }

    result = evaluate_cp3(live_metrics, reference_data)

    assert result["score"] >= 65
    assert result["verdict"] == "OBSERVAR"


# ── 9. bt_period parsing: widened separators + graceful failure (C10) ──────


def test_bt_period_parses_slash_separator():
    assert calculate_monthly_frequency(300, "2024/01/02 - 2025/01/02") == pytest.approx(24.9508, rel=1e-3)


def test_bt_period_invalid_month_returns_none_no_crash():
    assert calculate_monthly_frequency(300, "2024.13.01 - 2025.01.01") is None


def test_bt_period_dash_separator_parses():
    assert calculate_monthly_frequency(300, "2024-01-02 - 2025-01-02") == pytest.approx(24.9508, rel=1e-3)


# ── 10. SPP orientation, activation, and lower-is-better inversion (C9) ────


def test_spp_confidence_higher_is_better_orientation_is_median_over_original():
    reference_data = {"backtest": {"payout_ratio": 1.0}, "spp": {"median_payout_ratio": 1.4}}
    conf = _spp_confidence(reference_data, "payout_ratio", higher_is_better=True)
    assert conf == pytest.approx(1.4)


def test_spp_confidence_lower_is_better_orientation_is_original_over_median():
    reference_data = {"backtest": {"max_dd_pct": 8.0}, "spp": {"median_max_dd_pct": 6.0}}
    conf = _spp_confidence(reference_data, "max_dd_pct", higher_is_better=False)
    assert conf == pytest.approx(8.0 / 6.0)


_CP2_SPP_REFERENCE = {
    "date_added": "2020-01-01",
    "backtest": {"win_rate": 55, "total_trades": 300, "bt_period": "2015.01.01 - 2020.01.01", "payout_ratio": 1.0},
    "mc_manipulation": {
        "confidence_95": {
            "win_rate": 40, "profit_factor": 1.0, "expectancy": 10, "avg_trade": 10,
            "max_dd_pct": 12, "max_consec_losses": 8, "payout_ratio": 1.45,
        },
        "confidence_50": {
            "win_rate": 48, "profit_factor": 1.4, "expectancy": 15, "avg_trade": 15,
            "max_dd_pct": 10, "max_consec_losses": 6, "payout_ratio": 1.6,
        },
    },
    "spp": {"median_payout_ratio": 1.4},
}
_CP2_SPP_LIVE = {
    "total_trades": 25, "win_rate": 55, "profit_factor": 1.8, "expectancy": 20.0,
    "payout_ratio": 1.4, "max_dd_pct": 8.0, "max_consec_losses": 4,
}


def test_cp2_rescue_fires_for_the_first_time_with_correct_orientation():
    """median (1.4) is 40% above original bt (1.0) -> conf=1.4>1.3, live
    sits exactly at the median -> payout_ratio rescued from failing to
    acceptable. This code path was dead before the orientation fix (design
    §5) because it read the never-populated orig_vs_median_pct ratios."""
    result = evaluate_cp2(_CP2_SPP_LIVE, _CP2_SPP_REFERENCE)

    assert result["metrics_evaluation"]["payout_ratio"]["status"] == "acceptable"
    assert "payout_ratio" in result["spp_adjustments"]


def test_cp2_spp_absent_no_adjustment_and_still_a_confident_verdict():
    reference_data = {k: v for k, v in _CP2_SPP_REFERENCE.items() if k != "spp"}

    result = evaluate_cp2(_CP2_SPP_LIVE, reference_data)

    assert result["spp_adjustments"] == []
    # A confident, scored verdict is still emitted -- SPP absence never
    # manufactures a SIN DATOS result (SPP is genuinely optional, design §2).
    assert result["verdict"] in {"CONTINUAR", "OBSERVAR", "ELIMINAR"}
    assert not result.get("sin_datos")


def test_cp3_spp_blend_shifts_score_first_ever_activation():
    reference_data = {
        "date_added": "2020-01-01",
        "backtest": {
            "win_rate": 55, "profit_factor": 1.8, "expectancy": 20.0, "payout_ratio": 1.5,
            "ret_dd_ratio": 2.0, "max_dd_pct": 8.0, "max_consec_losses": 4, "stagnation_days": 10,
            "total_trades": 300, "bt_period": "2015.01.01 - 2020.01.01",
        },
        "mc_manipulation": {
            "confidence_95": {
                "win_rate": 40, "profit_factor": 1.0, "expectancy": 10, "avg_trade": 10,
                "payout_ratio": 1.45, "ret_dd_ratio": 1.0, "max_dd_pct": 12,
                "max_consec_losses": 8, "stagnation_days": 30,
            },
            "confidence_50": {
                "win_rate": 48, "profit_factor": 1.4, "expectancy": 15, "avg_trade": 15,
                "payout_ratio": 1.6, "ret_dd_ratio": 1.5, "max_dd_pct": 10,
                "max_consec_losses": 6, "stagnation_days": 20,
            },
        },
        "spp": {"median_payout_ratio": 2.0},
    }
    live_metrics = {
        "total_trades": 80,
        "win_rate": 55, "profit_factor": 1.8, "expectancy": 20.0, "payout_ratio": 1.4,
        "ret_dd": 2.0, "max_dd_pct": 8.0, "max_consec_losses": 4, "stagnation_days": 10,
        "monthly_frequency": 300 / ((5 * 365) / 30.44),
    }

    with_spp = evaluate_cp3(live_metrics, reference_data)
    without_spp = evaluate_cp3(live_metrics, {k: v for k, v in reference_data.items() if k != "spp"})

    assert "payout_ratio" in with_spp["spp_adjustments"]
    assert without_spp["spp_adjustments"] == []
    assert with_spp["metrics_scores"]["payout_ratio"]["score"] != without_spp["metrics_scores"]["payout_ratio"]["score"]


# ── 11. Zero-loss EA: payout_ratio "∞" -> OK, not FUERA ─────────────────────


def test_validator_infinite_payout_ratio_is_ok_not_fuera():
    live = {
        "total_trades": 50, "weeks_operating": 20, "win_rate": 70.0,
        "profit_factor": "∞", "payout_ratio": "∞", "expectancy": 8.0,
        "max_dd_pct": 6.0, "max_consec_losses": 2, "stagnation_days": 18,
        "avg_bars_live": 14.5,
    }
    bt = _VALIDATOR_BT_FULL

    result = calculate_validator_score(
        bt=bt, mc_retest={}, mc_trades={}, spp={"expectancy_median": 10.0}, live=live
    )

    assert result["payout_estado"] == "OK"


# ── 12. SIN DATOS never persisted into checkpoints.cp1/cp2/cp3 slots ───────


def test_sin_datos_evaluation_not_persisted_into_checkpoint_slot():
    config = {"mappings": {"IncEA": {"magic": "1", "active": True}}}
    from datetime import datetime

    parsed_data = {
        "closed_trades": [
            {
                "position_id": i, "symbol": "EURUSD", "direction": "buy", "volume": 0.1,
                "open_time": datetime(2026, 1, i + 1, 10, 0, 0),
                "close_time": datetime(2026, 1, i + 1, 12, 0, 0),
                "open_price": 1.1, "close_price": 1.101, "sl": None, "tp": None,
                "commission": -1.0, "swap": 0.0, "profit": 11.0, "net_pnl": 10.0,
                "duration_hours": 2.0, "comment": "IncEA",
            }
            for i in range(8)  # CP1 range (5-19)
        ]
    }
    # Coarse reference_ready passes (backtest + a truthy mc95 dict exist),
    # but the CP1 required set is incomplete: max_consec_losses is missing
    # from the only provided mc95 section.
    entry = {
        "date_added": "2020-01-01",
        "backtest": {"win_rate": 60.0, "total_trades": 300, "bt_period": "2015.01.01 - 2020.01.01"},
        "mc_manipulation": {"confidence_95": {"max_dd_pct": 12.0}},
    }

    bundle = evaluate_ea("IncEA", parsed_data, config, entry)

    assert bundle["evaluation"]["verdict"] == "SIN DATOS"
    # The SIN DATOS evaluation must never be written into the cp1 slot --
    # either the "checkpoints" dict was never touched, or its cp1 slot is
    # still None. Either way, no SIN DATOS payload should land there.
    assert bundle["entry"].get("checkpoints", {}).get("cp1") is None
    assert bundle["entry"]["last_evaluation"]["verdict"] == "SIN DATOS"


# ── 13. metric_summary_for_tooltip: CP3 SIN DATOS -> no TypeError ──────────


def test_metric_summary_for_tooltip_cp3_none_score_no_crash():
    """Pre-existing crash pin: metric_summary_for_tooltip formatted
    `score:.2f` unconditionally for CP3, raising TypeError whenever CP3
    carries score=None (e.g. a hard-gate failure, or now a SIN DATOS
    result). Verified to raise TypeError before this guard existed."""
    evaluation = {
        "current_checkpoint": "CP3",
        "score": None,
        "missing": ["backtest.profit_factor", "mc95.win_rate"],
        "details": {"missing": ["backtest.profit_factor", "mc95.win_rate"]},
    }

    summary = metric_summary_for_tooltip(evaluation)

    assert summary == "SIN DATOS: 2 campos faltantes"


# ── MUST-NOT-CHANGE pins (design §9) ────────────────────────────────────────


def test_validator_weight_sums_unchanged():
    assert CONFIG["w_riesgo"] + CONFIG["w_edge"] + CONFIG["w_caracter"] + CONFIG["w_desv"] == 100
    assert CONFIG["w_dd_escalado"] + CONFIG["w_consec_losses"] + CONFIG["w_stagnation"] == 100
    assert CONFIG["w_win_rate"] + CONFIG["w_profit_factor"] + CONFIG["w_payout_ratio"] + CONFIG["w_edge_erosion"] == 100
    assert CONFIG["w_frecuencia"] + CONFIG["w_avg_bars"] == 100


def test_validator_verdict_cutoffs_unchanged():
    assert CONFIG["thresh_continuar"] == 70
    assert CONFIG["thresh_monitorear"] == 45


def test_score_metric_interpolation_continuous_at_25_65_100_boundaries():
    assert _score_metric(10.0, 10.0, 20.0, 30.0, higher_is_better=True) == pytest.approx(25.0)
    assert _score_metric(20.0, 10.0, 20.0, 30.0, higher_is_better=True) == pytest.approx(65.0)
    assert _score_metric(30.0, 10.0, 20.0, 30.0, higher_is_better=True) == pytest.approx(100.0)


def test_validator_stagnation_factors_unchanged():
    """0.3/0.6 factors: Normal <= 0.3*bt, Elevada <= 0.6*bt, else Alta."""
    base_live = {
        "total_trades": 60, "weeks_operating": 20, "win_rate": 60.0, "profit_factor": 1.7,
        "payout_ratio": 1.25, "expectancy": 27.0, "max_dd_pct": 6.0, "max_consec_losses": 4,
        "avg_bars_live": 10.0,
    }
    bt = {
        "win_rate": 60.0, "profit_factor": 1.7, "payout_ratio": 1.25, "expectancy": 27.0,
        "avg_bars": 10.0, "max_dd_pct": 9.0, "max_consec_losses": 4, "trades_total": 300,
        "months": 48, "worst_dd_1m": 5.0, "stagnation_days": 100,
    }
    spp = {"expectancy_median": 27.0}

    for sl, expected_label, expected_estado in [
        (30, "Normal", "OK"),
        (31, "Elevada", "ALERTA"),
        (60, "Elevada", "ALERTA"),
        (61, "Alta", "FUERA"),
    ]:
        live = dict(base_live)
        live["stagnation_days"] = sl
        result = calculate_validator_score(bt=bt, mc_retest={}, mc_trades={}, spp=spp, live=live)
        assert result["stagn_label"] == expected_label
        assert result["stagn_estado"] == expected_estado
