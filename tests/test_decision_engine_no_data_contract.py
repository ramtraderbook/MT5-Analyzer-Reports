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
    SPP_ADJUSTMENT_ENABLED,
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


def test_validator_full_reference_preserves_monitorear():
    """The full-reference fixture must keep reaching a confident MONITOREAR.

    The score moved 63.1 -> 54.9 when freq_estado became two-sided. This
    fixture trades 60 trades in 20 weeks against a backtest pace of 300/48 =
    6.25 per month, i.e. 12.99/month = 207.8% of backtest pace. The old
    one-sided check (`OK if freq_pct >= 70`) read that 2x over-trading as OK;
    the two-sided check reads deviation 107.8 as FUERA, which also raises
    detcount to 3 and flags DESV. The lower boundaries are unchanged, so this
    is purely the newly-visible over-trading. The VERDICT is unaffected --
    that is what this test guards.
    """
    result = calculate_validator_score(
        bt=_VALIDATOR_BT_FULL,
        mc_retest={"max_dd": 12},
        mc_trades={"max_dd": 14},
        spp={"expectancy_median": 10.0},
        live=_VALIDATOR_LIVE,
    )

    assert result["score"] == 54.9
    assert result["veredicto"] == "MONITOREAR"
    assert result["sin_datos"] is False
    # The fixture over-trades 2x; that must now be visible, not silent.
    assert result["freq_estado"] == "FUERA"
    assert result["freq_pct"] == pytest.approx(207.8, abs=0.1)


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


def test_spp_confidence_median_zero_returns_none_no_crash():
    """Defense in depth (F1 correction): a median of exactly 0 used to
    divide-by-zero in the lower-is-better branch (`original / median`).
    Guarded even though the adjustment itself is currently disabled."""
    reference_data = {"backtest": {"max_dd_pct": 8.0}, "spp": {"median_max_dd_pct": 0.0}}
    assert _spp_confidence(reference_data, "max_dd_pct", higher_is_better=False) is None

    reference_data_hib = {"backtest": {"payout_ratio": 1.0}, "spp": {"median_payout_ratio": 0.0}}
    assert _spp_confidence(reference_data_hib, "payout_ratio", higher_is_better=True) is None


def test_spp_adjustment_is_disabled_module_flag():
    """F1 (CRITICAL): the SPP adjustment was DEAD since inception
    (`orig_vs_median_pct` had zero write sites). Reviving it introduced a
    ZeroDivisionError and a blend that only ever lowers scores -- verified
    strict downgrades: payout 24.14->23.14, max_dd 38.33->34.46. Disabled
    explicitly pending a redesign of the blend semantics (follow-up)."""
    assert SPP_ADJUSTMENT_ENABLED is False


# Physically plausible MC ordering for a higher-is-better metric: the
# original backtest run is the best-known result, MC50 is a moderate
# degradation, MC95 is the worst-case degradation (bt >= mc50 >= mc95). The
# pre-correction fixture violated this (bt=1.0 while mc95=1.45/mc50=1.6 --
# the "worst case" was BETTER than the actual backtest), which the design
# flagged as physically impossible (F6).
_CP2_SPP_REFERENCE = {
    "date_added": "2020-01-01",
    "backtest": {"win_rate": 55, "total_trades": 300, "bt_period": "2015.01.01 - 2020.01.01", "payout_ratio": 2.0},
    "mc_manipulation": {
        "confidence_95": {
            "win_rate": 40, "profit_factor": 1.0, "expectancy": 10, "avg_trade": 10,
            "max_dd_pct": 12, "max_consec_losses": 8, "payout_ratio": 1.0,
        },
        "confidence_50": {
            "win_rate": 48, "profit_factor": 1.4, "expectancy": 15, "avg_trade": 15,
            "max_dd_pct": 10, "max_consec_losses": 6, "payout_ratio": 1.5,
        },
    },
    "spp": {"median_payout_ratio": 2.8},
}
_CP2_SPP_LIVE = {
    "total_trades": 25, "win_rate": 55, "profit_factor": 1.8, "expectancy": 20.0,
    "payout_ratio": 0.8, "max_dd_pct": 8.0, "max_consec_losses": 4,
}


