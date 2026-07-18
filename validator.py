"""
validator.py - EA Validator: Sistema de Scoring Ponderado con Umbrales Dinámicos
Implementación Python del EA_Validator_Final_v2.xlsx
Compara resultados LIVE (MT5) vs Backtest (SQX/manual) por magic number.
"""

import math
import os

from local_json import load_local_json, save_local_json
from metrics import calculate_ea_metrics
from trade_matching import trade_matches_ea

APP_DIR = os.path.dirname(os.path.abspath(__file__))
VALIDATOR_STORE_PATH = os.path.join(APP_DIR, "validator_store.json")

# ─── Config de pesos y umbrales (igual que la hoja Config del Excel) ─────────
CONFIG = {
    "pts_ok": 10,
    "pts_alerta": 5,
    "pts_fuera": 0,
    # Pesos categorías (suman 100)
    "w_riesgo": 35,
    "w_edge": 30,
    "w_caracter": 15,
    "w_desv": 20,
    # Sub-pesos RIESGO (suman 100)
    "w_dd_escalado": 50,
    "w_consec_losses": 30,
    "w_stagnation": 20,
    # Sub-pesos EDGE (suman 100)
    "w_win_rate": 25,
    "w_profit_factor": 30,
    "w_payout_ratio": 20,
    "w_edge_erosion": 25,
    # Sub-pesos CARACTER (suman 100)
    "w_frecuencia": 55,
    "w_avg_bars": 45,
    # Umbrales de veredicto
    "thresh_continuar": 70,
    "thresh_monitorear": 45,
    # Threshold desviación estructural
    "thresh_desv": 3,
    # Umbral de datos mínimos para emitir veredicto
    # - trades >= min_trades                        → evaluar (sin importar semanas)
    # - trades < min_trades Y sem < min_weeks       → SIN DATOS (demasiado temprano)
    # - trades < min_trades Y sem >= min_weeks      → ELIMINAR (perdió frecuencia/edge)
    "min_trades": 5,
    "min_weeks": 8,
}

# §5: below half a month, the "worst DD in one month" reference and the monthly
# trade-frequency reference are meaningless — a backtest spanning less than this
# cannot ground a per-month rate, so scaling a DD limit off it (or a frequency
# ratio) produces a confident-but-fictional number. Below this floor the
# reference is declared degenerate (SIN DATOS) instead of scaled.
BT_MONTHS_SANITY_FLOOR = 0.5


def _pts(estado: str) -> int:
    if estado == "OK":
        return CONFIG["pts_ok"]
    elif estado == "ALERTA":
        return CONFIG["pts_alerta"]
    return CONFIG["pts_fuera"]


def _band_verdict(score):
    """The score-driven verdict, ignoring the DD/PF overrides."""
    if score >= CONFIG["thresh_continuar"]:
        return "CONTINUAR"
    if score >= CONFIG["thresh_monitorear"]:
        return "MONITOREAR"
    return "ELIMINAR"


def score_verdict_is_weight_sensitive(s_riesgo, s_edge, s_caracter, s_desv, rel=0.2):
    """True if the score-driven verdict would change under a +/-`rel` shift of
    any single category weight.

    The category weights (35/30/15/20) are a port of a spreadsheet with no
    empirical grounding, so a verdict that rests on their exact values is a
    coin-toss dressed as a precise score. Each weight is scaled by a factor in
    {1-rel, 1, 1+rel}; the set is renormalized to its original total so the
    score keeps its 0-100 scale; the banded verdict is compared to the
    unperturbed one. Read-only -- never changes a verdict.
    """
    subs = (s_riesgo, s_edge, s_caracter, s_desv)
    if any(s is None for s in subs):
        return False

    base_weights = (
        CONFIG["w_riesgo"],
        CONFIG["w_edge"],
        CONFIG["w_caracter"],
        CONFIG["w_desv"],
    )
    total = sum(base_weights)

    def verdict_for(weights):
        wsum = sum(weights)
        if wsum <= 0:
            return None
        norm = total / wsum  # renormalize so the score stays on the 0-100 scale
        score = sum(w * norm * s for w, s in zip(weights, subs)) / 10.0
        return _band_verdict(round(score, 1))

    base_verdict = verdict_for(base_weights)
    factors = (1.0 - rel, 1.0, 1.0 + rel)
    for f0 in factors:
        for f1 in factors:
            for f2 in factors:
                for f3 in factors:
                    weights = (
                        base_weights[0] * f0,
                        base_weights[1] * f1,
                        base_weights[2] * f2,
                        base_weights[3] * f3,
                    )
                    if verdict_for(weights) != base_verdict:
                        return True
    return False


def verdict_weight_sensitive(analysis):
    """Read-only honesty flag for a validator result: True when the score-driven
    CONTINUAR/MONITOREAR/ELIMINAR verdict would flip under a +/-20% shift of any
    category weight. False for SIN DATOS, missing sub-scores, or a verdict forced
    by the DD/PF overrides (which do not depend on the weights)."""
    if not analysis or analysis.get("sin_datos"):
        return False
    score = analysis.get("score")
    subs = [analysis.get(k) for k in ("s_riesgo", "s_edge", "s_caracter", "s_desv")]
    if score is None or any(s is None for s in subs):
        return False
    # Only meaningful when the score actually drove the verdict, not an override.
    if analysis.get("veredicto") != _band_verdict(round(score, 1)):
        return False
    return score_verdict_is_weight_sensitive(*subs)


