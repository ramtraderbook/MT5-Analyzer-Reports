"""
test_incubation_validator.py - Characterization tests for incubation_validator.py.

These tests PIN the CURRENT behavior of the checkpoint decision engine, not the
behavior described in AGENTS.md or docs/decision-logic.md. Where the code and
the docs disagree, the code's behavior is asserted and flagged with a `# NOTE:`
comment — it is not "fixed" here (see docs/design/domain-extraction.md, D7).

All fixtures are hardcoded and hand-computed. No real report data is used.
"""

import pytest

from incubation_validator import (
    _resolve_cp3_verdict,
    calculate_monthly_frequency,
    evaluate_cp3,
    get_checkpoint_for_trades,
    get_worst_case_mc,
)


# ── Priority 1: CP3 verdict boundaries via the `_resolve_cp3_verdict` seam ──
#
# This seam was extracted verbatim from incubation_validator.py:832-848
# (behavior-identical extraction, see docs/design/domain-extraction.md D7).


@pytest.mark.parametrize(
    "score, expected_verdict",
    [
        (65.0, "APROBAR"),
        (64.99, "OBSERVAR"),
        (45.0, "OBSERVAR"),
        (44.99, "ELIMINAR"),
    ],
)
def test_resolve_cp3_verdict_score_boundaries(score, expected_verdict):
    verdict, escalation = _resolve_cp3_verdict(score, below_mc95=[], cp2_verdict=None)

    assert verdict == expected_verdict
    assert escalation is False


def test_resolve_cp3_verdict_below_mc95_blocks_aprobar_even_at_max_score():
    """
    The below_mc95 gate (incubation_validator.py:832) requires
    `final_score >= 65 and not below_mc95` — a score of 100 with ANY metric
    below MC95 still yields OBSERVAR, never APROBAR. This is the condition
    AGENTS.md's summary omits.
    """
    verdict, escalation = _resolve_cp3_verdict(100.0, below_mc95=["win_rate"], cp2_verdict=None)

    assert verdict == "OBSERVAR"
    assert escalation is False


def test_resolve_cp3_verdict_cp2_observar_escalation():
    """CP2 OBSERVAR + CP3 lands OBSERVAR -> escalates to ELIMINAR."""
    verdict, escalation = _resolve_cp3_verdict(50.0, below_mc95=[], cp2_verdict="OBSERVAR")

    assert verdict == "ELIMINAR"
    assert escalation is True


def test_resolve_cp3_verdict_cp2_observar_does_not_escalate_aprobar():
    """Escalation only fires when the CP3 verdict itself is OBSERVAR."""
    verdict, escalation = _resolve_cp3_verdict(70.0, below_mc95=[], cp2_verdict="OBSERVAR")

    assert verdict == "APROBAR"
    assert escalation is False


def test_resolve_cp3_verdict_no_prior_cp2_result():
    """cp2_verdict=None (no previous CP2 result available) never escalates."""
    verdict, escalation = _resolve_cp3_verdict(50.0, below_mc95=[], cp2_verdict=None)

    assert verdict == "OBSERVAR"
    assert escalation is False


# ── Priority 2 & 3: below_mc95 gate + accumulation loop via evaluate_cp3 ────
#
# below_mc95 is NOT part of evaluate_cp3()'s return dict, so it can only be
# observed indirectly through its effect on the final verdict. The fixture
# below is engineered so every deviation/risk metric scores exactly 100
# (live == bt), which makes final_score == 100 regardless of any mc95/mc50
# override — isolating the below_mc95 loop's effect on the verdict from the
# scoring formula.

_BT_TOTAL_TRADES = 80
_BT_PERIOD = "2020.01.01 - 2020.06.01"
_EXPECTED_MONTHLY = calculate_monthly_frequency(_BT_TOTAL_TRADES, _BT_PERIOD)