def test_cp2_spp_disabled_does_not_rescue_a_failing_status():
    """payout_ratio live (0.8) sits below MC95 (1.0) -> failing on the raw
    bands. The SPP median (2.8) is >=130% of the original bt (2.0), which
    would have rescued the metric to "acceptable" under the old (buggy)
    blend -- but the adjustment is disabled (F1), so the status and
    spp_adjustments must be unaffected by the SPP data."""
    result = evaluate_cp2(_CP2_SPP_LIVE, _CP2_SPP_REFERENCE)

    assert result["metrics_evaluation"]["payout_ratio"]["status"] == "failing"
    assert result["spp_adjustments"] == []


def test_cp2_spp_absent_no_adjustment_and_still_a_confident_verdict():
    reference_data = {k: v for k, v in _CP2_SPP_REFERENCE.items() if k != "spp"}

    result = evaluate_cp2(_CP2_SPP_LIVE, reference_data)

    assert result["spp_adjustments"] == []
    # A confident, scored verdict is still emitted -- SPP absence never
    # manufactures a SIN DATOS result (SPP is genuinely optional, design §2).
    assert result["verdict"] in {"CONTINUAR", "OBSERVAR", "ELIMINAR"}
    assert not result.get("sin_datos")


def test_cp3_spp_disabled_does_not_change_the_score():
    """Same physically plausible MC ordering as the CP2 fixture (bt >= mc50
    >= mc95 for a higher-is-better metric). While SPP_ADJUSTMENT_ENABLED is
    False, presence/absence of SPP data must produce IDENTICAL scores and
    empty spp_adjustments both ways -- SPP no longer influences any verdict
    or score (F1)."""
    reference_data = {
        "date_added": "2020-01-01",
        "backtest": {
            "win_rate": 55, "profit_factor": 1.8, "expectancy": 20.0, "payout_ratio": 2.0,
            "ret_dd_ratio": 2.0, "max_dd_pct": 8.0, "max_consec_losses": 4, "stagnation_days": 10,
            "total_trades": 300, "bt_period": "2015.01.01 - 2020.01.01",
        },
        "mc_manipulation": {
            "confidence_95": {
                "win_rate": 40, "profit_factor": 1.0, "expectancy": 10, "avg_trade": 10,
                "payout_ratio": 1.0, "ret_dd_ratio": 1.0, "max_dd_pct": 12,
                "max_consec_losses": 8, "stagnation_days": 30,
            },
            "confidence_50": {
                "win_rate": 48, "profit_factor": 1.4, "expectancy": 15, "avg_trade": 15,
                "payout_ratio": 1.5, "ret_dd_ratio": 1.5, "max_dd_pct": 10,
                "max_consec_losses": 6, "stagnation_days": 20,
            },
        },
        "spp": {"median_payout_ratio": 2.8},
    }
    live_metrics = {
        "total_trades": 80,
        "win_rate": 55, "profit_factor": 1.8, "expectancy": 20.0, "payout_ratio": 1.4,
        "ret_dd": 2.0, "max_dd_pct": 8.0, "max_consec_losses": 4, "stagnation_days": 10,
        "monthly_frequency": 300 / ((5 * 365) / 30.44),
    }

    with_spp = evaluate_cp3(live_metrics, reference_data)
    without_spp = evaluate_cp3(live_metrics, {k: v for k, v in reference_data.items() if k != "spp"})

    assert with_spp["spp_adjustments"] == []
    assert without_spp["spp_adjustments"] == []
    assert with_spp["metrics_scores"]["payout_ratio"]["score"] == without_spp["metrics_scores"]["payout_ratio"]["score"]
    assert with_spp["score"] == without_spp["score"]


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


# ── 14. validator: N/D must not reach scoring (F2 correction round) ────────
# The design §6 claim "no scored estado can be N/D when a verdict is
# emitted" was false: the presence-only gate let degenerate-but-present
# values (weeks_live<=0, a reference field literally 0) fall through to a
# confident CONTINUAR verdict with one or more estados silently "N/D".


