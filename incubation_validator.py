"""
incubation_validator.py
Progressive checkpoint evaluator for Incubation Screening.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime

# SPP adjustment is DISABLED (bounded-correction round, F1). The blend
# semantics are unresolved: `_spp_confidence` passes `spp_median` into
# `_score_metric`'s mc95/worst-case slot, but the blend only ever fires when
# `spp_median > 1.3 * bt` (i.e. the "typical permutation" is >=30% better
# than the original run). For a higher-is-better metric that means the value
# landed in the "worst case" slot is ABOVE the backtest reference, inverting
# the interpolation band and collapsing the score to the bottom branch
# instead of upgrading it. Verified strict downgrade on live fixtures:
# payout_ratio 24.14 -> 23.14, max_dd 38.33 -> 34.46 -- the opposite of
# design §5's "SPP only ever upgrades" premise, which was the whole
# justification for treating SPP as optional. Until the blend itself is
# redesigned (follow-up), the adjustment stays off: `spp_adjustments` is
# always `[]` and no SPP value influences any verdict or score. Display
# fields (`_spp_confidence`, `_spp_median`, `metric_sources["spp"]`) keep
# computing normally so the UI can still show SPP data.
SPP_ADJUSTMENT_ENABLED = False


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
        # B4: int(round(inf)) raises OverflowError (and round(nan) -> ValueError);
        # a non-finite count is not an integer, so fall back to the default
        # rather than letting the exception escape.
        return int(round(float(str(value).replace(",", "."))))
    except (TypeError, ValueError, OverflowError):
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


def _parse_date_only(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _incubation_start_date(reference_data, live_metrics):
    """Resolve the incubation clock start date.

    Priority: entry["date_added"] (stamped at reference save time -- the
    authoritative clock, design C7) -> first trade's close date (legacy
    fallback, used only when date_added is absent/unparseable) -> None.
    A zero-trade EA still ages against the PRE_CP1 frequency deadline as
    long as date_added is present.
    """
    date_added = reference_data.get("date_added") if isinstance(reference_data, dict) else None
    parsed = _parse_date_only(date_added)
    if parsed:
        return parsed

    first_dt = _first_trade_date(live_metrics)
    return first_dt.date() if first_dt else None


def _wins_from_metrics(live_metrics):
    wins = live_metrics.get("winning_trades")
    if wins is not None:
        return _safe_int(wins, 0)

    total = _safe_int(live_metrics.get("total_trades"), 0) or 0
    wr = _safe_float(live_metrics.get("win_rate"), 0.0) or 0.0
    # B5: a non-finite win_rate (NaN/inf) would make int(round(total*wr/100))
    # raise ValueError/OverflowError. Without a wins count to fall back on, the
    # only honest derivation is unavailable -> 0 wins, not a crash.
    if not math.isfinite(wr):
        return 0
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
    """Return the values stored at `confidence_level`, or {} if absent.

    No cross-confidence aliasing: requesting confidence_50 when only
    confidence_95 is populated returns {} (design C2). Missing keys then
    surface through the required-set as explicit mc50.*/mc95.* SIN DATOS
    entries instead of silently borrowing the other level's values.
    """
    if not isinstance(mc_section, dict):
        return {}
    values = mc_section.get(confidence_level, {})
    return values if isinstance(values, dict) else {}


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
    """Exact left-tail binomial CDF: P(X <= wins) for X ~ Binomial(n, p).

    Computed in pure Python via math.comb -- there is no scipy dependency
    and no normal-approximation fallback. This is the single deterministic
    code path (docs/design/decision-engine-no-data-contract.md, C1).
    """
    if n <= 0:
        return 1.0
    n = int(n)
    p = max(0.0, min(1.0, p))
    wins = max(0, min(int(wins), n))

    return sum(
        math.comb(n, k) * (p ** k) * ((1 - p) ** (n - k))
        for k in range(wins + 1)
    )


def trades_to_winrate_significance(wins, n, bt_wr_pct, alpha=0.03, max_extra=500):
    """How many MORE trades until the win-rate binomial gate would fire,
    assuming the current observed win rate continues.

    The gate is the one-sided lower test used in `_hard_gates`: it eliminates
    when the left-tail p-value P(X <= wins | n, bt_wr) drops below `alpha`,
    i.e. when the live win rate is significantly below the backtest rate. This
    projects the CURRENT observed rate forward (future trades win at the same
    observed proportion) and returns the smallest additional-trade count at
    which that p-value first crosses `alpha`.

    It never changes a verdict; it is a "wait vs act now" signal for the trader.

    Returns:
      0        -- already significant at the current sample (the gate fires now)
      int > 0  -- additional trades needed if the current rate holds
      None     -- not applicable: no trades yet, a non-positive backtest rate,
                  an observed rate at or above backtest (a lower-tail kill can
                  never become significant, so no kill is pending), or a
                  crossing that is farther out than `max_extra` (not near).
    """
    n = int(n)
    wins = int(wins)
    if n <= 0 or bt_wr_pct is None:
        return None

    p0 = bt_wr_pct / 100.0
    if p0 <= 0.0:
        return None

    p_hat = wins / n
    # At or above the backtest rate there is no underperformance to detect, so
    # the lower-tail test can never reach significance -- no kill is pending.
    if p_hat >= p0:
        return None

    if _binomial_p_value(wins, n, p0) < alpha:
        return 0

    for extra in range(1, max_extra + 1):
        n_proj = n + extra
        wins_proj = round(p_hat * n_proj)
        if _binomial_p_value(wins_proj, n_proj, p0) < alpha:
            return extra

    return None


_BT_PERIOD_RE = re.compile(
    r"(\d{4})[./-](\d{2})[./-](\d{2})\s*-\s*(\d{4})[./-](\d{2})[./-](\d{2})"
)


def calculate_monthly_frequency(total_trades, bt_period_str):
    """Parse `bt_period_str` ("YYYY.MM.DD - YYYY.MM.DD", also accepting "-"
    and "/" separators) and return the backtest's average trades/month.

    Returns None (never a silent 0.0) when the period is absent or
    unparseable, including invalid calendar dates such as month 13 --
    callers surface that as `backtest.bt_period` in the SIN DATOS missing
    list instead of crashing or silently defaulting (design C10).
    """
    if not bt_period_str:
        return None

    match = _BT_PERIOD_RE.search(str(bt_period_str).strip())
    if not match:
        return None

    y1, m1, d1, y2, m2, d2 = match.groups()
    try:
        start = date(int(y1), int(m1), int(d1))
        end = date(int(y2), int(m2), int(d2))
    except ValueError:
        return None

    days = max((end - start).days, 1)
    months = days / 30.44
    if months <= 0:
        return None
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
    """Compute the CP1 hard gates.

    PRECONDITION: callers (evaluate_cp1/cp2/cp3) MUST run
    `_completeness_missing()` first and only call this once it returns an
    empty list. live dd/mcl/wr, backtest.win_rate, and mc95 dd/mcl are all
    required inputs by that point, so no `or 0` coercion survives on them
    here -- a gap that slips through would fail loudly (design C3/C4).
    """
    total_trades = _safe_int(live_metrics.get("total_trades"), 0) or 0
    wins = _wins_from_metrics(live_metrics)

    live_dd = _safe_float(live_metrics.get("max_dd_pct"))
    live_mcl = _safe_int(live_metrics.get("max_consec_losses"))
    live_wr = _safe_float(live_metrics.get("win_rate"))

    bt_wr = _safe_float(_get_reference_value(reference_data, ("backtest", "win_rate")))
    bt_total = _safe_int(_get_reference_value(reference_data, ("backtest", "total_trades")), 0) or total_trades
    bt_period = _get_reference_value(reference_data, ("backtest", "bt_period"), "")
    bt_monthly = _safe_float(_get_reference_value(reference_data, ("backtest", "monthly_frequency")))
    expected_monthly = bt_monthly if bt_monthly is not None else calculate_monthly_frequency(bt_total, bt_period)

    mc95_bundle = _mc_source_bundle(reference_data, "confidence_95")
    mc95_dd = _safe_float(mc95_bundle["worst"].get("max_dd_pct"))
    mc95_mcl = _safe_int(mc95_bundle["worst"].get("max_consec_losses"))

    dd_threshold = mc95_dd * 1.5
    dd_passed = live_dd <= dd_threshold

    p_value = _binomial_p_value(wins, total_trades, bt_wr / 100.0)
    wr_passed = p_value >= 0.03

    mcl_passed = live_mcl <= mc95_mcl

    actual_monthly = 0.0
    start_date = _incubation_start_date(reference_data, live_metrics)
    if start_date:
        days_incubating = max((date.today() - start_date).days, 1)
        actual_monthly = total_trades / (days_incubating / 30.44)
    else:
        days_incubating = 0

    # Frequency is a WARNING, not a gate: an unparseable/absent BT period
    # surfaces as an explicit SIN DATOS status, never a silent "OK" or a
    # crash on a None comparison.
    # C4: a literal 0.0 expected_monthly is as unusable as None (it makes the
    # ratio undefined / infinite), so it must also declare SIN DATOS rather than
    # silently leaving the frequency warning at "OK".
    freq_warning = "OK"
    if expected_monthly is None or expected_monthly <= 0:
        freq_warning = "SIN DATOS"
    else:
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
            "expected": round(expected_monthly, 4) if expected_monthly is not None else None,
            "actual": round(actual_monthly, 4),
        },
    }


# Per-metric spec used to build the CP2/CP3 required-set: (output_key,
# live_key, mc_key, bt_key). live_key is the field read from live_metrics;
# mc_key is the key used inside the MC worst-case bundle; bt_key is the
# backtest field name (CP3 only -- CP2 does not require per-metric bt.<k>).
_CP2_METRIC_KEYS = [
    ("win_rate", "win_rate", "win_rate"),
    ("profit_factor", "profit_factor", "profit_factor"),
    ("expectancy", "expectancy", "expectancy"),
    ("max_dd_pct", "max_dd_pct", "max_dd_pct"),
    ("max_consec_losses", "max_consec_losses", "max_consec_losses"),
    ("payout_ratio", "payout_ratio", "payout_ratio"),
    ("avg_trade", "expectancy", "avg_trade"),
]

_CP3_METRIC_KEYS = [
    ("win_rate", "win_rate", "win_rate", "win_rate"),
    ("profit_factor", "profit_factor", "profit_factor", "profit_factor"),
    ("expectancy", "expectancy", "expectancy", "expectancy"),
    ("avg_trade", "expectancy", "avg_trade", "expectancy"),
    ("payout_ratio", "payout_ratio", "payout_ratio", "payout_ratio"),
    ("ret_dd_ratio", "ret_dd", "ret_dd_ratio", "ret_dd_ratio"),
    ("max_dd_pct", "max_dd_pct", "max_dd_pct", "max_dd_pct"),
    ("max_consec_losses", "max_consec_losses", "max_consec_losses", "max_consec_losses"),
    ("stagnation_days", "stagnation_days", "stagnation_days", "stagnation_days"),
]

# Fields whose downstream consumer parses with `_safe_int` instead of
# `_safe_float` (design F7 correction). The completeness gate must use the
# same parser as the consumer for that field, or a value like "∞" can pass
# the gate (accepted by `_safe_float`) and still come back None from the
# consumer's `_safe_int`, crashing a later `<=` comparison against None.
_INT_PARSED_KEYS = {"max_consec_losses"}


def _effective_ret_dd(live_metrics):
    """Resolve the live ret/dd ratio, treating a zero-drawdown EA as
    mathematically infinite rather than missing (design F3 correction).

    `metrics.py` sets `ret_dd = None` whenever `max_dd_dollar <= 0` (the
    division is undefined), which is correct for the raw ratio but wrong for
    the required-set gate: a EA with completed trades and literally zero
    drawdown has an infinite ret/dd ratio -- the best possible outcome, not
    an absence of data. `_score_metric` already treats `inf >= bt` as a
    perfect (100) score, so mapping this case to `float("inf")` is
    consistent with the existing `_safe_float` "∞" -> sentinel precedent
    used elsewhere. If `ret_dd` is `None` for any OTHER reason (no trades,
    non-zero drawdown but still missing), it stays missing -> SIN DATOS.
    """
    raw = live_metrics.get("ret_dd")
    if raw is not None:
        return _safe_float(raw)
    max_dd_pct = _safe_float(live_metrics.get("max_dd_pct"))
    total_trades = _safe_int(live_metrics.get("total_trades"), 0) or 0
    if max_dd_pct == 0 and total_trades > 0:
        return float("inf")
    return None


def _completeness_value(container, key):
    """Parse `container[key]` with the same parser its downstream consumer
    uses (design F7)."""
    if key in _INT_PARSED_KEYS:
        return _safe_int(container.get(key))
    return _safe_float(container.get(key))


def _completeness_live_value(live_metrics, live_key):
    """Parse a live-side required value the same way its checkpoint
    evaluation consumes it (design F3 + F7)."""
    if live_key == "ret_dd":
        return _effective_ret_dd(live_metrics)
    return _completeness_value(live_metrics, live_key)


def _sd_result(checkpoint, missing):
    """Build the SIN DATOS shape shared by CP1/CP2/CP3 (design §1)."""
    return {
        "checkpoint": checkpoint,
        "verdict": "SIN DATOS",
        "score": None,
        "sin_datos": True,
        "missing": missing,
        "gates": {},
        "hard_gate_failures": [],
        "metrics_evaluation": {},
        "metrics_scores": {},
        "category_scores": {},
        "spp_adjustments": [],
        "escalation_from_cp2": False,
        "mc_source": {"has_manipulation": False, "has_retest": False, "dominant_metrics": {}},
    }


def _completeness_missing(checkpoint, live_metrics, reference_data):
    """Collect missing required inputs for `checkpoint` ("CP1"/"CP2"/"CP3").

    Order: live -> backtest -> mc95 -> mc50 -> spp -> start_date (stable,
    deterministic -- docs/design/decision-engine-no-data-contract.md §2/§3).
    A field is required iff its absence would otherwise alter the verdict
    through a silent default.
    """
    missing = []

    def _add(name):
        if name not in missing:
            missing.append(name)

    # CP1 hard-gate required set: backtest.win_rate; mc95.max_dd_pct;
    # mc95.max_consec_losses; live dd/mcl/wr/wins (total_trades covers the
    # "wins" derivation when winning_trades isn't tracked separately).
    if _safe_float(live_metrics.get("max_dd_pct")) is None:
        _add("live.max_dd_pct")
    if _completeness_value(live_metrics, "max_consec_losses") is None:
        _add("live.max_consec_losses")
    if _safe_float(live_metrics.get("win_rate")) is None:
        _add("live.win_rate")
    if _safe_float(live_metrics.get("total_trades")) is None:
        _add("live.total_trades")
    if _safe_float(_get_reference_value(reference_data, ("backtest", "win_rate"))) is None:
        _add("backtest.win_rate")

    mc95_bundle = _mc_source_bundle(reference_data, "confidence_95")
    if _safe_float(mc95_bundle["worst"].get("max_dd_pct")) is None:
        _add("mc95.max_dd_pct")
    if _completeness_value(mc95_bundle["worst"], "max_consec_losses") is None:
        _add("mc95.max_consec_losses")

    if checkpoint == "CP1":
        return missing

    # CP2 (and CP3, which extends it): per-metric mc95.<k> AND mc50.<k> for
    # the 7 metrics + the corresponding live values. MC50 sections are
    # form-optional but verdict-mandatory: absent MC50 -> SIN DATOS naming
    # the mc50.* keys.
    mc50_bundle = _mc_source_bundle(reference_data, "confidence_50")
    for _output_key, live_key, mc_key in _CP2_METRIC_KEYS:
        if _completeness_live_value(live_metrics, live_key) is None:
            _add(f"live.{live_key}")
        if _completeness_value(mc95_bundle["worst"], mc_key) is None:
            _add(f"mc95.{mc_key}")
        if _completeness_value(mc50_bundle["worst"], mc_key) is None:
            _add(f"mc50.{mc_key}")

    if checkpoint == "CP2":
        return missing

    # CP3: the CP2 set extended to the 9 scored metrics, + bt.<k> per
    # metric, + coherence inputs (backtest.total_trades, parseable bt_period)
    # -- coherence carries 15% weight, so it cannot silently score 10.
    bt = reference_data.get("backtest", {}) if isinstance(reference_data, dict) else {}
    for _output_key, live_key, mc_key, bt_key in _CP3_METRIC_KEYS:
        if _completeness_live_value(live_metrics, live_key) is None:
            _add(f"live.{live_key}")
        if _completeness_value(mc95_bundle["worst"], mc_key) is None:
            _add(f"mc95.{mc_key}")
        if _completeness_value(mc50_bundle["worst"], mc_key) is None:
            _add(f"mc50.{mc_key}")
        if _completeness_value(bt, bt_key) is None:
            _add(f"backtest.{bt_key}")

    bt_total = _safe_int(bt.get("total_trades"))
    if bt_total is None:
        _add("backtest.total_trades")
    elif calculate_monthly_frequency(bt_total, bt.get("bt_period", "")) is None:
        _add("backtest.bt_period")

    return missing


def evaluate_cp1(live_metrics, reference_data):
    missing = _completeness_missing("CP1", live_metrics, reference_data)
    if missing:
        return _sd_result("CP1", missing)

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


# SPP median keys and their corresponding original-backtest field, per
# output_key. Metrics absent from this map (win_rate, profit_factor,
# max_consec_losses) have no SPP median tracked in the reference form, so
# their confidence is always None -- consistent with the pre-existing form
# schema (INCUBATION_REFERENCE_SECTIONS in incubation_domain.py).
_SPP_MEDIAN_KEY_MAP = {
    "max_dd_pct": "median_max_dd_pct",
    "payout_ratio": "median_payout_ratio",
    "avg_trade": "median_avg_trade",
    "expectancy": "median_avg_trade",
    "ret_dd_ratio": "median_ret_dd_ratio",
    "stagnation_days": "median_stagnation_days",
}

_SPP_ORIGINAL_KEY_MAP = {
    "max_dd_pct": "max_dd_pct",
    "payout_ratio": "payout_ratio",
    "avg_trade": "expectancy",
    "expectancy": "expectancy",
    "ret_dd_ratio": "ret_dd_ratio",
    "stagnation_days": "stagnation_days",
}


def _spp_confidence(reference_data, metric_key, higher_is_better=True):
    """Confidence ratio between the SPP median permutation and the
    original backtest value for `metric_key`.

    conf > 1.3 means the typical permutation performs >=30% better than the
    original run (docs/decision-logic.md). Orientation is direction-aware
    so the single documented threshold survives regardless of metric type:
    - higher-is-better metrics: conf = median / original
    - lower-is-better metrics:  conf = original / median
    (design §5 -- this revives the CP2 rescue / CP3 blend, dead until now.)
    """
    spp_key = _SPP_MEDIAN_KEY_MAP.get(metric_key)
    if not spp_key:
        return None
    median = _spp_median(reference_data, spp_key)
    # Defense in depth (F1 correction): guard the zero-median division even
    # though the adjustment itself is currently disabled -- this function
    # must not crash if the adjustment is ever re-enabled.
    if median is None or median == 0:
        return None
    bt = reference_data.get("backtest", {}) if isinstance(reference_data, dict) else {}
    original = _safe_float(bt.get(_SPP_ORIGINAL_KEY_MAP.get(metric_key, metric_key)))
    if original is None or original == 0:
        return None
    return (median / original) if higher_is_better else (original / median)


def _spp_median(reference_data, key):
    spp = reference_data.get("spp", {}) if isinstance(reference_data, dict) else {}
    return _safe_float(spp.get(key))


def evaluate_cp2(live_metrics, reference_data):
    missing = _completeness_missing("CP2", live_metrics, reference_data)
    if missing:
        return _sd_result("CP2", missing)

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
        # No 0.0 coercion (design C6): the completeness gate above already
        # guarantees every one of these live values is present.
        live_value = _safe_float(live_metrics.get(live_key))

        mc50 = _safe_float(mc50_bundle["worst"].get(mc_key))
        mc95 = _safe_float(mc95_bundle["worst"].get(mc_key))
        mc50_manip = mc50_bundle["mc_manipulation"].get(mc_key)
        mc50_retest = mc50_bundle["mc_retest"].get(mc_key)
        mc95_manip = mc95_bundle["mc_manipulation"].get(mc_key)
        mc95_retest = mc95_bundle["mc_retest"].get(mc_key)
        spp_key = _SPP_MEDIAN_KEY_MAP.get(output_key)
        spp_median = _spp_median(reference_data, spp_key) if spp_key else None

        status = _metric_status(live_value, mc50, mc95, higher_is_better=higher_is_better)
        spp_conf = None

        if spp_median is not None:
            spp_conf = _spp_confidence(reference_data, output_key, higher_is_better=higher_is_better)
            if SPP_ADJUSTMENT_ENABLED and spp_conf and spp_conf > 1.3 and status == "failing":
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
    """Piecewise interpolation between MC95/MC50/BT reference bands.

    PRECONDITION: the CP3 completeness gate guarantees non-None live/mc95/
    mc50/bt inputs for every metric this is called with. No `or 0.0`
    coercion survives here (design C5) -- a None slipping through raises a
    TypeError instead of silently scoring 100 (the old `0 >= 0` bug for an
    all-missing reference).
    """
    live_value = _safe_float(live_value)
    mc95_value = _safe_float(mc95_value)
    mc50_value = _safe_float(mc50_value)
    bt_value = _safe_float(bt_value)

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


def _resolve_cp3_verdict(score, below_mc95, cp2_verdict=None):
    """
    Resolve the final CP3 verdict and the CP2->CP3 escalation flag.

    score: the CP3 final_score (float).
    below_mc95: list of metric keys currently below the MC95 threshold. A
        non-empty list blocks APROBAR even when score >= 65 (the gate that
        AGENTS.md's summary omits).
    cp2_verdict: the previous CP2 checkpoint verdict string, or None if no
        prior CP2 result is available.

    Boundaries (score, escalation aside): 65.0 -> APROBAR, 64.99 -> OBSERVAR,
    45.0 -> OBSERVAR, 44.99 -> ELIMINAR.
    """
    if score >= 65 and not below_mc95:
        verdict = "APROBAR"
    elif score >= 45:
        verdict = "OBSERVAR"
    else:
        verdict = "ELIMINAR"

    escalation_from_cp2 = False
    if cp2_verdict == "OBSERVAR" and verdict == "OBSERVAR":
        verdict = "ELIMINAR"
        escalation_from_cp2 = True

    return verdict, escalation_from_cp2


def evaluate_cp3(live_metrics, reference_data, previous_cp2_result=None):
    missing = _completeness_missing("CP3", live_metrics, reference_data)
    if missing:
        return _sd_result("CP3", missing)

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
            "spp_conf": _spp_confidence(reference_data, "win_rate", higher_is_better=True),
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
            "spp_conf": _spp_confidence(reference_data, "profit_factor", higher_is_better=True),
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
            "spp_conf": _spp_confidence(reference_data, "expectancy", higher_is_better=True),
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
            "spp_conf": _spp_confidence(reference_data, "avg_trade", higher_is_better=True),
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
            "spp_conf": _spp_confidence(reference_data, "payout_ratio", higher_is_better=True),
            "higher": True,
        },
        "ret_dd_ratio": {
            # Zero-drawdown EAs are treated as an infinite (maximal) ratio,
            # not missing data (design F3 correction) -- see
            # `_effective_ret_dd`.
            "live": _effective_ret_dd(live_metrics),
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
            "spp_conf": _spp_confidence(reference_data, "ret_dd_ratio", higher_is_better=True),
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
            "spp_conf": _spp_confidence(reference_data, "max_dd_pct", higher_is_better=False),
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
            "spp_conf": _spp_confidence(reference_data, "stagnation_days", higher_is_better=False),
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
        if SPP_ADJUSTMENT_ENABLED and spec["spp"] is not None and spec["spp_conf"] and spec["spp_conf"] > 1.3:
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
        if SPP_ADJUSTMENT_ENABLED and spec["spp"] is not None and spec["spp_conf"] and spec["spp_conf"] > 1.3:
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
        total_trades_for_freq = _safe_int(live_metrics.get("total_trades"), 0) or 0
        start_date = _incubation_start_date(reference_data, live_metrics)
        if start_date:
            days_incubating_freq = max((date.today() - start_date).days, 1)
            actual_monthly = total_trades_for_freq / (days_incubating_freq / 30.44)
        else:
            actual_monthly = 0.0

    expected_monthly = calculate_monthly_frequency(
        _safe_int(bt.get("total_trades"), 0) or 0,
        bt.get("bt_period", ""),
    )
    ratio = (actual_monthly / expected_monthly) if expected_monthly and expected_monthly > 0 else 0.0
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

    # Detect metrics below MC95 threshold.
    # "avg_trade" and "expectancy" share the same live source — deduplicate by
    # skipping "avg_trade" here to avoid counting the same value twice.
    # NOTE (design C8): the blocker only needs live + mc95 -- mc50 is NOT
    # required here (it is still required upstream by the completeness
    # gate for the score itself, but the below_mc95 gate must not be
    # skipped just because mc50 happens to be absent for one metric).
    below_mc95 = []
    for key, spec in metric_sources.items():
        if key == "avg_trade":
            continue  # same live value as "expectancy", would double-count
        live_value = spec["live"]
        if live_value is None or spec["mc95"] is None:
            continue
        if spec["higher"] and live_value < spec["mc95"]:
            below_mc95.append(key)
        if not spec["higher"] and live_value > spec["mc95"]:
            below_mc95.append(key)

    metrics_scores["frequency"] = {
        "live": round(actual_monthly, 4),
        "bt": round(expected_monthly, 4) if expected_monthly is not None else None,
        "mc95": None,
        "mc50": None,
        "score": round(coherence_score, 2),
    }

    cp2_verdict = None
    if previous_cp2_result is not None:
        cp2_verdict = (
            previous_cp2_result.get("verdict")
            if isinstance(previous_cp2_result, dict)
            else str(previous_cp2_result)
        )

    # Canonizar sobre el valor publicado: redondear una sola vez y decidir el
    # veredicto con el MISMO score que se muestra, para que un 65.0 en pantalla
    # no pueda convivir con un OBSERVAR decidido sobre un 64.998 crudo
    # (known-issues 14-A1).
    published_score = round(final_score, 2)
    verdict, escalation_from_cp2 = _resolve_cp3_verdict(published_score, below_mc95, cp2_verdict)

    return {
        "checkpoint": "CP3",
        "verdict": verdict,
        "score": published_score,
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
    start_date = _incubation_start_date(reference_data, live_metrics)
    if start_date:
        days_incubating = max((date.today() - start_date).days, 0)

    checkpoint = get_checkpoint_for_trades(total_trades)

    if checkpoint == "PRE_CP1":
        # Frequency-based deadline: if BT tells us X trades/month, reaching 5 trades
        # should take ~(5 / bt_monthly * 30.44) days. Give 3× tolerance.
        # If that deadline passes with < 5 trades → frequency/edge is lost → ELIMINAR.
        # The clock is `date_added` (design C7), not first-trade date, so a
        # zero-trade EA ages too instead of staying PENDING forever.
        bt_total = _safe_int(_get_reference_value(reference_data, ("backtest", "total_trades")))
        bt_period = _get_reference_value(reference_data, ("backtest", "bt_period"), "")
        bt_monthly = _safe_float(_get_reference_value(reference_data, ("backtest", "monthly_frequency")))
        if bt_monthly is None:
            bt_monthly = calculate_monthly_frequency(bt_total or 0, bt_period)

        missing = []
        if bt_total is None:
            missing.append("backtest.total_trades")
        if bt_monthly is None or bt_monthly <= 0:
            missing.append("backtest.bt_period")

        if missing:
            return {
                "ea_name": ea_name,
                "total_trades": total_trades,
                "days_incubating": days_incubating,
                "current_checkpoint": checkpoint,
                "verdict": "SIN DATOS",
                "score": None,
                "sin_datos": True,
                "missing": missing,
                "details": {
                    "checkpoint": "PRE_CP1",
                    "missing": missing,
                },
                "hard_gate_failures": [],
                "mc_source": {},
                "timestamp": datetime.now().isoformat(),
            }

        days_expected_for_5 = (5.0 / bt_monthly) * 30.44
        freq_deadline_days = round(days_expected_for_5 * 3)
        actual_monthly = 0.0
        if days_incubating > 0:
            actual_monthly = round(total_trades / (days_incubating / 30.44), 2)

        deadline_exceeded = days_incubating >= freq_deadline_days

        if deadline_exceeded:
            return {
                "ea_name": ea_name,
                "total_trades": total_trades,
                "days_incubating": days_incubating,
                "current_checkpoint": checkpoint,
                "verdict": "ELIMINAR",
                "score": None,
                "sin_datos": False,
                "missing": [],
                "details": {
                    "checkpoint": "PRE_CP1",
                    "freq_deadline": True,
                    "deadline_days": freq_deadline_days,
                    "bt_monthly": round(bt_monthly, 2),
                    "actual_monthly": actual_monthly,
                },
                "hard_gate_failures": ["freq_deadline"],
                # C8: carry mc_source so every PRE_CP1 branch is shape-compatible
                # with the SIN DATOS branch (which already emits "mc_source": {}).
                "mc_source": {},
                "timestamp": datetime.now().isoformat(),
            }

        return {
            "ea_name": ea_name,
            "total_trades": total_trades,
            "days_incubating": days_incubating,
            "current_checkpoint": checkpoint,
            "verdict": "PENDING",
            "score": None,
            "sin_datos": False,
            "missing": [],
            "details": {
                "checkpoint": "PRE_CP1",
                "freq_deadline": False,
                "deadline_days": freq_deadline_days,
                "bt_monthly": round(bt_monthly, 2),
                "actual_monthly": actual_monthly,
            },
            "hard_gate_failures": [],
            # C8: shape-compatible with the other PRE_CP1 branches.
            "mc_source": {},
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
        "sin_datos": result.get("sin_datos", False),
        "missing": result.get("missing", []),
        "details": result,
        "hard_gate_failures": hard_gate_failures,
        "mc_source": result.get("mc_source", {}),
        "timestamp": datetime.now().isoformat(),
    }