def load_validator_store() -> dict:
    return load_local_json(VALIDATOR_STORE_PATH, {})


def save_validator_store(store: dict):
    save_local_json(VALIDATOR_STORE_PATH, store)


def timeframe_to_hours(tf: str) -> float:
    """Convierte timeframe a horas para calcular avg_bars desde duration_hours."""
    mapping = {
        "M1": 1 / 60,
        "M5": 5 / 60,
        "M15": 15 / 60,
        "M30": 30 / 60,
        "H1": 1.0,
        "H4": 4.0,
        "D1": 24.0,
        "W1": 168.0,
    }
    return mapping.get(str(tf).upper().strip(), 1.0)


def calculate_validator_score(
    bt: dict, mc_retest: dict, mc_trades: dict, spp: dict, live: dict
) -> dict:
    """
    Calcula el score completo del validador para un EA.

    bt: datos backtest original (del formulario)
    mc_retest: Monte Carlo Retest 95%
    mc_trades: Monte Carlo Trades Manipulation 95%
    spp: System Parameter Permutation (medianas)
    live: datos live extraídos de MT5 (calculate_ea_metrics)

    Returns dict completo con todos los cálculos intermedios y resultado final.
    """
    # ── Extraer live data ──────────────────────────────────────────────────
    # B1/B2: total_trades is a COUNT — coerce to a FINITE float or None. Unlike
    # _safe_float (which maps ±inf to ±1e9 so an ∞ payout still scores), an
    # infinite/NaN/non-numeric trade count is meaningless and becomes None ->
    # SIN DATOS (guarded below), so int(trades_live) never sees a non-finite.
    trades_live = _finite_or_none(live.get("total_trades"))
    weeks_live = _safe_float(live.get("weeks_operating"))
    if weeks_live is None:
        weeks_live = 0.0
    wr_live = _safe_float(live.get("win_rate"))
    pf_live = _safe_float(live.get("profit_factor"))
    payout_live = _safe_float(live.get("payout_ratio"))
    expect_live = _safe_float(live.get("expectancy"))
    max_dd_live = _safe_float(live.get("max_dd_pct"))
    avg_bars_live = _safe_float(
        live.get("avg_bars_live")
    )  # calculado desde duration_hours/tf
    consec_losses_live = _safe_float(live.get("max_consec_losses"))
    stagnation_live = _safe_float(live.get("stagnation_days"))

    # ── Extraer BT data ────────────────────────────────────────────────────
    bt_wr = _safe_float(bt.get("win_rate"))
    bt_pf = _safe_float(bt.get("profit_factor"))
    bt_payout = _safe_float(bt.get("payout_ratio"))
    bt_expect = _safe_float(bt.get("expectancy"))
    bt_avg_bars = _safe_float(bt.get("avg_bars"))
    bt_max_dd = _safe_float(bt.get("max_dd_pct"))
    bt_max_consec = _safe_float(bt.get("max_consec_losses"))
    bt_trades = _safe_float(bt.get("trades_total"))
    bt_months = _safe_float(bt.get("months"))
    bt_worst_dd_1m = _safe_float(bt.get("worst_dd_1m"))
    bt_worst_dd_3m = _safe_float(bt.get("worst_dd_3m"))
    bt_stagnation = _safe_float(bt.get("stagnation_days"))

    mc_r_dd = _safe_float(mc_retest.get("max_dd"))
    mc_t_dd = _safe_float(mc_trades.get("max_dd"))

    spp_expect_median = _safe_float(spp.get("expectancy_median"))

    result = {}

    # ── Significancia ──────────────────────────────────────────────────────
    # trades_live is None only for a missing/non-finite count; tl=0 is a safe
    # placeholder because the guard right after _nd_result returns SIN DATOS.
    tl = int(trades_live) if trades_live is not None else 0
    if tl >= 100:
        signif = "Alta"
    elif tl >= 50:
        signif = "Media"
    elif tl >= 30:
        signif = "Baja"
    else:
        signif = "Muy baja"
    result["signif"] = signif
    result["trades_live"] = tl
    result["weeks_live"] = round(weeks_live, 1)

    # ── Guard: datos mínimos para emitir veredicto ─────────────────────────
    # Lógica:
    #   trades >= min_trades            → evaluar normalmente (sin importar semanas)
    #   trades < min_trades Y sem < max_wait_weeks → SIN DATOS (demasiado temprano)
    #   trades < min_trades Y sem >= max_wait_weeks → ELIMINAR (2 meses sin actividad = perdió EDGE)
    min_trades = CONFIG["min_trades"]
    max_wait_weeks = CONFIG["min_weeks"]

    def _nd_result(veredicto, accion, missing=None):
        result["veredicto"] = veredicto
        result["accion"] = accion
        result["sin_datos"] = True
        result["score"] = None
        result["desv_flag"] = "-"
        result["missing"] = missing or []
        # C5/§6: HARD-set every derived numeric to None (not setdefault). When
        # the second completeness gate reaches SIN DATOS after some metrics were
        # already computed, setdefault would leave confident numbers (wr_delta,
        # payout_var, bars_var, edge_erosion, freq_pct, the raw live/bt echoes,
        # dd_limit...) sitting next to "N/D" estados — the exact silent,
        # self-contradicting number the SIN DATOS contract exists to prevent
        # (docs/design/decision-engine-no-data-contract.md). Blank them all,
        # exactly like dd_limit used to be blanked on its own.
        for key in (
            "wr_delta", "wr_live", "wr_bt", "wr_estado",
            "pf_live", "pf_bt", "pf_estado",
            "payout_var", "payout_live", "payout_bt", "payout_estado",
            "dd_live", "dd_limit", "dd_method", "dd_estado",
            "consec_ratio", "consec_estado",
            "bars_var", "avg_bars_live", "avg_bars_bt", "bars_estado",
            "freq_pct", "freq_estado",
            "edge_erosion", "expect_live", "spp_expect_median", "edge_estado",
            "stagn_live", "stagn_bt", "stagn_label", "stagn_estado",
            "s_riesgo", "s_edge", "s_caracter", "s_desv", "detcount",
            "live_vs_bt_profit_ratio", "live_vs_bt_profit_status",
        ):
            result[key] = None
        for key in (
            "wr_estado", "pf_estado", "payout_estado", "dd_estado",
            "consec_estado", "bars_estado", "freq_estado", "edge_estado",
            "stagn_estado", "stagn_label", "dd_method", "consec_ratio",
        ):
            result[key] = "N/D"
        result["live_vs_bt_profit_status"] = "N/D"
        return result

    if trades_live is None:
        # B1/B2: a missing/non-numeric/non-finite live trade count cannot ground
        # any verdict — declare it SIN DATOS naming the field.
        return _nd_result(
            "SIN DATOS",
            "Falta el conteo de trades live (live.total_trades ausente o no finito).",
            missing=["live.total_trades"],
        )

    if tl < min_trades:
        if weeks_live < max_wait_weeks:
            falta_trades = min_trades - tl
            falta_semanas = round(max_wait_weeks - weeks_live, 1)
            return _nd_result(
                "SIN DATOS",
                f"Solo {tl} trade(s). Faltan {falta_trades} más (mín {min_trades}) "
                f"o esperar hasta sem {max_wait_weeks:.0f}.",
            )
        else:
            # Pasaron 8+ semanas y no llegó a 5 trades: perdió frecuencia/edge
            return _nd_result(
                "ELIMINAR",
                f"Solo {tl} trade(s) en {weeks_live:.1f} sem — "
                f"frecuencia insuficiente vs BT tras {max_wait_weeks:.0f} sem.",
            )

    # ── Completeness gate: SIN DATOS contract ──────────────────────────────
    # Missing required reference/live data must never produce a confident
    # verdict from a silent default
    # (docs/design/decision-engine-no-data-contract.md §1/§2).
    missing = []
    if wr_live is None:
        missing.append("live.win_rate")
    if pf_live is None:
        missing.append("live.profit_factor")
    if payout_live is None:
        missing.append("live.payout_ratio")
    if expect_live is None:
        missing.append("live.expectancy")
    if max_dd_live is None:
        missing.append("live.max_dd_pct")
    if consec_losses_live is None:
        missing.append("live.max_consec_losses")
    if stagnation_live is None:
        missing.append("live.stagnation_days")
    if avg_bars_live is None:
        missing.append("live.avg_bars_live")
    if bt_wr is None:
        missing.append("bt.win_rate")
    if bt_pf is None:
        missing.append("bt.profit_factor")
    if bt_payout is None:
        missing.append("bt.payout_ratio")
    if bt_avg_bars is None:
        missing.append("bt.avg_bars")
    if bt_max_consec is None:
        missing.append("bt.max_consec_losses")
    if bt_trades is None:
        missing.append("bt.trades_total")
    if bt_months is None:
        missing.append("bt.months")
    # DD reference: either the BT worst-1m-DD path or BOTH MC DD values.
    if bt_worst_dd_1m is None and not (mc_r_dd is not None and mc_t_dd is not None):
        if bt_worst_dd_1m is None:
            missing.append("bt.worst_dd_1m")
        if mc_r_dd is None:
            missing.append("mc_retest.max_dd")
        if mc_t_dd is None:
            missing.append("mc_trades.max_dd")
    if spp_expect_median is None:
        missing.append("spp.expectancy_median")

    if missing:
        return _nd_result(
            "SIN DATOS",
            "Completar datos de referencia: " + ", ".join(missing),
            missing=missing,
        )

    # ── Win Rate ───────────────────────────────────────────────────────────
    if wr_live is not None and bt_wr is not None:
        wr_delta = wr_live - bt_wr
        abs_d = abs(wr_delta)
        if tl < 30:
            wr_estado = "OK" if abs_d <= 15 else ("ALERTA" if abs_d <= 20 else "FUERA")
        elif tl < 50:
            wr_estado = "OK" if abs_d <= 10 else ("ALERTA" if abs_d <= 15 else "FUERA")
        elif tl < 100:
            wr_estado = "OK" if abs_d <= 7 else ("ALERTA" if abs_d <= 12 else "FUERA")
        else:
            wr_estado = "OK" if abs_d <= 5 else ("ALERTA" if abs_d <= 10 else "FUERA")
    else:
        wr_delta = None
        wr_estado = "N/D"
    result["wr_delta"] = round(wr_delta, 2) if wr_delta is not None else None
    result["wr_live"] = wr_live
    result["wr_bt"] = bt_wr
    result["wr_estado"] = wr_estado

    # ── Profit Factor ──────────────────────────────────────────────────────
    if pf_live is not None:
        if tl < 30:
            pf_estado = (
                "OK" if pf_live >= 0.8 else ("ALERTA" if pf_live >= 0.5 else "FUERA")
            )
        elif tl < 50:
            pf_estado = (
                "OK" if pf_live >= 1.0 else ("ALERTA" if pf_live >= 0.8 else "FUERA")
            )
        else:
            ref = bt_pf if bt_pf is not None else 1.0
            pf_estado = (
                "OK" if pf_live >= ref else ("ALERTA" if pf_live >= 1.0 else "FUERA")
            )
    else:
        pf_estado = "N/D"
    result["pf_live"] = pf_live
    result["pf_bt"] = bt_pf
    result["pf_estado"] = pf_estado

    # ── Payout Ratio ───────────────────────────────────────────────────────
    if payout_live is not None and payout_live >= 1e9:
        # Zero losing trades: metrics.fmt_pf() reports payout_ratio as "∞",
        # _safe_float maps it to a large finite sentinel (1e9). An infinite
        # payout ratio cannot represent a deviation from BT -- treat as OK
        # instead of letting the percentage-variance check read it as FUERA.
        payout_var = None
        payout_estado = "OK"
    elif payout_live is not None and bt_payout is not None and bt_payout != 0:
        payout_var = (payout_live - bt_payout) / bt_payout * 100
        abs_pv = abs(payout_var)
        if tl < 30:
            payout_estado = (
                "OK" if abs_pv <= 40 else ("ALERTA" if abs_pv <= 60 else "FUERA")
            )
        elif tl < 50:
            payout_estado = (
                "OK" if abs_pv <= 30 else ("ALERTA" if abs_pv <= 50 else "FUERA")
            )
        else:
            payout_estado = (
                "OK" if abs_pv <= 25 else ("ALERTA" if abs_pv <= 40 else "FUERA")
            )
    else:
        payout_var = None
        payout_estado = "N/D"
    result["payout_var"] = round(payout_var, 1) if payout_var is not None else None
    result["payout_live"] = payout_live
    result["payout_bt"] = bt_payout
    result["payout_estado"] = payout_estado

    # ── DD% Escalado ───────────────────────────────────────────────────────
    dd_limit_used = None
    dd_method = "N/D"
    if max_dd_live is not None:
        if (
            bt_worst_dd_1m is not None
            and bt_worst_dd_1m > 0
            and bt_trades is not None
            and bt_trades > 0
            and bt_months is not None
            and bt_months > 0
            and trades_live > 0
        ):
            # Trade clock, not a calendar clock: equity is a discrete random
            # walk indexed by trades, so variance accumulates per trade and
            # idle calendar time contributes none. bt_freq_mes normalizes to
            # "one month of BT trading pace", exactly as the old 4.33 constant
            # normalized to "one calendar month" -- bt_worst_dd_1m keeps its
            # meaning and the scaling factor is still 1.0 at the reference
            # trading pace (docs/metrics-formulas.md §13).
            bt_freq_mes = bt_trades / bt_months
            # §5 + B3 site1: the operand guards (bt_months > 0, bt_trades > 0)
            # do NOT guarantee a usable reference. A backtest below the sanity
            # floor cannot ground a monthly rate, and a normal-but-extreme
            # operand pair can make bt_freq_mes (or the scaled dd_limit)
            # underflow to exactly 0.0 / overflow to inf even though both
            # operands passed `> 0`. Any of these is a degenerate reference ->
            # dd_estado N/D, which the completeness gate turns into SIN DATOS
            # naming the cause, instead of a confident-but-fictional dd_limit.
            if bt_months < BT_MONTHS_SANITY_FLOOR:
                dd_estado = "N/D"
            elif not math.isfinite(bt_freq_mes) or bt_freq_mes <= 0:
                dd_estado = "N/D"
            else:
                dd_limit = bt_worst_dd_1m * math.sqrt(trades_live / bt_freq_mes)
                if not math.isfinite(dd_limit) or dd_limit <= 0:
                    dd_estado = "N/D"
                else:
                    dd_limit_used = round(dd_limit, 2)
                    dd_method = (
                        f"sqrt({trades_live:.0f}tr/{bt_freq_mes:.1f}tr-mes) "
                        f"x {bt_worst_dd_1m}%"
                    )
                    dd_estado = (
                        "OK"
                        if max_dd_live <= dd_limit
                        else ("ALERTA" if max_dd_live <= dd_limit * 1.5 else "FUERA")
                    )
        elif mc_r_dd is not None and mc_t_dd is not None:
            # Use the more conservative (higher) of the two MC 95% DD values as the
            # ALERTA boundary so the zone is always reachable regardless of which
            # MC method produces a tighter threshold. mc_r_dd/mc_t_dd are 95% MDD
            # figures over the FULL backtest and are NOT scaled by trade/time
            # count -- scaling them for a young EA would reintroduce the
            # newborn-execution defect this fallback is not exposed to.
            mc_dd_alert = max(mc_r_dd, mc_t_dd)
            dd_limit_used = min(mc_r_dd, mc_t_dd)
            # KNOWN QUIRK, deliberately left as-is: when mc_r_dd == mc_t_dd the
            # ALERTA zone is empty (max() == min()), so this gate collapses to
            # two states and an EA whose two MC methods AGREE gets a harsher
            # gate than one whose methods disagree. Widening to the BT path's
            # 1.5x convention was tried and rejected: it moves DD in
            # (max(mc), 1.5*min(mc)] from FUERA -- a hard ELIMINAR veto -- to
            # ALERTA, which is a broad loosening of the stop path and breaks the
            # pinned boundaries in test_dd_estado_both_mc_present_fallback_
            # boundaries. Changing it is a policy decision, not a bug fix.
            dd_method = "MC min(Retest,Trades) 95% (fallback)"
            dd_estado = (
                "OK"
                if max_dd_live <= dd_limit_used
                else ("ALERTA" if max_dd_live <= mc_dd_alert else "FUERA")
            )
        else:
            dd_estado = "N/D"
    else:
        dd_estado = "N/D"
    result["dd_live"] = max_dd_live
    result["dd_limit"] = dd_limit_used
    result["dd_method"] = dd_method
    result["dd_estado"] = dd_estado

    # ── Consec Losses ──────────────────────────────────────────────────────
    if consec_losses_live is not None and bt_max_consec is not None:
        cl_live = int(consec_losses_live)
        cl_bt = int(bt_max_consec)
        consec_ratio = f"{cl_live}/{cl_bt}"
        consec_estado = (
            "OK"
            if cl_live <= cl_bt
            else ("ALERTA" if cl_live <= cl_bt * 1.5 else "FUERA")
        )
    else:
        consec_ratio = "N/D"
        consec_estado = "N/D"
    result["consec_ratio"] = consec_ratio
    result["consec_estado"] = consec_estado

    # ── Avg Bars/Trade ─────────────────────────────────────────────────────
    if avg_bars_live is not None and bt_avg_bars is not None and bt_avg_bars != 0:
        bars_var = (avg_bars_live - bt_avg_bars) / bt_avg_bars * 100
        abs_bv = abs(bars_var)
        if tl < 30:
            bars_estado = (
                "OK" if abs_bv <= 50 else ("ALERTA" if abs_bv <= 70 else "FUERA")
            )
        else:
            bars_estado = (
                "OK" if abs_bv <= 30 else ("ALERTA" if abs_bv <= 50 else "FUERA")
            )
    else:
        bars_var = None
        bars_estado = "N/D"
    result["bars_var"] = round(bars_var, 1) if bars_var is not None else None
    result["avg_bars_live"] = avg_bars_live
    result["avg_bars_bt"] = bt_avg_bars
    result["bars_estado"] = bars_estado

    # ── Frecuencia Trades ──────────────────────────────────────────────────
    freq_pct = None
    freq_estado = "N/D"
    if (
        bt_trades
        and bt_months
        and bt_months >= BT_MONTHS_SANITY_FLOOR
        and weeks_live > 0
    ):
        bt_freq_per_month = bt_trades / bt_months
        live_months = weeks_live / 4.33
        # B3 site2: the operand guards (> 0) do NOT guarantee usable quotients.
        # bt_trades/bt_months can underflow to 0.0, and weeks_live/4.33 can
        # underflow to 0.0 for a subnormal weeks_live even though weeks_live > 0.
        # Either would divide by zero below — treat a degenerate reference as
        # N/D (-> SIN DATOS at the gate) instead of crashing.
        if (
            math.isfinite(bt_freq_per_month)
            and bt_freq_per_month > 0
            and math.isfinite(live_months)
            and live_months > 0
        ):
            live_freq_per_month = trades_live / live_months
            freq_pct = (live_freq_per_month / bt_freq_per_month) * 100

    if freq_pct is not None:
        # Two-sided, like wr_estado and bars_estado: trading far ABOVE backtest
        # pace is a deviation too. A one-sided check read 413% of BT pace as
        # "OK", so an EA whose character changed (grid/martingale degradation,
        # a broker feeding duplicate signals) went unflagged -- and, since
        # dd_limit scales on sqrt(trades), that extra pace also bought it a
        # proportionally larger drawdown allowance with nothing to object.
        # The under-trading boundaries are unchanged: a deviation of 30 is the
        # old freq_pct >= 70 and a deviation of 50 is the old freq_pct >= 50.
        freq_dev = abs(freq_pct - 100)
        freq_estado = (
            "OK" if freq_dev <= 30 else ("ALERTA" if freq_dev <= 50 else "FUERA")
        )
    result["freq_pct"] = round(freq_pct, 1) if freq_pct is not None else None
    result["freq_estado"] = freq_estado

    # ── Edge Erosion ───────────────────────────────────────────────────────
    if (
        expect_live is not None
        and spp_expect_median is not None
        and spp_expect_median != 0
    ):
        edge_erosion = (expect_live - spp_expect_median) / spp_expect_median * 100
        edge_estado = (
            "OK"
            if edge_erosion >= -30
            else ("ALERTA" if edge_erosion >= -60 else "FUERA")
        )
    else:
        edge_erosion = None
        edge_estado = "N/D"
    result["edge_erosion"] = (
        round(edge_erosion, 1) if edge_erosion is not None else None
    )
    result["expect_live"] = expect_live
    result["spp_expect_median"] = spp_expect_median
    result["edge_estado"] = edge_estado

    # ── Stagnation ─────────────────────────────────────────────────────────
    if stagnation_live is not None:
        sl = int(stagnation_live)
        if bt_stagnation is not None and bt_stagnation > 0:
            stagn_label = (
                "Normal"
                if sl <= bt_stagnation * 0.3
                else ("Elevada" if sl <= bt_stagnation * 0.6 else "Alta")
            )
        else:
            stagn_label = "Normal" if sl <= 60 else ("Elevada" if sl <= 120 else "Alta")
        stagn_estado = (
            "OK"
            if stagn_label == "Normal"
            else ("ALERTA" if stagn_label == "Elevada" else "FUERA")
        )
    else:
        stagn_label = "N/D"
        stagn_estado = "N/D"
    result["stagn_live"] = stagnation_live
    result["stagn_bt"] = bt_stagnation
    result["stagn_label"] = stagn_label
    result["stagn_estado"] = stagn_estado

    # ── Completeness gate #2: no SCORED estado may reach scoring as N/D ────
    # (docs/design/decision-engine-no-data-contract.md, F2 correction round).
    # The presence-only gate above catches None/absent required fields, but
    # it does not catch degenerate-but-present values -- weeks_live <= 0
    # (e.g. an EA closing 5+ trades on its first day) or a reference field
    # that is literally 0 (bt_worst_dd_1m, bt_payout, bt_avg_bars,
    # spp_expect_median) -- that still leave an estado computed as "N/D"
    # below. Enforce the actual invariant here, structurally, for every
    # SCORED estado. `live_vs_bt_profit_status` is informational and is deliberately
    # excluded -- it must never trigger SIN DATOS.
    scored_estados = {
        "dd_estado": dd_estado,
        "consec_estado": consec_estado,
        "stagn_estado": stagn_estado,
        "wr_estado": wr_estado,
        "pf_estado": pf_estado,
        "payout_estado": payout_estado,
        "bars_estado": bars_estado,
        "freq_estado": freq_estado,
        "edge_estado": edge_estado,
    }
    nd_estados = [name for name, estado in scored_estados.items() if estado == "N/D"]
    if nd_estados:
        causes = []

        def _add_cause(name):
            if name not in causes:
                causes.append(name)

        if "dd_estado" in nd_estados:
            # The DD branch scales on the TRADE clock, so weeks_operating is no
            # longer one of its inputs -- its causes are the trade-clock terms.
            if bt_worst_dd_1m is None or bt_worst_dd_1m <= 0:
                _add_cause("bt.worst_dd_1m")
            if not bt_trades or bt_trades <= 0:
                _add_cause("bt.trades_total")
            # §5: below the sanity floor (incl. None/0/negative) bt.months is
            # a degenerate monthly reference.
            if bt_months is None or bt_months < BT_MONTHS_SANITY_FLOOR:
                _add_cause("bt.months")
            if trades_live <= 0:
                _add_cause("live.total_trades")
            # B3 site1: operands all look present and positive but the derived
            # rate / scaled DD underflowed or overflowed -> name the reference.
            if not causes:
                _add_cause("bt.trades_total")
                _add_cause("bt.months")
        if "freq_estado" in nd_estados:
            if weeks_live <= 0:
                _add_cause("live.weeks_operating")
            if not bt_trades:
                _add_cause("bt.trades_total")
            if bt_months is None or bt_months < BT_MONTHS_SANITY_FLOOR:
                _add_cause("bt.months")
            # B3 site2: operands all look present and positive but the derived
            # monthly frequency / live_months underflowed (subnormal weeks) ->
            # name the references so the reason never ships an empty causes list.
            if not causes:
                _add_cause("bt.trades_total")
                _add_cause("bt.months")
                _add_cause("live.weeks_operating")
        if "payout_estado" in nd_estados and bt_payout == 0:
            _add_cause("bt.payout_ratio")
        if "bars_estado" in nd_estados and bt_avg_bars == 0:
            _add_cause("bt.avg_bars")
        if "edge_estado" in nd_estados and spp_expect_median == 0:
            _add_cause("spp.expectancy_median")

        reason = "Estados no evaluables para score: " + ", ".join(nd_estados)
        if causes:
            reason += " (causa: " + ", ".join(causes) + ")"
        return _nd_result("SIN DATOS", reason, missing=nd_estados + causes)

    # ── Category Scores ────────────────────────────────────────────────────
    s_riesgo = (
        _pts(dd_estado) * CONFIG["w_dd_escalado"] / 100
        + _pts(consec_estado) * CONFIG["w_consec_losses"] / 100
        + _pts(stagn_estado) * CONFIG["w_stagnation"] / 100
    )

    s_edge = (
        _pts(wr_estado) * CONFIG["w_win_rate"] / 100
        + _pts(pf_estado) * CONFIG["w_profit_factor"] / 100
        + _pts(payout_estado) * CONFIG["w_payout_ratio"] / 100
        + _pts(edge_estado) * CONFIG["w_edge_erosion"] / 100
    )

    s_caracter = (
        _pts(freq_estado) * CONFIG["w_frecuencia"] / 100
        + _pts(bars_estado) * CONFIG["w_avg_bars"] / 100
    )

    # Conteo de deterioro para S.Desv y flag Desviacion Estructural
    detcount = 0
    if wr_live is not None and bt_wr is not None and wr_live < bt_wr - 5:
        detcount += 1
    if (
        payout_live is not None
        and bt_payout is not None
        and payout_live < bt_payout * 0.8
    ):
        detcount += 1
    if pf_live is not None and bt_pf is not None and pf_live < bt_pf * 0.8:
        detcount += 1
    if edge_erosion is not None and edge_erosion < -30:
        detcount += 1
    if freq_pct is not None and freq_pct < 70:
        detcount += 1

    if detcount == 0:
        s_desv = 10
    elif detcount <= 1:
        s_desv = 8
    elif detcount <= 2:
        s_desv = 5
    else:
        s_desv = 0

    result["s_riesgo"] = round(s_riesgo, 2)
    result["s_edge"] = round(s_edge, 2)
    result["s_caracter"] = round(s_caracter, 2)
    result["s_desv"] = s_desv
    result["detcount"] = detcount

    # ── Score total /100 ───────────────────────────────────────────────────
    # Canonizar sobre el valor publicado: redondear una sola vez y decidir el
    # veredicto con el MISMO score que se muestra, para que el número visible no
    # pueda contradecir al veredicto (known-issues 14-A1/14-E; el gemelo en CP3
    # está en incubation_validator.py). En validator.py la grilla exacta no
    # tiene puntos en las bandas peligrosas, así que ningún veredicto cambia hoy;
    # esto blinda el patrón ante futuros cambios de pesos en CONFIG.
    score = round(
        (
            CONFIG["w_riesgo"] * s_riesgo
            + CONFIG["w_edge"] * s_edge
            + CONFIG["w_caracter"] * s_caracter
            + CONFIG["w_desv"] * s_desv
        )
        / 10,
        1,
    )
    result["score"] = score

    # ── Desviacion Estructural ─────────────────────────────────────────────
    desv_flag = "DESV" if detcount >= CONFIG["thresh_desv"] else "-"
    result["desv_flag"] = desv_flag

    # ── VEREDICTO ──────────────────────────────────────────────────────────
    if dd_estado == "FUERA":
        veredicto = "ELIMINAR"
    elif pf_live is not None and pf_live < 1.0 and tl >= 50:
        veredicto = "ELIMINAR"
    elif score >= CONFIG["thresh_continuar"]:
        veredicto = "CONTINUAR"
    elif score >= CONFIG["thresh_monitorear"]:
        veredicto = "MONITOREAR"
    else:
        veredicto = "ELIMINAR"
    result["veredicto"] = veredicto

    # ── ACCION ─────────────────────────────────────────────────────────────
    if veredicto == "ELIMINAR":
        if dd_estado == "FUERA":
            accion = "DD% supero limite escalado - detener inmediatamente"
        elif pf_live is not None and pf_live < 1.0 and tl >= 50:
            accion = "PF < 1 sostenido con 50+ trades - detener"
        else:
            accion = "Score bajo umbral minimo - evaluar desactivacion"
    elif veredicto == "MONITOREAR":
        if desv_flag == "DESV":
            accion = "Deterioro estructural: posible cambio de regimen"
        elif tl < 30:
            accion = f"Solo {tl} trades. Esperar a 30+ para decision firme."
        else:
            accion = "Score moderado - revisar metricas en detalle"
    else:
        if desv_flag == "DESV":
            accion = "Funciona pero con senal de deterioro - vigilar de cerca"
        else:
            accion = "Mantener activo - opera dentro de parametros esperados"
    # ── Live vs Backtest ratio ──────────────────────────────────────────────
    # NOT Walk-Forward Efficiency: WFE (Pardo) compares out-of-sample backtest
    # to in-sample backtest across rolling re-optimized windows. This compares
    # live results to a single backtest aggregate -- it absorbs slippage,
    # spread, commission and regime change on top of any overfit, and cannot
    # separate them. It also structurally cannot BE walk-forward analysis:
    # this function only receives operator-typed aggregates (bt_expect,
    # bt_trades, bt_months), never a backtest equity curve or a parameter
    # space to re-optimize. See docs/research/prior-art.md §4.5.
    live_vs_bt_profit_ratio = None
    live_vs_bt_profit_status = "N/D"
    if (
        bt_expect is not None
        and bt_trades is not None
        and bt_months is not None
        and bt_months > 0
        and bt_trades > 0
        and expect_live is not None
        and trades_live > 0
        and weeks_live > 0
    ):
        bt_profit_per_month = bt_expect * bt_trades / bt_months
        live_months = weeks_live / 4.33
        live_profit_per_month = (
            expect_live * trades_live / live_months if live_months > 0 else 0
        )
        if bt_profit_per_month != 0:
            raw_ratio = (live_profit_per_month / bt_profit_per_month) * 100
            live_vs_bt_profit_ratio = round(raw_ratio, 1)
            # C7 (display-only, no scored estado): band on the UNROUNDED ratio so
            # a value just over a boundary (e.g. raw 120.05, which rounds to
            # 120.0) is not silently reclassified from ALERTA to OK. The
            # published number stays 1dp, consistent with prior fix 4A.
            if raw_ratio > 120:
                live_vs_bt_profit_status = "ALERTA"  # mejor que BT, posible sobreajuste del BT
            elif raw_ratio >= 70:
                live_vs_bt_profit_status = "OK"
            elif raw_ratio >= 30:
                live_vs_bt_profit_status = "ALERTA"
            else:
                live_vs_bt_profit_status = "FUERA"
    result["live_vs_bt_profit_ratio"] = live_vs_bt_profit_ratio
    result["live_vs_bt_profit_status"] = live_vs_bt_profit_status

    result["accion"] = accion
    result["sin_datos"] = False
    result["missing"] = []

    return result