def test_validator_weeks_operating_zero_day_one_scalper_is_sin_datos():
    """An EA that closes 5+ trades on its FIRST DAY has weeks_operating=0.

    Frecuencia still requires weeks_live > 0 (trades per month is undefined
    over zero weeks), so it computes "N/D" while every OTHER estado stays
    confident -- this used to reach veredicto=CONTINUAR score=74.2
    missing=[]. Must be SIN DATOS.

    DD-escalado no longer contributes to `missing` here: it scales on the
    TRADE clock (sqrt(trades_live / bt_freq_mes)), not on weeks_live, so 60
    trades on day one carry enough accumulated variance to judge the
    drawdown even though the calendar says zero weeks. That is deliberate --
    the calendar clock made dd_limit 0.91% on day one and hard-eliminated
    healthy newborn scalpers. The SIN DATOS verdict itself is unchanged.
    """
    live = dict(_VALIDATOR_LIVE)
    live["weeks_operating"] = 0

    result = calculate_validator_score(
        bt=_VALIDATOR_BT_FULL, mc_retest={}, mc_trades={}, spp={"expectancy_median": 10.0}, live=live
    )

    assert result["veredicto"] == "SIN DATOS"
    assert result["score"] is None
    assert result["sin_datos"] is True
    assert "freq_estado" in result["missing"]
    assert "live.weeks_operating" in result["missing"]
    # DD is now evaluable from the trade clock, so it must NOT be reported
    # as missing -- but the SIN DATOS blanking still applies to its estado.
    assert "dd_estado" not in result["missing"]
    assert result["dd_estado"] == "N/D"
    # No confident threshold may survive next to an N/D estado/method.
    assert result["dd_limit"] is None
    assert result["dd_method"] == "N/D"


def test_validator_worst_dd_1m_zero_no_mc_fallback_is_sin_datos():
    """bt.worst_dd_1m present but literally 0.0 (degenerate, not missing)
    with no MC reference to fall back on: dd_estado computed "N/D" while
    freq_estado stayed confident (weeks_live > 0) -- this used to reach
    veredicto=CONTINUAR score=78.0 missing=[]. Must now be SIN DATOS."""
    bt = dict(_VALIDATOR_BT_FULL)
    bt["worst_dd_1m"] = 0.0

    result = calculate_validator_score(
        bt=bt, mc_retest={}, mc_trades={}, spp={"expectancy_median": 10.0}, live=_VALIDATOR_LIVE
    )

    assert result["veredicto"] == "SIN DATOS"
    assert result["score"] is None
    assert "dd_estado" in result["missing"]
    assert "bt.worst_dd_1m" in result["missing"]
    assert "freq_estado" not in result["missing"]  # frequency stayed usable


def test_validator_spp_expect_median_zero_is_sin_datos():
    """spp.expectancy_median present but literally 0 (degenerate, not
    missing per the presence gate) makes edge_estado's percentage-erosion
    computation ill-defined -> N/D. Must be SIN DATOS, not a confident
    verdict that silently ignores the edge dimension."""
    result = calculate_validator_score(
        bt=_VALIDATOR_BT_FULL, mc_retest={"max_dd": 12}, mc_trades={"max_dd": 14},
        spp={"expectancy_median": 0.0}, live=_VALIDATOR_LIVE,
    )

    assert result["veredicto"] == "SIN DATOS"
    assert result["score"] is None
    assert "edge_estado" in result["missing"]
    assert "spp.expectancy_median" in result["missing"]


# ── 15. incubation CP3: zero-drawdown ret_dd (F3 correction round) ─────────

_CP3_FULL_REFERENCE = {
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
        "confidence_50": {
            "win_rate": 48, "profit_factor": 1.4, "expectancy": 15, "avg_trade": 15,
            "payout_ratio": 1.2, "ret_dd_ratio": 1.5, "max_dd_pct": 10,
            "max_consec_losses": 6, "stagnation_days": 20,
        },
    },
}


def test_cp3_zero_drawdown_ea_with_trades_is_scored_not_sin_datos():
    """`metrics.py` sets `ret_dd=None` whenever `max_dd_dollar <= 0`. A
    flawless EA with 40+ trades and zero drawdown was SIN DATOS forever,
    naming `live.ret_dd` -- a field no form can supply. Zero drawdown means
    ret/dd is mathematically INFINITE (maximally good), not missing."""
    live_metrics = {
        "total_trades": 80,
        "win_rate": 55, "profit_factor": 1.8, "expectancy": 20.0, "payout_ratio": 1.5,
        # "ret_dd" intentionally absent -- metrics.py yields None here
        "max_dd_pct": 0.0, "max_consec_losses": 4, "stagnation_days": 10,
    }

    result = evaluate_cp3(live_metrics, _CP3_FULL_REFERENCE)

    assert result["verdict"] != "SIN DATOS"
    assert result["score"] is not None
    assert result["metrics_scores"]["ret_dd_ratio"]["score"] == pytest.approx(100.0)