def _cp3_reference_data():
    return {
        "backtest": {
            "win_rate": 55,
            "profit_factor": 1.8,
            "expectancy": 20.0,
            "payout_ratio": 1.5,
            "ret_dd_ratio": 2.0,
            "max_dd_pct": 8.0,
            "max_consec_losses": 4,
            "stagnation_days": 10,
            "total_trades": _BT_TOTAL_TRADES,
            "bt_period": _BT_PERIOD,
        },
        "mc_manipulation": {
            "confidence_95": {
                "win_rate": 40,
                "profit_factor": 1.0,
                "expectancy": 10,
                "avg_trade": 10,
                "payout_ratio": 1.0,
                "ret_dd_ratio": 1.0,
                "max_dd_pct": 12,
                "max_consec_losses": 8,
                "stagnation_days": 30,
            },
            "confidence_50": {
                "win_rate": 48,
                "profit_factor": 1.4,
                "expectancy": 15,
                "avg_trade": 15,
                "payout_ratio": 1.2,
                "ret_dd_ratio": 1.5,
                "max_dd_pct": 10,
                "max_consec_losses": 6,
                "stagnation_days": 20,
            },
        },
    }


def _cp3_live_metrics():
    return {
        "total_trades": _BT_TOTAL_TRADES,
        "win_rate": 55,
        "profit_factor": 1.8,
        "expectancy": 20.0,
        "payout_ratio": 1.5,
        "ret_dd": 2.0,  # NOTE: evaluate_cp3 reads live "ret_dd" but bt/mc "ret_dd_ratio"
        "max_dd_pct": 8.0,
        "max_consec_losses": 4,
        "stagnation_days": 10,
        "monthly_frequency": _EXPECTED_MONTHLY,  # forces coherence ratio == 1.0 -> score 100
    }


def test_evaluate_cp3_baseline_all_metrics_at_par_approves():
    """
    Sanity baseline: every live value equals its BT counterpart (score 100
    on every deviation/risk metric) and no metric is below MC95 -> APROBAR
    with the maximum possible score.
    """
    result = evaluate_cp3(_cp3_live_metrics(), _cp3_reference_data())

    assert result["score"] == 100.0
    assert result["verdict"] == "APROBAR"
    assert result["hard_gate_failures"] == []


def test_evaluate_cp3_below_mc95_gate_blocks_aprobar_at_perfect_score():
    """
    Priority 2: a single metric below MC95 (win_rate live=55 < overridden
    mc95=60) blocks APROBAR even though final_score is still 100 — the gate
    at incubation_validator.py:832 that AGENTS.md's summary omits.
    """
    reference_data = _cp3_reference_data()
    reference_data["mc_manipulation"]["confidence_95"]["win_rate"] = 60

    result = evaluate_cp3(_cp3_live_metrics(), reference_data)

    assert result["score"] == 100.0
    assert result["verdict"] == "OBSERVAR"


def test_evaluate_cp3_avg_trade_dedupe_skip_does_not_double_count():
    """
    Priority 3: "avg_trade" shares its live value with "expectancy"
    (incubation_validator.py:620-621) and is explicitly skipped in the
    below_mc95 accumulation loop (line 814-815) to avoid double-counting.
    Here avg_trade's own mc95 threshold (25) would trigger if evaluated
    (live 20 < 25), but expectancy's threshold (10) does not (20 >= 10).
    Because avg_trade is skipped, the verdict stays APROBAR.
    """
    reference_data = _cp3_reference_data()
    reference_data["mc_manipulation"]["confidence_95"]["avg_trade"] = 25

    result = evaluate_cp3(_cp3_live_metrics(), reference_data)

    assert result["verdict"] == "APROBAR"


def test_evaluate_cp3_below_mc95_direction_logic_lower_is_better():
    """
    Priority 3: for a lower-is-better metric (stagnation_days), below_mc95
    triggers when live > mc95 (worse than the threshold), the opposite
    direction from higher-is-better metrics.
    """
    reference_data = _cp3_reference_data()
    reference_data["mc_manipulation"]["confidence_95"]["stagnation_days"] = 8  # live=10 > 8

    result = evaluate_cp3(_cp3_live_metrics(), reference_data)

    assert result["verdict"] == "OBSERVAR"


def test_evaluate_cp3_below_mc95_none_value_is_skipped():
    """
    Priority 3: when mc50 is None for a metric (missing from the reference
    data), the below_mc95 check is skipped entirely for that metric — even
    though live (1.5) is below the overridden mc95 (2.0) and would otherwise
    trigger the gate.
    """
    reference_data = _cp3_reference_data()
    reference_data["mc_manipulation"]["confidence_95"]["payout_ratio"] = 2.0
    del reference_data["mc_manipulation"]["confidence_50"]["payout_ratio"]

    result = evaluate_cp3(_cp3_live_metrics(), reference_data)

    assert result["verdict"] == "APROBAR"