def _finite_or_none(value):
    """Coerce a count to a FINITE float, or None for missing/non-numeric/
    non-finite input.

    Unlike _safe_float, this does NOT map ±inf to ±1e9: a trade COUNT cannot be
    infinite the way an ∞ profit_factor can, so NaN/±inf/non-numeric all become
    None (SIN DATOS) rather than a bogus large finite number (B1/B2).
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _safe_float(val):
    """Safely convert to float. Returns None if not convertible.

    "∞" and equivalent strings are treated as a very large finite number (1e9)
    so that metrics like profit_factor and payout_ratio that are mathematically
    infinite (zero losing trades) still contribute positively to the score
    instead of being silently dropped as N/D.
    """
    if val is None or val == "":
        return None
    # Infinity representations coming from metrics.fmt_pf()
    if val in ("∞", "inf", "Inf", "+inf", "+Inf", "Infinity", float("inf")):
        return 1e9
    try:
        f = float(val)
        if math.isnan(f):
            return None
        if math.isinf(f):
            return 1e9 if f > 0 else -1e9
        return f
    except (ValueError, TypeError):
        return None


def get_all_validator_results(parsed_data: dict, config: dict, store: dict) -> list:
    """
    Build the full validator table for all active EAs that have a magic number.
    Returns list of dicts with name, magic, live metrics, bt data, and score.
    """
    results = []
    mappings = config.get("mappings", {})

    for ea_name, mapping in mappings.items():
        if not mapping.get("active", True):
            continue

        magic = str(mapping.get("magic", "")).strip()
        if not magic:
            continue

        # Check if we have BT data for this magic
        bt_entry = store.get(magic, {})
        has_bt = bool(bt_entry.get("bt", {}))

        # Get live trades for this EA
        ea_trades = [
            t
            for t in parsed_data.get("closed_trades", [])
            if trade_matches_ea(t, ea_name, config)
        ]

        if not ea_trades:
            live_metrics = {
                "total_trades": 0,
                "weeks_operating": 0,
                "win_rate": 0,
                "profit_factor": 0,
                "payout_ratio": 0,
                "expectancy": 0,
                "max_dd_pct": 0,
                "max_consec_losses": 0,
                "stagnation_days": 0,
                "avg_duration_hours": 0,
            }
        else:
            live_metrics = calculate_ea_metrics(ea_name, ea_trades, config)

        # Compute avg_bars_live from duration_hours + timeframe
        tf = bt_entry.get("timeframe", "H1")
        tf_hours = timeframe_to_hours(tf)
        avg_dur = live_metrics.get("avg_duration_hours") or 0
        avg_bars_live = round(avg_dur / tf_hours, 1) if tf_hours > 0 else None

        # Adjust live dict for scoring
        live_for_score = {
            "total_trades": live_metrics.get("total_trades", 0),
            "weeks_operating": live_metrics.get("weeks_operating", 0),
            "win_rate": live_metrics.get("win_rate"),
            "profit_factor": _safe_float(live_metrics.get("profit_factor")),
            "payout_ratio": _safe_float(live_metrics.get("payout_ratio")),
            "expectancy": live_metrics.get("expectancy"),
            "max_dd_pct": live_metrics.get("max_dd_pct"),
            "max_consec_losses": live_metrics.get("max_consec_losses"),
            "stagnation_days": live_metrics.get("stagnation_days"),
            "avg_bars_live": avg_bars_live,
        }

        alias = mapping.get("alias", "") or ea_name
        label = f"{magic} - {alias}" if magic else alias

        row = {
            "magic": magic,
            "ea_name": ea_name,
            "label": label,
            "instrument": bt_entry.get("instrument", mapping.get("instrument", "")),
            "timeframe": tf,
            "has_bt": has_bt,
            "live": live_for_score,
            "avg_bars_live": avg_bars_live,
        }

        if has_bt:
            analysis = calculate_validator_score(
                bt=bt_entry.get("bt", {}),
                mc_retest=bt_entry.get("mc_retest", {}),
                mc_trades=bt_entry.get("mc_trades", {}),
                spp=bt_entry.get("spp", {}),
                live=live_for_score,
            )
            row["analysis"] = analysis
        else:
            row["analysis"] = None

        results.append(row)

    # Sort: has_bt first, then by score desc (score can be None for SIN DATOS)
    def _sort_key(x):
        if not x["has_bt"] or not x["analysis"]:
            return (1, 0)
        score = x["analysis"].get("score")
        return (0, -(score if score is not None else -1))

    results.sort(key=_sort_key)

    return results
