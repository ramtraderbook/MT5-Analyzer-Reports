"""
incubation_validator.py
Progressive checkpoint evaluator for Incubation Screening.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime

try:
    from scipy.stats import binom

    _HAS_SCIPY = True
except Exception:  # pragma: no cover - optional dependency
    binom = None
    _HAS_SCIPY = False


def _safe_float(value, default=None):
    if value is None or value == "":
        return default
    if isinstance(value, str):
        text = value.strip().replace(",", ".")
        if text in {"∞", "inf", "+inf", "Infinity"}:
            return float("inf")
        if text in {"-∞", "-inf", "-Infinity"}:
            return float("-inf")
        try:
            return float(text)
        except ValueError:
            return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default=None):
    if value is None or value == "":
        return default
    try:
        return int(round(float(str(value).replace(",", "."))))
    except (TypeError, ValueError):
        return default


def _parse_dt(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _trade_list(live_metrics):
    trades = live_metrics.get("trades") or []
    return [t for t in trades if t.get("close_time") is not None]


def _first_trade_date(live_metrics):
    dates = []
    for trade in _trade_list(live_metrics):
        dt = _parse_dt(trade.get("close_time"))
        if dt:
            dates.append(dt)
    return min(dates) if dates else None


def _wins_from_metrics(live_metrics):
    wins = live_metrics.get("winning_trades")
    if wins is not None:
        return _safe_int(wins, 0)

    total = _safe_int(live_metrics.get("total_trades"), 0) or 0
    wr = _safe_float(live_metrics.get("win_rate"), 0.0) or 0.0
    return int(round(total * wr / 100.0))


def _get_reference_value(reference_data, path, default=None):
    cur = reference_data or {}
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
        if cur is None:
            return default
    return cur


HIGHER_IS_BETTER_KEYS = {
    "profit_factor",
    "win_rate",
    "expectancy",
    "avg_trade",
    "payout_ratio",
    "ret_dd_ratio",
    "sqn_score",
}

LOWER_IS_BETTER_KEYS = {
    "max_dd_pct",
    "max_consec_losses",
    "stagnation_days",
}


def _dual_mc_sections(reference_data):
    if not isinstance(reference_data, dict):
        return None, None

    mc_manipulation = reference_data.get("mc_manipulation")
    mc_retest = reference_data.get("mc_retest")

    if not mc_manipulation and not mc_retest:
        legacy = reference_data.get("monte_carlo")
        if isinstance(legacy, dict):
            mc_manipulation = legacy

    return mc_manipulation, mc_retest


def _mc_section_values(mc_section, confidence_level):
    if not isinstance(mc_section, dict):
        return {}
    values = mc_section.get(confidence_level, {})
    if isinstance(values, dict) and values:
        return values

    fallback_level = "confidence_50" if confidence_level == "confidence_95" else "confidence_95"
    fallback = mc_section.get(fallback_level, {})
    return fallback if isinstance(fallback, dict) else {}


def _mc_source_bundle(reference_data, confidence_level):
    mc_manipulation, mc_retest = _dual_mc_sections(reference_data)
    manip = _mc_section_values(mc_manipulation, confidence_level)
    retest = _mc_section_values(mc_retest, confidence_level)

    worst = {}
    dominant = {}
    all_keys = sorted(set(manip.keys()) | set(retest.keys()))

    for key in all_keys:
        m_val = manip.get(key)
        r_val = retest.get(key)
        if m_val is None and r_val is None:
            continue

        if m_val is None:
            worst[key] = r_val
            dominant[key] = "retest"
            continue

        if r_val is None:
            worst[key] = m_val
            dominant[key] = "manipulation"
            continue

        if key in HIGHER_IS_BETTER_KEYS:
            if r_val < m_val:
                worst[key] = r_val
                dominant[key] = "retest"
            else:
                worst[key] = m_val
                dominant[key] = "manipulation"
        elif key in LOWER_IS_BETTER_KEYS:
            if r_val > m_val:
                worst[key] = r_val
                dominant[key] = "retest"
            else:
                worst[key] = m_val
                dominant[key] = "manipulation"
        else:
            worst[key] = m_val
            dominant[key] = "manipulation"

    return {
        "mc_manipulation": manip,
        "mc_retest": retest,
        "worst": worst,
        "dominant": dominant,
        "has_manipulation": bool(manip),
        "has_retest": bool(retest),
    }


def get_worst_case_mc(mc_manipulation, mc_retest, confidence_level="confidence_95"):
    """
    Return the most conservative MC value per metric between manipulation and retest.
    """
    bundle = _mc_source_bundle(
        {
            "mc_manipulation": mc_manipulation,
            "mc_retest": mc_retest,
        },
        confidence_level,
    )
    return bundle["worst"]


def _binomial_p_value(wins, n, p):
    if n <= 0:
        return 1.0
    p = max(0.0, min(1.0, p))
    wins = max(0, min(int(wins), int(n)))

    if _HAS_SCIPY:
        return float(binom.cdf(wins, n, p))

    variance = n * p * (1 - p)
    if variance <= 0:
        return 1.0 if wins >= n * p else 0.0

    z = (wins - n * p) / math.sqrt(variance)
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def calculate_monthly_frequency(total_trades, bt_period_str):
    if not bt_period_str:
        return 0.0

    match = re.search(
        r"(\d{4}\.\d{2}\.\d{2})\s*-\s*(\d{4}\.\d{2}\.\d{2})",
        str(bt_period_str).strip(),
    )
    if not match:
        return 0.0

    start = datetime.strptime(match.group(1), "%Y.%m.%d").date()
    end = datetime.strptime(match.group(2), "%Y.%m.%d").date()
    days = max((end - start).days, 1)
    months = days / 30.44
    if months <= 0:
        return 0.0
    return float(total_trades) / months


def get_checkpoint_for_trades(n_trades):
    if n_trades < 5:
        return "PRE_CP1"
    if n_trades < 20:
        return "CP1"
    if n_trades < 40:
        return "CP2"
    return "CP3"


def _hard_gates(live_metrics, reference_data):
    total_trades = _safe_int(live_metrics.get("total_trades"), 0) or 0
    wins = _wins_from_metrics(live_metrics)

    live_dd = _safe_float(live_metrics.get("max_dd_pct"), 0.0) or 0.0
    live_mcl = _safe_int(live_metrics.get("max_consec_losses"), 0) or 0
    live_wr = _safe_float(live_metrics.get("win_rate"), 0.0) or 0.0

    bt_wr = _safe_float(_get_reference_value(reference_data, ("backtest", "win_rate")), 0.0) or 0.0
    bt_total = _safe_int(_get_reference_value(reference_data, ("backtest", "total_trades")), 0) or total_trades
    bt_period = _get_reference_value(reference_data, ("backtest", "bt_period"), "")
    bt_monthly = _safe_float(_get_reference_value(reference_data, ("backtest", "monthly_frequency")))
    expected_monthly = bt_monthly if bt_monthly is not None else calculate_monthly_frequency(bt_total, bt_period)

    mc95_bundle = _mc_source_bundle(reference_data, "confidence_95")
    mc95_dd = _safe_float(mc95_bundle["worst"].get("max_dd_pct"), 0.0) or 0.0
    mc95_mcl = _safe_int(mc95_bundle["worst"].get("max_consec_losses"), 0) or 0

    dd_threshold = mc95_dd * 1.5
    dd_passed = live_dd <= dd_threshold

    p_value = _binomial_p_value(wins, total_trades, bt_wr / 100.0)
    wr_passed = p_value >= 0.03

    mcl_passed = live_mcl <= mc95_mcl

    actual_monthly = 0.0
    first_dt = _first_trade_date(live_metrics)
    if first_dt:
        days_incubating = max((date.today() - first_dt.date()).days, 1)
        actual_monthly = total_trades / (days_incubating / 30.44)
    else:
        days_incubating = 0

    freq_warning = "OK"
    if expected_monthly > 0:
        ratio = actual_monthly / expected_monthly
        if ratio < 0.25 or ratio > 3.0:
            freq_warning = "WARNING"

    return {
        "mc_source": {
            "has_manipulation": mc95_bundle["has_manipulation"],
            "has_retest": mc95_bundle["has_retest"],
            "dominant_metrics": mc95_bundle["dominant"],
        },
        "dd_extreme": {
            "passed": dd_passed,
            "live_value": round(live_dd, 4),
            "threshold": round(dd_threshold, 4),
        },
        "win_rate_binomial": {
            "passed": wr_passed,
            "live_wr": round(live_wr, 4),
            "bt_wr": round(bt_wr, 4),
            "p_value": round(p_value, 6),
            "wins": wins,
            "n": total_trades,
        },
        "max_consec_losses": {
            "passed": mcl_passed,
            "live_value": live_mcl,
            "mc95_value": mc95_mcl,
        },
        "frequency": {
            "status": freq_warning,
            "expected": round(expected_monthly, 4),
            "actual": round(actual_monthly, 4),
        },
    }


def evaluate_cp1(live_metrics, reference_data):
    gates = _hard_gates(live_metrics, reference_data)

    hard_gate_failures = []
    if not gates["dd_extreme"]["passed"]:
        hard_gate_failures.append("dd_extreme")
    if not gates["win_rate_binomial"]["passed"]:
        hard_gate_failures.append("win_rate_binomial")
    if not gates["max_consec_losses"]["passed"]:
        hard_gate_failures.append("max_consec_losses")

    verdict = "ELIMINAR" if hard_gate_failures else "CONTINUAR"
    return {
        "checkpoint": "CP1",
        "verdict": verdict,
        "score": None,
        "gates": gates,
        "hard_gate_failures": hard_gate_failures,
        "mc_source": gates.get("mc_source", {}),
    }


def _metric_status(value, mc50, mc95, higher_is_better=True):
    if value is None or mc50 is None or mc95 is None:
        return "failing"

    if higher_is_better:
        if value >= mc50:
            return "good"
        if value >= mc95:
            return "acceptable"
        return "failing"

    if value <= mc50:
        return "good"
    if value <= mc95:
        return "acceptable"
    return "failing"


def _spp_confidence(reference_data, key):
    spp = reference_data.get("spp", {}) if isinstance(reference_data, dict) else {}
    ratios = spp.get("orig_vs_median_pct", {}) if isinstance(spp, dict) else {}
    raw = _safe_float(ratios.get(key))
    return (raw / 100.0) if raw is not None else None


def _spp_median(reference_data, key):
    spp = reference_data.get("spp", {}) if isinstance(reference_data, dict) else {}
    return _safe_float(spp.get(key))


def evaluate_cp2(live_metrics, reference_data):
    gates = _hard_gates(live_metrics, reference_data)
    hard_gate_failures = [
        gate for gate in ("dd_extreme", "win_rate_binomial", "max_consec_losses") if not gates[gate]["passed"]
    ]
    mc_manipulation, mc_retest = _dual_mc_sections(reference_data)
    mc95_bundle = _mc_source_bundle(reference_data, "confidence_95")
    mc50_bundle = _mc_source_bundle(reference_data, "confidence_50")

    if hard_gate_failures:
        return {
            "checkpoint": "CP2",
            "verdict": "ELIMINAR",
            "score": None,
            "gates": gates,
            "hard_gate_failures": hard_gate_failures,
            "metrics_evaluation": {},
            "failing_count": None,
            "spp_adjustments": [],
            "mc_source": {
                "has_manipulation": bool(mc_manipulation),
                "has_retest": bool(mc_retest),
                "dominant_metrics": mc95_bundle["dominant"],
                "dominant_metrics_50": mc50_bundle["dominant"],
            },
        }

    metric_specs = [
        ("win_rate", "win_rate", "win_rate", True),
        ("profit_factor", "profit_factor", "profit_factor", True),
        ("expectancy", "expectancy", "expectancy", True),
        ("max_dd_pct", "max_dd_pct", "max_dd_pct", False),
        ("max_consec_losses", "max_consec_losses", "max_consec_losses", False),
        ("payout_ratio", "payout_ratio", "payout_ratio", True),
        ("avg_trade", "expectancy", "avg_trade", True),
    ]

    metrics_eval = {}
    spp_adjustments = []
    failing_count = 0

    for output_key, live_key, mc_key, higher_is_better in metric_specs:
        live_value = _safe_float(live_metrics.get(live_key), 0.0)
        if output_key == "avg_trade":
            live_value = _safe_float(live_metrics.get("expectancy"), 0.0)
        if output_key == "max_consec_losses":
            live_value = _safe_float(live_metrics.get("max_consec_losses"), 0.0)

        mc50 = _safe_float(mc50_bundle["worst"].get(mc_key))
        mc95 = _safe_float(mc95_bundle["worst"].get(mc_key))
        mc50_manip = mc50_bundle["mc_manipulation"].get(mc_key)
        mc50_retest = mc50_bundle["mc_retest"].get(mc_key)
        mc95_manip = mc95_bundle["mc_manipulation"].get(mc_key)
        mc95_retest = mc95_bundle["mc_retest"].get(mc_key)
        spp_median = None
        spp_key_map = {
            "max_dd_pct": "median_max_dd_pct",
            "max_consec_losses": None,
            "payout_ratio": "median_payout_ratio",
            "avg_trade": "median_avg_trade",
            "expectancy": "median_avg_trade",
            "win_rate": None,
            "profit_factor": None,
        }
        spp_key = spp_key_map.get(output_key)
        if spp_key:
            spp_median = _spp_median(reference_data, spp_key)

        status = _metric_status(live_value, mc50, mc95, higher_is_better=higher_is_better)
        spp_conf = None

        if spp_median is not None:
            spp_conf = _spp_confidence(
                reference_data,
                {
                    "max_dd_pct": "max_dd_pct",
                    "max_consec_losses": "max_consec_losses",
                    "payout_ratio": "payout_ratio",
                    "avg_trade": "avg_trade",
                    "expectancy": "avg_trade",
                }.get(output_key, ""),
            )
            if spp_conf and spp_conf > 1.3 and status == "failing":
                spp_status = "good" if (
                    (higher_is_better and live_value >= spp_median)
                    or (not higher_is_better and live_value <= spp_median)
                ) else "failing"
                if spp_status == "good":
                    status = "acceptable"
                    spp_adjustments.append(output_key)

        metrics_eval[output_key] = {
            "live": live_value,
            "mc50": mc50,
            "mc95": mc95,
            "mc50_manipulation": mc50_manip,
            "mc50_retest": mc50_retest,
            "mc95_manipulation": mc95_manip,
            "mc95_retest": mc95_retest,
            "dominant_50": mc50_bundle["dominant"].get(mc_key),
            "dominant_95": mc95_bundle["dominant"].get(mc_key),
            "spp_median": spp_median,
            "status": status,
        }
        if status == "failing":
            failing_count += 1

    if failing_count <= 1:
        verdict = "CONTINUAR"
    elif failing_count == 2:
        verdict = "OBSERVAR"
    else:
        verdict = "ELIMINAR"

    return {
        "checkpoint": "CP2",
        "verdict": verdict,
        "score": None,
        "gates": gates,
        "hard_gate_failures": hard_gate_failures,
        "metrics_evaluation": metrics_eval,
        "failing_count": failing_count,
        "spp_adjustments": spp_adjustments,
        "mc_source": {
            "has_manipulation": bool(mc_manipulation),
            "has_retest": bool(mc_retest),
            "dominant_metrics": mc95_bundle["dominant"],
            "dominant_metrics_50": mc50_bundle["dominant"],
        },
    }


def _score_metric(live_value, mc95_value, mc50_value, bt_value, higher_is_better=True):
    live_value = _safe_float(live_value, 0.0) or 0.0
    mc95_value = _safe_float(mc95_value, 0.0) or 0.0
    mc50_value = _safe_float(mc50_value, 0.0) or 0.0
    bt_value = _safe_float(bt_value, 0.0) or 0.0

    if higher_is_better:
        if live_value >= bt_value:
            return 100.0
        if live_value >= mc50_value:
            return 65 + 35 * (live_value - mc50_value) / max(bt_value - mc50_value, 0.001)
        if live_value >= mc95_value:
            return 25 + 40 * (live_value - mc95_value) / max(mc50_value - mc95_value, 0.001)
        return max(0.0, 25 * live_value / max(mc95_value, 0.001))

    if live_value <= bt_value:
        return 100.0
    if live_value <= mc50_value:
        return 65 + 35 * (mc50_value - live_value) / max(mc50_value - bt_value, 0.001)
    if live_value <= mc95_value:
        return 25 + 40 * (mc95_value - live_value) / max(mc95_value - mc50_value, 0.001)
    return max(0.0, 25 * mc95_value / max(live_value, 0.001))


def evaluate_cp3(live_metrics, reference_data, previous_cp2_result=None):
    gates = _hard_gates(live_metrics, reference_data)
    hard_gate_failures = [
        gate for gate in ("dd_extreme", "win_rate_binomial", "max_consec_losses") if not gates[gate]["passed"]
    ]
    mc_manipulation, mc_retest = _dual_mc_sections(reference_data)
    mc95_bundle = _mc_source_bundle(reference_data, "confidence_95")
    mc50_bundle = _mc_source_bundle(reference_data, "confidence_50")
    if hard_gate_failures:
        return {
            "checkpoint": "CP3",
            "verdict": "ELIMINAR",
            "score": None,
            "category_scores": {},
            "metrics_scores": {},
            "spp_adjustments": [],
            "gates": gates,
            "hard_gate_failures": hard_gate_failures,
            "escalation_from_cp2": False,
            "mc_source": {
                "has_manipulation": bool(mc_manipulation),
                "has_retest": bool(mc_retest),
                "dominant_metrics": mc95_bundle["dominant"],
                "dominant_metrics_50": mc50_bundle["dominant"],
            },
        }

    bt = reference_data.get("backtest", {}) if isinstance(reference_data, dict) else {}
    mc95 = mc95_bundle["worst"]
    mc50 = mc50_bundle["worst"]

    metric_map = {
        "win_rate": ("win_rate", True),
        "profit_factor": ("profit_factor", True),
        "expectancy": ("expectancy", True),
        "avg_trade": ("expectancy", True),
        "payout_ratio": ("payout_ratio", True),
        "ret_dd_ratio": ("ret_dd", True),
        "max_dd_pct": ("max_dd_pct", False),
        "max_consec_losses": ("max_consec_losses", False),
        "stagnation_days": ("stagnation_days", False),
    }

    metric_sources = {
        "win_rate": {
            "live": _safe_float(live_metrics.get("win_rate")),
            "bt": _safe_float(bt.get("win_rate")),
            "mc95": _safe_float(mc95.get("win_rate")),
            "mc50": _safe_float(mc50.get("win_rate")),
            "mc95_manipulation": mc95_bundle["mc_manipulation"].get("win_rate"),
            "mc95_retest": mc95_bundle["mc_retest"].get("win_rate"),
            "mc50_manipulation": mc50_bundle["mc_manipulation"].get("win_rate"),
            "mc50_retest": mc50_bundle["mc_retest"].get("win_rate"),
            "dominant_95": mc95_bundle["dominant"].get("win_rate"),
            "dominant_50": mc50_bundle["dominant"].get("win_rate"),
            "spp": _spp_median(reference_data, "median_win_rate"),
            "spp_conf": _spp_confidence(reference_data, "win_rate"),
            "higher": True,
        },
        "profit_factor": {
            "live": _safe_float(live_metrics.get("profit_factor")),
            "bt": _safe_float(bt.get("profit_factor")),
            "mc95": _safe_float(mc95.get("profit_factor")),
            "mc50": _safe_float(mc50.get("profit_factor")),
            "mc95_manipulation": mc95_bundle["mc_manipulation"].get("profit_factor"),
            "mc95_retest": mc95_bundle["mc_retest"].get("profit_factor"),
            "mc50_manipulation": mc50_bundle["mc_manipulation"].get("profit_factor"),
            "mc50_retest": mc50_bundle["mc_retest"].get("profit_factor"),
            "dominant_95": mc95_bundle["dominant"].get("profit_factor"),
            "dominant_50": mc50_bundle["dominant"].get("profit_factor"),
            "spp": _spp_median(reference_data, "median_profit_factor"),
            "spp_conf": _spp_confidence(reference_data, "profit_factor"),
            "higher": True,
        },
        "expectancy": {
            "live": _safe_float(live_metrics.get("expectancy")),
            "bt": _safe_float(bt.get("expectancy")),
            "mc95": _safe_float(mc95.get("expectancy")),
            "mc50": _safe_float(mc50.get("expectancy")),
            "mc95_manipulation": mc95_bundle["mc_manipulation"].get("expectancy"),
            "mc95_retest": mc95_bundle["mc_retest"].get("expectancy"),
            "mc50_manipulation": mc50_bundle["mc_manipulation"].get("expectancy"),
            "mc50_retest": mc50_bundle["mc_retest"].get("expectancy"),
            "dominant_95": mc95_bundle["dominant"].get("expectancy"),
            "dominant_50": mc50_bundle["dominant"].get("expectancy"),
            "spp": _spp_median(reference_data, "median_avg_trade"),
            "spp_conf": _spp_confidence(reference_data, "avg_trade"),
            "higher": True,
        },
        "avg_trade": {
            "live": _safe_float(live_metrics.get("expectancy")),
            "bt": _safe_float(bt.get("expectancy")),
            "mc95": _safe_float(mc95.get("avg_trade")),
            "mc50": _safe_float(mc50.get("avg_trade")),
            "mc95_manipulation": mc95_bundle["mc_manipulation"].get("avg_trade"),
            "mc95_retest": mc95_bundle["mc_retest"].get("avg_trade"),
            "mc50_manipulation": mc50_bundle["mc_manipulation"].get("avg_trade"),
            "mc50_retest": mc50_bundle["mc_retest"].get("avg_trade"),
            "dominant_95": mc95_bundle["dominant"].get("avg_trade"),
            "dominant_50": mc50_bundle["dominant"].get("avg_trade"),
            "spp": _spp_median(reference_data, "median_avg_trade"),
            "spp_conf": _spp_confidence(reference_data, "avg_trade"),
            "higher": True,
        },
        "payout_ratio": {
            "live": _safe_float(live_metrics.get("payout_ratio")),
            "bt": _safe_float(bt.get("payout_ratio")),
            "mc95": _safe_float(mc95.get("payout_ratio")),
            "mc50": _safe_float(mc50.get("payout_ratio")),
            "mc95_manipulation": mc95_bundle["mc_manipulation"].get("payout_ratio"),
            "mc95_retest": mc95_bundle["mc_retest"].get("payout_ratio"),
            "mc50_manipulation": mc50_bundle["mc_manipulation"].get("payout_ratio"),
            "mc50_retest": mc50_bundle["mc_retest"].get("payout_ratio"),
            "dominant_95": mc95_bundle["dominant"].get("payout_ratio"),
            "dominant_50": mc50_bundle["dominant"].get("payout_ratio"),
            "spp": _spp_median(reference_data, "median_payout_ratio"),
            "spp_conf": _spp_confidence(reference_data, "payout_ratio"),
            "higher": True,
        },
        "ret_dd_ratio": {
            "live": _safe_float(live_metrics.get("ret_dd")),
            "bt": _safe_float(bt.get("ret_dd_ratio")),
            "mc95": _safe_float(mc95.get("ret_dd_ratio")),
            "mc50": _safe_float(mc50.get("ret_dd_ratio")),
            "mc95_manipulation": mc95_bundle["mc_manipulation"].get("ret_dd_ratio"),
            "mc95_retest": mc95_bundle["mc_retest"].get("ret_dd_ratio"),
            "mc50_manipulation": mc50_bundle["mc_manipulation"].get("ret_dd_ratio"),
            "mc50_retest": mc50_bundle["mc_retest"].get("ret_dd_ratio"),
            "dominant_95": mc95_bundle["dominant"].get("ret_dd_ratio"),
            "dominant_50": mc50_bundle["dominant"].get("ret_dd_ratio"),
            "spp": _spp_median(reference_data, "median_ret_dd_ratio"),
            "spp_conf": _spp_confidence(reference_data, "ret_dd_ratio"),
            "higher": True,
        },
        "max_dd_pct": {
            "live": _safe_float(live_metrics.get("max_dd_pct")),
            "bt": _safe_float(bt.get("max_dd_pct")),
            "mc95": _safe_float(mc95.get("max_dd_pct")),
            "mc50": _safe_float(mc50.get("max_dd_pct")),
            "mc95_manipulation": mc95_bundle["mc_manipulation"].get("max_dd_pct"),
            "mc95_retest": mc95_bundle["mc_retest"].get("max_dd_pct"),
            "mc50_manipulation": mc50_bundle["mc_manipulation"].get("max_dd_pct"),
            "mc50_retest": mc50_bundle["mc_retest"].get("max_dd_pct"),
            "dominant_95": mc95_bundle["dominant"].get("max_dd_pct"),
            "dominant_50": mc50_bundle["dominant"].get("max_dd_pct"),
            "spp": _spp_median(reference_data, "median_max_dd_pct"),
            "spp_conf": _spp_confidence(reference_data, "max_dd_pct"),
            "higher": False,
        },
        "max_consec_losses": {
            "live": _safe_int(live_metrics.get("max_consec_losses")),
            "bt": _safe_int(bt.get("max_consec_losses")),
            "mc95": _safe_int(mc95.get("max_consec_losses")),
            "mc50": _safe_int(mc50.get("max_consec_losses")),
            "mc95_manipulation": mc95_bundle["mc_manipulation"].get("max_consec_losses"),
            "mc95_retest": mc95_bundle["mc_retest"].get("max_consec_losses"),
            "mc50_manipulation": mc50_bundle["mc_manipulation"].get("max_consec_losses"),
            "mc50_retest": mc50_bundle["mc_retest"].get("max_consec_losses"),
            "dominant_95": mc95_bundle["dominant"].get("max_consec_losses"),
            "dominant_50": mc50_bundle["dominant"].get("max_consec_losses"),
            "spp": None,
            "spp_conf": None,
            "higher": False,
        },
        "stagnation_days": {
            "live": _safe_float(live_metrics.get("stagnation_days")),
            "bt": _safe_float(bt.get("stagnation_days")),
            "mc95": _safe_float(mc95.get("stagnation_days")),
            "mc50": _safe_float(mc50.get("stagnation_days")),
            "mc95_manipulation": mc95_bundle["mc_manipulation"].get("stagnation_days"),
            "mc95_retest": mc95_bundle["mc_retest"].get("stagnation_days"),
            "mc50_manipulation": mc50_bundle["mc_manipulation"].get("stagnation_days"),
            "mc50_retest": mc50_bundle["mc_retest"].get("stagnation_days"),
            "dominant_95": mc95_bundle["dominant"].get("stagnation_days"),
            "dominant_50": mc50_bundle["dominant"].get("stagnation_days"),
            "spp": _safe_float(_get_reference_value(reference_data, ("spp", "median_stagnation_days"))),
            "spp_conf": _spp_confidence(reference_data, "stagnation"),
            "higher": False,
        },
    }

    metrics_scores = {}
    spp_adjustments = []

    deviation_weights = {
        "win_rate": 0.15,
        "profit_factor": 0.20,
        "expectancy": 0.20,
        "avg_trade": 0.15,
        "payout_ratio": 0.15,
        "ret_dd_ratio": 0.15,
    }
    risk_weights = {
        "max_dd_pct": 0.45,
        "max_consec_losses": 0.30,
        "stagnation_days": 0.25,
    }

    deviation_total = 0.0
    for key, weight in deviation_weights.items():
        spec = metric_sources[key]
        score = _score_metric(spec["live"], spec["mc95"], spec["mc50"], spec["bt"], higher_is_better=spec["higher"])
        if spec["spp"] is not None and spec["spp_conf"] and spec["spp_conf"] > 1.3:
            spp_score = _score_metric(spec["live"], spec["spp"], spec["mc50"], spec["bt"], higher_is_better=spec["higher"])
            score = score * 0.85 + spp_score * 0.15
            spp_adjustments.append(key)
        metrics_scores[key] = {
            "live": spec["live"],
            "bt": spec["bt"],
            "mc95": spec["mc95"],
            "mc50": spec["mc50"],
            "score": round(score, 2),
        }
        deviation_total += score * weight

    risk_total = 0.0
    for key, weight in risk_weights.items():
        spec = metric_sources[key]
        score = _score_metric(spec["live"], spec["mc95"], spec["mc50"], spec["bt"], higher_is_better=spec["higher"])
        if spec["spp"] is not None and spec["spp_conf"] and spec["spp_conf"] > 1.3:
            spp_score = _score_metric(spec["live"], spec["spp"], spec["mc50"], spec["bt"], higher_is_better=spec["higher"])
            score = score * 0.85 + spp_score * 0.15
            spp_adjustments.append(key)
        metrics_scores[key] = {
            "live": spec["live"],
            "bt": spec["bt"],
            "mc95": spec["mc95"],
            "mc50": spec["mc50"],
            "score": round(score, 2),
        }
        risk_total += score * weight

    actual_monthly = _safe_float(live_metrics.get("monthly_frequency"))
    if actual_monthly is None:
        first_dt = _first_trade_date(live_metrics)
        total_trades = _safe_int(live_metrics.get("total_trades"), 0) or 0
        if first_dt:
            days_incubating = max((date.today() - first_dt.date()).days, 1)
            actual_monthly = total_trades / (days_incubating / 30.44)
        else:
            actual_monthly = 0.0

    expected_monthly = calculate_monthly_frequency(
        _safe_int(bt.get("total_trades"), 0) or 0,
        bt.get("bt_period", ""),
    )
    ratio = (actual_monthly / expected_monthly) if expected_monthly > 0 else 0.0
    if 0.5 <= ratio <= 2.0:
        coherence_score = 100.0
    elif 0.25 <= ratio < 0.5 or 2.0 < ratio <= 3.0:
        coherence_score = 50.0
    else:
        coherence_score = 10.0

    total_trades = _safe_int(live_metrics.get("total_trades"), 0) or 0
    if total_trades >= 80:
        sample_score = 100.0
    elif total_trades >= 60:
        sample_score = 80.0
    elif total_trades >= 40:
        sample_score = 60.0
    else:
        sample_score = 40.0

    category_scores = {
        "deviation": {"score": round(deviation_total, 2), "weight": 0.45, "details": {}},
        "risk": {"score": round(risk_total, 2), "weight": 0.30, "details": {}},
        "coherence": {"score": round(coherence_score, 2), "weight": 0.15, "details": {"ratio": round(ratio, 4)}},
        "sample": {"score": round(sample_score, 2), "weight": 0.10},
    }

    final_score = (
        category_scores["deviation"]["score"] * category_scores["deviation"]["weight"]
        + category_scores["risk"]["score"] * category_scores["risk"]["weight"]
        + category_scores["coherence"]["score"] * category_scores["coherence"]["weight"]
        + category_scores["sample"]["score"] * category_scores["sample"]["weight"]
    )

    below_mc95 = []
    for key, spec in metric_sources.items():
        live_value = spec["live"]
        if live_value is None or spec["mc95"] is None or spec["mc50"] is None:
            continue
        if spec["higher"] and live_value < spec["mc95"]:
            below_mc95.append(key)
        if not spec["higher"] and live_value > spec["mc95"]:
            below_mc95.append(key)

    metrics_scores["frequency"] = {
        "live": round(actual_monthly, 4),
        "bt": round(expected_monthly, 4),
        "mc95": None,
        "mc50": None,
        "score": round(coherence_score, 2),
    }

    if final_score >= 65 and not below_mc95:
        verdict = "APROBAR"
    elif final_score >= 45:
        verdict = "OBSERVAR"
    else:
        verdict = "ELIMINAR"

    escalation_from_cp2 = False
    if previous_cp2_result is not None:
        cp2_verdict = (
            previous_cp2_result.get("verdict")
            if isinstance(previous_cp2_result, dict)
            else str(previous_cp2_result)
        )
        if cp2_verdict == "OBSERVAR" and verdict == "OBSERVAR":
            verdict = "ELIMINAR"
            escalation_from_cp2 = True

    return {
        "checkpoint": "CP3",
        "verdict": verdict,
        "score": round(final_score, 2),
        "category_scores": category_scores,
        "metrics_scores": metrics_scores,
        "spp_adjustments": sorted(set(spp_adjustments)),
        "gates": gates,
        "hard_gate_failures": hard_gate_failures,
        "escalation_from_cp2": escalation_from_cp2,
        "mc_source": {
            "has_manipulation": bool(mc_manipulation),
            "has_retest": bool(mc_retest),
            "dominant_metrics": mc95_bundle["dominant"],
            "dominant_metrics_50": mc50_bundle["dominant"],
        },
    }


def evaluate_incubation(ea_name, live_metrics, reference_data, previous_cp2_result=None):
    total_trades = _safe_int(live_metrics.get("total_trades"), 0) or 0
    days_incubating = 0
    first_dt = _first_trade_date(live_metrics)
    if first_dt:
        days_incubating = max((date.today() - first_dt.date()).days, 0)

    checkpoint = get_checkpoint_for_trades(total_trades)

    if checkpoint == "PRE_CP1":
        return {
            "ea_name": ea_name,
            "total_trades": total_trades,
            "days_incubating": days_incubating,
            "current_checkpoint": checkpoint,
            "verdict": "PENDING",
            "score": None,
            "details": {"checkpoint": "PRE_CP1"},
            "hard_gate_failures": [],
            "timestamp": datetime.now().isoformat(),
        }

    if checkpoint == "CP1":
        result = evaluate_cp1(live_metrics, reference_data)
    elif checkpoint == "CP2":
        result = evaluate_cp2(live_metrics, reference_data)
    else:
        result = evaluate_cp3(live_metrics, reference_data, previous_cp2_result=previous_cp2_result)

    hard_gate_failures = result.get("hard_gate_failures", [])

    return {
        "ea_name": ea_name,
        "total_trades": total_trades,
        "days_incubating": days_incubating,
        "current_checkpoint": checkpoint,
        "verdict": result.get("verdict", "PENDING"),
        "score": result.get("score"),
        "details": result,
        "hard_gate_failures": hard_gate_failures,
        "mc_source": result.get("mc_source", {}),
        "timestamp": datetime.now().isoformat(),
    }