# ── Priority 4: CP2 -> CP3 escalation through evaluate_cp3 ──────────────────


def test_evaluate_cp3_escalates_from_cp2_observar_via_full_pipeline():
    """
    Previous CP2 verdict OBSERVAR + current CP3 lands OBSERVAR (forced by the
    below_mc95 gate here) -> escalates to ELIMINAR with escalation_from_cp2=True.
    """
    reference_data = _cp3_reference_data()
    reference_data["mc_manipulation"]["confidence_95"]["win_rate"] = 60  # forces OBSERVAR

    result = evaluate_cp3(
        _cp3_live_metrics(),
        reference_data,
        previous_cp2_result={"verdict": "OBSERVAR"},
    )

    assert result["verdict"] == "ELIMINAR"
    assert result["escalation_from_cp2"] is True


def test_evaluate_cp3_escalation_accepts_plain_string_cp2_result():
    """previous_cp2_result may be a plain string instead of a dict."""
    reference_data = _cp3_reference_data()
    reference_data["mc_manipulation"]["confidence_95"]["win_rate"] = 60  # forces OBSERVAR

    result = evaluate_cp3(
        _cp3_live_metrics(),
        reference_data,
        previous_cp2_result="OBSERVAR",
    )

    assert result["verdict"] == "ELIMINAR"
    assert result["escalation_from_cp2"] is True


# ── Priority 5: get_worst_case_mc dual-MC selection ─────────────────────────


def test_get_worst_case_mc_higher_is_better_picks_lower_value():
    """profit_factor is higher-is-better -> worst case is the LOWER of the two."""
    mc_manipulation = {"confidence_95": {"profit_factor": 1.2}}
    mc_retest = {"confidence_95": {"profit_factor": 0.9}}

    worst = get_worst_case_mc(mc_manipulation, mc_retest, "confidence_95")

    assert worst["profit_factor"] == 0.9


def test_get_worst_case_mc_lower_is_better_picks_higher_value():
    """max_dd_pct is lower-is-better (LOWER_IS_BETTER_KEYS) -> worst case is the HIGHER value."""
    mc_manipulation = {"confidence_95": {"max_dd_pct": 10.0}}
    mc_retest = {"confidence_95": {"max_dd_pct": 15.0}}

    worst = get_worst_case_mc(mc_manipulation, mc_retest, "confidence_95")

    assert worst["max_dd_pct"] == 15.0


def test_get_worst_case_mc_manipulation_only_when_retest_missing():
    """When retest has no value for a key, the manipulation value is used as-is."""
    mc_manipulation = {"confidence_95": {"win_rate": 42.0}}
    mc_retest = {"confidence_95": {}}

    worst = get_worst_case_mc(mc_manipulation, mc_retest, "confidence_95")

    assert worst["win_rate"] == 42.0


def test_get_worst_case_mc_retest_only_when_manipulation_missing():
    """Symmetric case: manipulation missing a key -> retest value is used as-is."""
    mc_manipulation = {"confidence_95": {}}
    mc_retest = {"confidence_95": {"win_rate": 38.0}}

    worst = get_worst_case_mc(mc_manipulation, mc_retest, "confidence_95")

    assert worst["win_rate"] == 38.0


def test_get_worst_case_mc_both_empty_returns_empty_dict():
    worst = get_worst_case_mc({}, {}, "confidence_95")

    assert worst == {}


def test_get_worst_case_mc_confidence_50_level():
    mc_manipulation = {"confidence_50": {"max_dd_pct": 6.0}}
    mc_retest = {"confidence_50": {"max_dd_pct": 9.0}}

    worst = get_worst_case_mc(mc_manipulation, mc_retest, "confidence_50")

    assert worst["max_dd_pct"] == 9.0


# ── Bonus: checkpoint thresholds (design table item 7, cheap to pin) ───────


@pytest.mark.parametrize(
    "n_trades, expected_checkpoint",
    [
        (4, "PRE_CP1"),
        (5, "CP1"),
        (19, "CP1"),
        (20, "CP2"),
        (39, "CP2"),
        (40, "CP3"),
    ],
)
def test_get_checkpoint_for_trades_boundaries(n_trades, expected_checkpoint):
    assert get_checkpoint_for_trades(n_trades) == expected_checkpoint