def test_cp3_ret_dd_none_with_nonzero_drawdown_is_still_sin_datos():
    """ret_dd=None for any OTHER reason (non-zero drawdown) must stay
    missing -- only the zero-drawdown case is reinterpreted as infinite."""
    live_metrics = {
        "total_trades": 80,
        "win_rate": 55, "profit_factor": 1.8, "expectancy": 20.0, "payout_ratio": 1.5,
        # "ret_dd" intentionally absent, but max_dd_pct is non-zero
        "max_dd_pct": 8.0, "max_consec_losses": 4, "stagnation_days": 10,
    }

    result = evaluate_cp3(live_metrics, _CP3_FULL_REFERENCE)

    assert result["verdict"] == "SIN DATOS"
    assert "live.ret_dd" in result["missing"]


# ── 16. dashboard: SIN DATOS row must not show a stale score (F5) ──────────


def _cp3_trades(ea_name, count):
    from datetime import datetime

    return [
        {
            "position_id": i, "symbol": "EURUSD", "direction": "buy", "volume": 0.1,
            "open_time": datetime(2026, 1, 1, 10, 0, 0),
            "close_time": datetime(2026, 1, 1 + (i % 27), 12, 0, 0),
            "open_price": 1.1, "close_price": 1.101, "sl": None, "tp": None,
            "commission": -1.0, "swap": 0.0, "profit": 11.0, "net_pnl": 10.0,
            "duration_hours": 2.0, "comment": ea_name,
        }
        for i in range(count)
    ]


def test_dashboard_sin_datos_row_does_not_render_a_stale_score():
    """Reproduces the F5 bug: a checkpoint slot (cp3) still holds a numeric
    score from an earlier CONFIDENT evaluation. The CURRENT evaluation is
    SIN DATOS (a backtest field went missing), which is never persisted
    into the cp3 slot (design §1) -- so `current_result_from_entry` used to
    fall back to the stale cp3 result and unconditionally overwrite
    score_display, rendering "SIN DATOS" next to a stale "72.50"."""
    import ea_analyzer

    config = {"mappings": {"IncEA": {"magic": "1", "alias": "", "active": True}}}
    parsed_data = {"closed_trades": _cp3_trades("IncEA", 40)}
    entry = {
        "date_added": "2020-01-01",
        "backtest": {
            "win_rate": 55, "expectancy": 20.0, "payout_ratio": 1.5,
            "ret_dd_ratio": 2.0, "max_dd_pct": 8.0, "max_consec_losses": 4, "stagnation_days": 10,
            "total_trades": 300, "bt_period": "2015.01.01 - 2020.01.01",
            # profit_factor intentionally absent -> CP3 SIN DATOS this run
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
        # Stale cp3 slot from an earlier, complete-reference evaluation.
        "checkpoints": {
            "cp1": None,
            "cp2": None,
            "cp3": {"checkpoint": "CP3", "verdict": "APROBAR", "score": 72.5},
        },
    }
    store = {"IncEA": entry}

    with ea_analyzer.app.test_request_context():
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(ea_analyzer, "get_incubation_parsed_data", lambda: parsed_data)
            mp.setattr(ea_analyzer, "load_incubation_config", lambda: config)
            mp.setattr(ea_analyzer, "load_incubation_store", lambda: store)
            mp.setattr(ea_analyzer, "save_incubation_store", lambda data: None)

            dashboard = ea_analyzer._build_incubation_dashboard()

    row = next(r for r in dashboard["rows"] if r["name"] == "IncEA")
    assert row["verdict"] == "SIN DATOS"
    assert row["score"] != "72.50"
    assert dashboard["sin_datos_count"] == 1


# ── 17. incubation CP1: max_consec_losses="∞" is graceful, not a crash (F7) ─


def test_cp1_max_consec_losses_infinity_string_is_sin_datos_not_typeerror():
    """The completeness gate used to accept "∞" for mc95.max_consec_losses
    (its `_safe_float` check maps "∞" -> a finite sentinel), but the
    consumer (`_hard_gates`) reads the same field via `_safe_int`, which
    returns None for "∞" -- `live_value <= None` then raised TypeError.
    The gate now parses this field the same way its consumer does."""
    reference_data = _cp1_reference({"max_dd_pct": 12.0, "max_consec_losses": "∞"})
    live_metrics = {"total_trades": 8, "max_dd_pct": 3.0, "max_consec_losses": 2, "win_rate": 62.5}

    result = evaluate_cp1(live_metrics, reference_data)  # must not raise TypeError

    assert result["verdict"] == "SIN DATOS"
    assert "mc95.max_consec_losses" in result["missing"]


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
