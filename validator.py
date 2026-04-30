"""
validator.py - EA Validator: Sistema de Scoring Ponderado con Umbrales Dinámicos
Implementación Python del EA_Validator_Final_v2.xlsx
Compara resultados LIVE (MT5) vs Backtest (SQX/manual) por magic number.
"""

import math
import os

from local_json import load_local_json, save_local_json
from metrics import calculate_ea_metrics

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


def _pts(estado: str) -> int:
    if estado == "OK":
        return CONFIG["pts_ok"]
    elif estado == "ALERTA":
        return CONFIG["pts_alerta"]
    return CONFIG["pts_fuera"]


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
    trades_live = float(live.get("total_trades") or 0)
    weeks_live = float(live.get("weeks_operating") or 0)
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
    tl = int(trades_live)
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

    def _nd_result(veredicto, accion):
        result["veredicto"] = veredicto
        result["accion"] = accion
        result["sin_datos"] = True
        result["score"] = None
        result["desv_flag"] = "-"
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
            "wfe", "wfe_status",
        ):
            result.setdefault(key, None)
        for key in (
            "wr_estado", "pf_estado", "payout_estado", "dd_estado",
            "consec_estado", "bars_estado", "freq_estado", "edge_estado",
            "stagn_estado", "stagn_label", "dd_method", "consec_ratio",
        ):
            result[key] = "N/D"
        result["wfe_status"] = "N/D"
        return result

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
    if payout_live is not None and bt_payout is not None and bt_payout != 0:
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
        if bt_worst_dd_1m is not None and bt_worst_dd_1m > 0 and weeks_live > 0:
            dd_limit = bt_worst_dd_1m * math.sqrt(weeks_live / 4.33)
            dd_limit_used = round(dd_limit, 2)
            dd_method = f"sqrt({weeks_live:.1f}sem/4.33) x {bt_worst_dd_1m}%"
            dd_estado = (
                "OK"
                if max_dd_live <= dd_limit
                else ("ALERTA" if max_dd_live <= dd_limit * 1.5 else "FUERA")
            )
        elif mc_r_dd is not None and mc_t_dd is not None:
            # Use the more conservative (higher) of the two MC 95% DD values as the
            # ALERTA boundary so the zone is always reachable regardless of which
            # MC method produces a tighter threshold.
            mc_dd_alert = max(mc_r_dd, mc_t_dd)
            dd_limit_used = min(mc_r_dd, mc_t_dd)
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
    if bt_trades and bt_months and bt_months > 0 and weeks_live > 0:
        bt_freq_per_month = bt_trades / bt_months
        live_freq_per_month = trades_live / (weeks_live / 4.33)
        freq_pct = (live_freq_per_month / bt_freq_per_month) * 100
        freq_estado = (
            "OK" if freq_pct >= 70 else ("ALERTA" if freq_pct >= 50 else "FUERA")
        )
    else:
        freq_pct = None
        freq_estado = "N/D"
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
    score = (
        CONFIG["w_riesgo"] * s_riesgo
        + CONFIG["w_edge"] * s_edge
        + CONFIG["w_caracter"] * s_caracter
        + CONFIG["w_desv"] * s_desv
    ) / 10
    result["score"] = round(score, 1)

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
    # ── Walk-Forward Efficiency ────────────────────────────────────────────
    wfe = None
    wfe_status = "N/D"
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
            wfe = round((live_profit_per_month / bt_profit_per_month) * 100, 1)
            if wfe > 120:
                wfe_status = "ALERTA"  # mejor que BT, posible sobreajuste del BT
            elif wfe >= 70:
                wfe_status = "OK"
            elif wfe >= 30:
                wfe_status = "ALERTA"
            else:
                wfe_status = "FUERA"
    result["wfe"] = wfe
    result["wfe_status"] = wfe_status

    result["accion"] = accion
    result["sin_datos"] = False

    return result


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
            if t.get("comment") == ea_name
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
