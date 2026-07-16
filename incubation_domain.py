"""
incubation_domain.py - Incubation decision/presentation domain

Pure logic for the EA incubation workflow: reference-data parsing and
form handling, checkpoint/verdict computation, comparison rows against
Monte Carlo / SPP references, and dashboard/timeline presentation
payloads. Everything here is importable and testable without Flask.

This module MUST stay free of Flask imports (request, session,
render_template, jsonify, flash, redirect, url_for, current_app, abort,
g, app). Session-backed data loading and URL building live in
ea_analyzer.py, which calls into this module with the data it needs.
"""

from collections import defaultdict
from datetime import date, datetime

from incubation_validator import _safe_float, get_worst_case_mc
from trade_matching import trade_matches_ea

INCUBATION_BACKTEST_FIELDS = [
    {"key": "net_profit", "label": "Net Profit ($)", "type": "text", "placeholder": "6338.81"},
    {"key": "total_trades", "label": "Total Trades", "type": "text", "placeholder": "487"},
    {"key": "win_rate", "label": "Win Rate (%)", "type": "text", "placeholder": "52.37"},
    {"key": "profit_factor", "label": "Profit Factor", "type": "text", "placeholder": "1.72"},
    {"key": "max_dd_pct", "label": "Max Drawdown (%)", "type": "text", "placeholder": "10.97"},
    {"key": "ret_dd_ratio", "label": "Ret/DD Ratio", "type": "text", "placeholder": "9.32"},
    {"key": "sqn_score", "label": "SQN Score", "type": "text", "placeholder": "1.08"},
    {"key": "expectancy", "label": "Expectancy ($/trade)", "type": "text", "placeholder": "9.33"},
    {"key": "max_consec_losses", "label": "Max Consecutive Losses", "type": "text", "placeholder": "8"},
    {"key": "avg_bars_trade", "label": "Avg Bars/Trade", "type": "text", "placeholder": "15"},
    {"key": "payout_ratio", "label": "Payout Ratio", "type": "text", "placeholder": "1.52"},
    {"key": "stagnation_days", "label": "Stagnation (days)", "type": "text", "placeholder": "287"},
    {"key": "bt_period", "label": "Período de BT", "type": "text", "placeholder": "2017.10.02 - 2026.01.28"},
    {
        "key": "timeframe",
        "label": "Timeframe",
        "type": "select",
        "options": ["M1", "M5", "M15", "M30", "H1", "H4", "D1"],
    },
]

INCUBATION_MC_95_FIELDS = [
    {"key": "max_dd_pct", "label": "Max DD (%)", "type": "text", "placeholder": "10.63"},
    {"key": "profit_factor", "label": "Profit Factor", "type": "text", "placeholder": "1.57"},
    {"key": "win_rate", "label": "Win Rate (%)", "type": "text", "placeholder": "51.04"},
    {"key": "stagnation_days", "label": "Stagnation (days)", "type": "text", "placeholder": "217"},
    {"key": "ret_dd_ratio", "label": "Ret/DD Ratio", "type": "text", "placeholder": "7.56"},
    {"key": "sqn_score", "label": "SQN Score", "type": "text", "placeholder": "1.20"},
    {"key": "avg_trade", "label": "Avg Trade ($)", "type": "text", "placeholder": "7.51"},
    {"key": "max_consec_losses", "label": "Max Consecutive Losses", "type": "text", "placeholder": "10"},
    {"key": "payout_ratio", "label": "Payout Ratio", "type": "text", "placeholder": "1.40"},
    {"key": "expectancy", "label": "Expectancy ($)", "type": "text", "placeholder": "7.51"},
    {
        "key": "simulations",
        "label": "Nº Simulaciones",
        "type": "text",
        "placeholder": "1000",
        "store_at": "group",
    },
    {
        "key": "method",
        "label": "Método",
        "type": "text",
        "placeholder": "Randomize trades order, Exact + Randomly skip 10%",
        "store_at": "group",
    },
]

INCUBATION_MC_50_FIELDS = [
    {"key": "max_dd_pct", "label": "Max DD (%)", "type": "text", "placeholder": "6.39"},
    {"key": "profit_factor", "label": "Profit Factor", "type": "text", "placeholder": "1.65"},
    {"key": "win_rate", "label": "Win Rate (%)", "type": "text", "placeholder": "52.07"},
    {"key": "stagnation_days", "label": "Stagnation (days)", "type": "text", "placeholder": "111"},
    {"key": "ret_dd_ratio", "label": "Ret/DD Ratio", "type": "text", "placeholder": "12.3"},
    {"key": "sqn_score", "label": "SQN Score", "type": "text", "placeholder": "1.35"},
    {"key": "avg_trade", "label": "Avg Trade ($)", "type": "text", "placeholder": "8.41"},
    {"key": "max_consec_losses", "label": "Max Consecutive Losses", "type": "text", "placeholder": "7"},
    {"key": "payout_ratio", "label": "Payout Ratio", "type": "text", "placeholder": "1.48"},
    {"key": "expectancy", "label": "Expectancy ($)", "type": "text", "placeholder": "8.41"},
]

INCUBATION_REFERENCE_SECTIONS = [
    {
        "key": "backtest",
        "title": "SECCION 1 - BACKTEST",
        "required": True,
        "info": "Backtest obligatorio para poder guardar.",
        "group": "backtest",
        "confidence_key": None,
        "tag_label": None,
        "tag_class": None,
        "fields": INCUBATION_BACKTEST_FIELDS,
    },
    {
        "key": "mc_manipulation_95",
        "title": "SECCION 2 - Monte Carlo - Trades Manipulation (95% Confidence)",
        "required": True,
        "info": "Robustez estadística: que pasa si los trades llegan en otro orden o algunos no se ejecutan.",
        "group": "mc_manipulation",
        "confidence_key": "confidence_95",
        "tag_label": "MANIPULATION",
        "tag_class": "ref-badge--manip",
        "fields": INCUBATION_MC_95_FIELDS,
    },
    {
        "key": "mc_manipulation_50",
        "title": "SECCION 3 - Monte Carlo - Trades Manipulation (50% Confidence)",
        "required": False,
        "info": "Referencia central del mismo test.",
        "group": "mc_manipulation",
        "confidence_key": "confidence_50",
        "tag_label": "MANIPULATION",
        "tag_class": "ref-badge--manip",
        "fields": INCUBATION_MC_50_FIELDS,
    },
    {
        "key": "mc_retest_95",
        "title": "SECCION 4 - Monte Carlo - Retest Methods (95% Confidence)",
        "required": True,
        "info": "Sensibilidad a ejecucion: que pasa si spread, slippage o precio varian.",
        "group": "mc_retest",
        "confidence_key": "confidence_95",
        "tag_label": "RETEST",
        "tag_class": "ref-badge--retest",
        "fields": INCUBATION_MC_95_FIELDS,
    },
    {
        "key": "mc_retest_50",
        "title": "SECCION 5 - Monte Carlo - Retest Methods (50% Confidence)",
        "required": False,
        "info": "Referencia central del mismo test.",
        "group": "mc_retest",
        "confidence_key": "confidence_50",
        "tag_label": "RETEST",
        "tag_class": "ref-badge--retest",
        "fields": INCUBATION_MC_50_FIELDS,
    },
    {
        "key": "spp",
        "title": "SECCION 6 - SYSTEM PARAMETER PERMUTATION (SPP)",
        "required": False,
        "info": "Sin datos SPP, el analisis usara solo BT y MC.",
        "group": "spp",
        "confidence_key": None,
        "tag_label": None,
        "tag_class": None,
        "fields": [
            {"key": "median_net_profit", "label": "Median Net Profit ($)", "type": "text", "placeholder": "4093.04", "ratio_source": "backtest.net_profit"},
            {"key": "median_max_dd_pct", "label": "Median Max DD (%)", "type": "text", "placeholder": "7.83", "ratio_source": "backtest.max_dd_pct"},
            {"key": "median_ret_dd_ratio", "label": "Median Ret/DD Ratio", "type": "text", "placeholder": "7.76", "ratio_source": "backtest.ret_dd_ratio"},
            {"key": "median_stability", "label": "Median Stability", "type": "text", "placeholder": "0.84"},
            {"key": "median_stagnation_days", "label": "Median Stagnation (days)", "type": "text", "placeholder": "441", "ratio_source": "backtest.stagnation_days"},
            {"key": "median_sqn_score", "label": "Median SQN Score", "type": "text", "placeholder": "0.94", "ratio_source": "backtest.sqn_score"},
            {"key": "median_avg_trade", "label": "Median Avg Trade ($)", "type": "text", "placeholder": "5.49", "ratio_source": "backtest.expectancy"},
            {"key": "median_payout_ratio", "label": "Median Payout Ratio", "type": "text", "placeholder": "1.48", "ratio_source": "backtest.payout_ratio"},
        ],
    },
]


def _normalize_decimal_text(value):
    if value is None:
        return ""
    return str(value).strip()


def _parse_numeric_input(value):
    text = _normalize_decimal_text(value)
    if not text:
        return None
    normalized = text.replace(" ", "")
    if "," in normalized and "." in normalized:
        if normalized.rfind(",") > normalized.rfind("."):
            normalized = normalized.replace(".", "").replace(",", ".")
        else:
            normalized = normalized.replace(",", "")
    else:
        normalized = normalized.replace(",", ".")
    try:
        return float(normalized)
    except ValueError:
        return None


def _format_numeric_value(value):
    if value is None or value == "":
        return ""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    if num.is_integer():
        return str(int(num))
    return ("%.4f" % num).rstrip("0").rstrip(".")


def parse_reference_form(form):
    data = {
        "backtest": {},
        "mc_manipulation": {"confidence_95": {}, "confidence_50": {}, "simulations": None, "method": ""},
        "mc_retest": {"confidence_95": {}, "confidence_50": {}, "simulations": None, "method": ""},
        "spp": {},
        "monte_carlo": None,
    }
    errors = {}
    warnings = []
    int_fields = {"total_trades", "max_consec_losses", "avg_bars_trade", "stagnation_days", "simulations"}

    def read_field(section_key, field_key, field_label, required, numeric=True):
        raw = _normalize_decimal_text(form.get(f"{section_key}_{field_key}", ""))
        if not raw:
            if required and section_key == "backtest":
                errors[f"{section_key}.{field_key}"] = f"{field_label} es obligatorio."
            return None
        if numeric:
            parsed = _parse_numeric_input(raw)
            if parsed is None:
                errors[f"{section_key}.{field_key}"] = f"{field_label} debe ser un número válido."
            elif field_key in int_fields:
                return int(round(parsed))
            return parsed
        return raw

    for section in INCUBATION_REFERENCE_SECTIONS:
        target_root = data[section["group"]]
        target_conf = target_root.get(section["confidence_key"], {}) if section.get("confidence_key") else target_root
        for field in section["fields"]:
            value = read_field(
                section["key"],
                field["key"],
                field["label"],
                section["required"],
                numeric=field["key"] not in {"bt_period", "timeframe", "method"},
            )
            if value is not None:
                if field.get("store_at") == "group":
                    target_root[field["key"]] = value
                else:
                    target_conf[field["key"]] = value

        if section.get("confidence_key"):
            target_root[section["confidence_key"]] = target_conf

    manip_95 = data["mc_manipulation"].get("confidence_95", {})
    retest_95 = data["mc_retest"].get("confidence_95", {})
    manip_50 = data["mc_manipulation"].get("confidence_50", {})
    retest_50 = data["mc_retest"].get("confidence_50", {})

    if not manip_95 and not retest_95:
        errors["monte_carlo"] = "Debes completar al menos un Monte Carlo 95% (Manipulation o Retest)."
    elif not manip_95:
        warnings.append("Sin datos de Trades Manipulation, el scoring usara solo el MC disponible.")
    elif not retest_95:
        warnings.append("Sin datos de Retest Methods, el scoring usara solo el MC disponible.")

    if not manip_50:
        warnings.append("Monte Carlo Trades Manipulation 50% no fue completado.")
    if not retest_50:
        warnings.append("Monte Carlo Retest Methods 50% no fue completado.")
    if not data["spp"]:
        warnings.append("Sin datos SPP, el análisis usará solo BT y MC.")

    return data, errors, warnings


def build_reference_form_values(entry=None, form=None):
    form_values = {}
    for section in INCUBATION_REFERENCE_SECTIONS:
        section_values = {}
        if form is not None:
            for field in section["fields"]:
                section_values[field["key"]] = form.get(f"{section['key']}_{field['key']}", "")
        elif entry:
            source = _incubation_reference_section_payload(entry, section)
            for field in section["fields"]:
                section_values[field["key"]] = _format_numeric_value(source.get(field["key"]))
        else:
            for field in section["fields"]:
                section_values[field["key"]] = ""
        form_values[section["key"]] = section_values
    return form_values


def reference_sections_for_render(entry=None):
    rendered = []
    for section in INCUBATION_REFERENCE_SECTIONS:
        source = _incubation_reference_section_payload(entry or {}, section)
        has_data = _incubation_reference_has_values(source)
        rendered.append(
            {
                **section,
                "has_data": has_data,
                "status_icon": "✅" if has_data else "⚠️",
                "status_label": "Completo" if has_data else "Pendiente",
            }
        )
    return rendered


def compute_spp_ratios(bt_data, spp_data):
    mapping = {
        "median_net_profit": "net_profit",
        "median_max_dd_pct": "max_dd_pct",
        "median_ret_dd_ratio": "ret_dd_ratio",
        "median_stagnation_days": "stagnation_days",
        "median_sqn_score": "sqn_score",
        "median_avg_trade": "expectancy",
        "median_payout_ratio": "payout_ratio",
    }
    ratios = {}
    for spp_key, bt_key in mapping.items():
        bt_value = _parse_numeric_input(bt_data.get(bt_key))
        spp_value = _parse_numeric_input(spp_data.get(spp_key))
        if bt_value is None or spp_value in (None, 0):
            ratios[spp_key] = None
        else:
            ratios[spp_key] = round((bt_value / spp_value) * 100, 2)
    return ratios


def _incubation_reference_section_payload(entry, section):
    if section["group"] == "backtest":
        return entry.get("backtest", {}) if isinstance(entry, dict) else {}
    if section["group"] == "spp":
        return entry.get("spp", {}) if isinstance(entry, dict) else {}

    group = entry.get(section["group"], {}) if isinstance(entry, dict) else {}
    if not isinstance(group, dict):
        return {}
    if section.get("confidence_key"):
        payload = group.get(section["confidence_key"], {})
        return payload if isinstance(payload, dict) else {}
    return group


def _incubation_reference_has_values(source):
    if not isinstance(source, dict):
        return False
    for value in source.values():
        if value not in (None, "", {}):
            return True
    return False


def checkpoint_for_trades(total_trades):
    if total_trades < 5:
        return "pre_cp1", "Pre-CP1", "pre"
    if total_trades < 20:
        return "cp1", "CP1 (5-20)", "cp1"
    if total_trades < 40:
        return "cp2", "CP2 (20-40)", "cp2"
    return "cp3", "CP3 (40+)", "cp3"


def days_since_first_trade(trades):
    first_dt = None
    for trade in trades:
        ct = trade.get("close_time")
        if not ct:
            continue
        if isinstance(ct, str):
            ct = datetime.fromisoformat(ct)
        if first_dt is None or ct < first_dt:
            first_dt = ct
    if not first_dt:
        return 0
    return max(0, (date.today() - first_dt.date()).days)


def _incubation_load_ea_metrics(ea_name, parsed_data, config):
    if not parsed_data:
        return None, None

    ea_trades = [
        t
        for t in parsed_data.get("closed_trades", [])
        if trade_matches_ea(t, ea_name, config)
    ]

    if not ea_trades:
        return None, []

    from metrics import calculate_ea_metrics

    metrics = calculate_ea_metrics(ea_name, ea_trades, config)
    return metrics, ea_trades


def _incubation_format_metric(value, kind):
    if value is None:
        return "—"
    if kind == "pct":
        return f"{value:.2f}%"
    if kind == "money":
        sign = "+" if value > 0 else ""
        return f"{sign}${value:.2f}"
    if kind == "ratio":
        if value == float("inf"):
            return "∞"
        return f"{value:.2f}"
    if kind == "int":
        return str(int(round(value)))
    if kind == "days":
        return f"{int(round(value))}d"
    return str(value)


def build_distribution_payload(metrics):
    trades = metrics.get("trades", [])
    pnl_list = [t.get("net_pnl", 0) for t in trades]
    streak_data = []
    for i, t in enumerate(trades):
        streak_data.append(
            {
                "index": i + 1,
                "pnl": round(t.get("net_pnl", 0), 2),
                "color": "#4CAF50" if t.get("net_pnl", 0) > 0 else "#FF5252",
            }
        )

    weekday_pnl = [0.0] * 7
    hour_pnl = [0.0] * 24
    heatmap = [[0.0 for _ in range(24)] for _ in range(7)]

    for t in trades:
        ct = t.get("close_time")
        if not ct:
            continue
        if isinstance(ct, str):
            ct = datetime.fromisoformat(ct)
        pnl = float(t.get("net_pnl", 0) or 0)
        weekday_idx = ct.weekday()
        hour_idx = ct.hour
        weekday_pnl[weekday_idx] += pnl
        hour_pnl[hour_idx] += pnl
        heatmap[weekday_idx][hour_idx] += pnl

    weekday_pnl = [round(v, 2) for v in weekday_pnl]
    hour_pnl = [round(v, 2) for v in hour_pnl]
    heatmap = [[round(v, 2) for v in row] for row in heatmap]

    long_list = [t for t in trades if t.get("direction") == "buy"]
    short_list = [t for t in trades if t.get("direction") == "sell"]
    long_short = {
        "long_count": len(long_list),
        "short_count": len(short_list),
        "long_pnl": round(sum(t.get("net_pnl", 0) for t in long_list), 2),
        "short_pnl": round(sum(t.get("net_pnl", 0) for t in short_list), 2),
        "long_wins": sum(1 for t in long_list if t.get("net_pnl", 0) > 0),
        "short_wins": sum(1 for t in short_list if t.get("net_pnl", 0) > 0),
    }

    duration_scatter = [
        {
            "x": round(float(t.get("duration_hours") or 0), 2),
            "y": round(float(t.get("net_pnl") or 0), 2),
            "win": (t.get("net_pnl") or 0) > 0,
        }
        for t in trades
    ]

    return {
        "pnl_list": pnl_list,
        "streak_data": streak_data,
        "weekday_pnl": weekday_pnl,
        "hour_pnl": hour_pnl,
        "weekday_hour_heatmap": {
            "z": heatmap,
            "x": list(range(24)),
            "y": ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"],
        },
        "long_short": long_short,
        "duration_scatter": duration_scatter,
    }


def build_monthly_performance(trades):
    monthly_data = defaultdict(lambda: defaultdict(float))
    monthly_has_data = defaultdict(set)

    for t in trades:
        ct = t.get("close_time")
        if not ct:
            continue
        if isinstance(ct, str):
            ct = datetime.fromisoformat(ct)
        monthly_data[ct.year][ct.month] += t.get("net_pnl", 0)
        monthly_has_data[ct.year].add(ct.month)

    monthly_perf = []
    for year in sorted(monthly_data.keys(), reverse=True):
        months_vals = []
        for mo in range(1, 13):
            if mo in monthly_has_data[year]:
                months_vals.append(round(monthly_data[year][mo], 2))
            else:
                months_vals.append(None)
        ytd = round(sum(monthly_data[year][mo] for mo in monthly_has_data[year]), 2)
        monthly_perf.append({"year": year, "months": months_vals, "ytd": ytd})

    return monthly_perf


def reference_ready(entry):
    mc_manipulation = entry.get("mc_manipulation") or entry.get("monte_carlo") or {}
    mc_retest = entry.get("mc_retest") or {}
    has_mc95 = bool(mc_manipulation.get("confidence_95")) or bool(mc_retest.get("confidence_95"))
    return bool(entry.get("backtest")) and has_mc95


def _incubation_checkpoint_slot(checkpoint_name):
    return {"CP1": "cp1", "CP2": "cp2", "CP3": "cp3"}.get(checkpoint_name)


def _incubation_metric_band_state(live_value, mc95_value, mc50_value, inverse=False):
    if live_value is None or mc95_value is None or mc50_value is None:
        return {"label": "—", "class": "cmp-neutral", "score_band": None}

    low = min(mc95_value, mc50_value)
    high = max(mc95_value, mc50_value)

    if inverse:
        if live_value <= low:
            return {"label": "🟢", "class": "cmp-green", "score_band": "above_mc50"}
        if live_value <= high:
            return {"label": "🟡", "class": "cmp-yellow", "score_band": "in_band"}
        return {"label": "🔴", "class": "cmp-red", "score_band": "below_mc95"}

    if live_value >= high:
        return {"label": "🟢", "class": "cmp-green", "score_band": "above_mc50"}
    if live_value >= low:
        return {"label": "🟡", "class": "cmp-yellow", "score_band": "in_band"}
    return {"label": "🔴", "class": "cmp-red", "score_band": "below_mc95"}


def metric_summary_for_tooltip(evaluation):
    if not evaluation:
        return "NO DATA"

    details = evaluation.get("details", {})
    checkpoint = evaluation.get("current_checkpoint", "")
    if checkpoint == "CP1":
        gates = details.get("gates", {})
        passed = 0
        total = 0
        for key, gate in gates.items():
            if not isinstance(gate, dict):
                continue
            if key == "frequency":
                total += 1
                passed += 1 if gate.get("status") == "OK" else 0
                continue
            if "passed" in gate:
                total += 1
                passed += 1 if gate.get("passed") else 0
        return f"Hard gates: {passed}/{total} passed"
    if checkpoint == "CP2":
        failing = details.get("failing_count", 0)
        return f"Failing metrics: {failing}/7"
    if checkpoint == "CP3":
        cats = details.get("category_scores", {})
        score = evaluation.get("score")
        return (
            f"Score: {score:.2f}/100 | "
            f"Deviation: {cats.get('deviation', {}).get('score', 0):.2f} | "
            f"Risk: {cats.get('risk', {}).get('score', 0):.2f} | "
            f"Coherence: {cats.get('coherence', {}).get('score', 0):.2f} | "
            f"Sample: {cats.get('sample', {}).get('score', 0):.2f}"
        )
    return "PENDING"


def _incubation_sync_checkpoint_store(entry, evaluation):
    checkpoints = entry.get("checkpoints") or {"cp1": None, "cp2": None, "cp3": None}
    slot = _incubation_checkpoint_slot(evaluation.get("current_checkpoint"))
    if slot:
        checkpoints[slot] = evaluation
        entry["checkpoints"] = checkpoints
    entry["last_evaluation"] = evaluation
    return entry


def evaluate_ea(ea_name, parsed_data, config, entry):
    metrics, ea_trades = _incubation_load_ea_metrics(ea_name, parsed_data, config)
    if metrics is None:
        return None

    is_reference_ready = reference_ready(entry)

    if not is_reference_ready:
        return {
            "ea_name": ea_name,
            "metrics": metrics,
            "entry": entry,
            "config": config,
            "reference_ready": False,
            "evaluation": None,
            "trades": ea_trades,
        }

    from incubation_validator import evaluate_incubation

    previous_cp2_result = (entry.get("checkpoints") or {}).get("cp2")
    evaluation = evaluate_incubation(
        ea_name,
        metrics,
        entry,
        previous_cp2_result=previous_cp2_result,
    )

    entry = _incubation_sync_checkpoint_store(entry, evaluation)

    return {
        "ea_name": ea_name,
        "metrics": metrics,
        "entry": entry,
        "config": config,
        "reference_ready": True,
        "evaluation": evaluation,
        "trades": ea_trades,
    }


def current_result_from_entry(entry):
    checkpoints = entry.get("checkpoints") or {}
    for slot in ("cp3", "cp2", "cp1"):
        result = checkpoints.get(slot)
        if result:
            return result
    return entry.get("last_evaluation")


def build_comparison_rows(metrics, entry):
    if not metrics:
        return []

    bt = entry.get("backtest", {}) or {}
    mc_manipulation = entry.get("mc_manipulation") or entry.get("monte_carlo") or {}
    mc_retest = entry.get("mc_retest") or {}
    spp = entry.get("spp", {}) or {}

    mc95_manip = (mc_manipulation or {}).get("confidence_95", {}) or {}
    mc50_manip = (mc_manipulation or {}).get("confidence_50", {}) or {}
    mc95_retest = (mc_retest or {}).get("confidence_95", {}) or {}
    mc50_retest = (mc_retest or {}).get("confidence_50", {}) or {}

    worst95 = get_worst_case_mc(mc_manipulation, mc_retest, "confidence_95")
    worst50 = get_worst_case_mc(mc_manipulation, mc_retest, "confidence_50")
    current_result = current_result_from_entry(entry) or {}
    mc_source = current_result.get("mc_source", {}) or {}
    dominant_95 = mc_source.get("dominant_metrics", {}) or {}
    dominant_50 = mc_source.get("dominant_metrics_50", {}) or {}

    specs = [
        ("Win Rate", "pct", False, "win_rate", None, True),
        ("Profit Factor", "ratio", False, "profit_factor", None, True),
        ("Expectancy", "money", False, "expectancy", None, True),
        ("Max DD%", "pct", True, "max_dd_pct", "median_max_dd_pct", False),
        ("Max Consec Losses", "int", True, "max_consec_losses", None, False),
        ("Payout Ratio", "ratio", False, "payout_ratio", "median_payout_ratio", True),
        ("SQN Score", "ratio", False, "sqn_score", "median_sqn_score", True),
        ("Stagnation", "days", True, "stagnation_days", "median_stagnation_days", False),
        ("Ret/DD", "ratio", False, "ret_dd_ratio", "median_ret_dd_ratio", True),
    ]

    rows = []
    for metric, kind, inverse, ref_key, spp_key, requires_spp in specs:
        live_value = metrics.get(
            {
                "Ret/DD": "ret_dd",
                "SQN Score": "sqn",
            }.get(metric, ref_key if metric != "Expectancy" else "expectancy")
        )
        # calculate_ea_metrics pre-formats infinite ratios (profit_factor,
        # payout_ratio) as the string "∞" for display. Coerce it back to a
        # real float here so comparisons/formatting below don't crash; the
        # "∞" display is restored by _incubation_format_metric.
        live_value = _safe_float(live_value, live_value)
        bt_value = bt.get(ref_key if metric != "Ret/DD" else "ret_dd_ratio")
        if metric == "Expectancy":
            bt_value = bt.get("expectancy")
        if metric == "SQN Score":
            bt_value = bt.get("sqn_score")
        if metric == "Ret/DD":
            bt_value = bt.get("ret_dd_ratio")

        worst95_value = worst95.get(ref_key)
        worst50_value = worst50.get(ref_key)
        if metric == "Ret/DD":
            worst95_value = worst95.get("ret_dd_ratio")
            worst50_value = worst50.get("ret_dd_ratio")

        state = _incubation_metric_band_state(live_value, worst95_value, worst50_value, inverse)
        details = {
            "mc_manip_95": _incubation_format_metric(mc95_manip.get(ref_key), kind),
            "mc_retest_95": _incubation_format_metric(mc95_retest.get(ref_key), kind),
            "mc_manip_50": _incubation_format_metric(mc50_manip.get(ref_key), kind),
            "mc_retest_50": _incubation_format_metric(mc50_retest.get(ref_key), kind),
            "dominant_95": dominant_95.get(ref_key, "manipulation"),
            "dominant_50": dominant_50.get(ref_key, "manipulation"),
        }
        if metric == "Ret/DD":
            details = {
                "mc_manip_95": _incubation_format_metric(mc95_manip.get("ret_dd_ratio"), kind),
                "mc_retest_95": _incubation_format_metric(mc95_retest.get("ret_dd_ratio"), kind),
                "mc_manip_50": _incubation_format_metric(mc50_manip.get("ret_dd_ratio"), kind),
                "mc_retest_50": _incubation_format_metric(mc50_retest.get("ret_dd_ratio"), kind),
                "dominant_95": dominant_95.get("ret_dd_ratio", "manipulation"),
                "dominant_50": dominant_50.get("ret_dd_ratio", "manipulation"),
            }

        spp_value = None
        if metric == "Expectancy":
            spp_value = spp.get("median_avg_trade")
        elif metric == "Max DD%":
            spp_value = spp.get("median_max_dd_pct")
        elif metric == "Payout Ratio":
            spp_value = spp.get("median_payout_ratio")
        elif metric == "SQN Score":
            spp_value = spp.get("median_sqn_score")
        elif metric == "Stagnation":
            spp_value = spp.get("median_stagnation_days")
        elif metric == "Ret/DD":
            spp_value = spp.get("median_ret_dd_ratio")

        rows.append(
            {
                "metric": metric,
                "live": _incubation_format_metric(live_value, kind),
                "bt": _incubation_format_metric(bt_value, kind),
                "worst95": _incubation_format_metric(worst95_value, kind),
                "worst50": _incubation_format_metric(worst50_value, kind),
                "spp": _incubation_format_metric(spp_value, kind),
                "state": state,
                "score_band": state["score_band"],
                "details": details,
            }
        )

    # Fix expectancy/spp mapping and general SPP values cleanly.
    for row in rows:
        if row["metric"] == "Expectancy":
            row["spp"] = _incubation_format_metric(spp.get("median_avg_trade"), "money")
        elif row["metric"] == "Max DD%":
            row["spp"] = _incubation_format_metric(spp.get("median_max_dd_pct"), "pct")
        elif row["metric"] == "Payout Ratio":
            row["spp"] = _incubation_format_metric(spp.get("median_payout_ratio"), "ratio")
        elif row["metric"] == "SQN Score":
            row["spp"] = _incubation_format_metric(spp.get("median_sqn_score"), "ratio")
        elif row["metric"] == "Stagnation":
            row["spp"] = _incubation_format_metric(spp.get("median_stagnation_days"), "days")
        elif row["metric"] == "Ret/DD":
            row["spp"] = _incubation_format_metric(spp.get("median_ret_dd_ratio"), "ratio")
        else:
            row["spp"] = "—"

    return rows


def build_verdict_card(evaluation):
    if not evaluation:
        return {
            "checkpoint": "PRE_CP1",
            "score": None,
            "verdict": "NO DATA",
            "verdict_class": "verdict-no-data",
            "summary": "Cargar datos de referencia BT/MC/SPP",
            "hard_gates": [],
            "reason": "No reference data",
            "verdict_reading": {
                "icon": "ℹ",
                "title": "Lectura del veredicto",
                "message": "Faltan datos de referencia para explicar el estado de esta estrategia.",
                "tone": "neutral",
            },
            "failing_metrics": [],
        }

    details = evaluation.get("details", {})
    checkpoint = evaluation.get("current_checkpoint", "PENDING")
    verdict = evaluation.get("verdict", "PENDING")
    days_incubating = evaluation.get("days_incubating", 0) or 0
    verdict_class = {
        "APROBAR": "verdict-approve",
        "CONTINUAR": "verdict-continue",
        "OBSERVAR": "verdict-observe",
        "ELIMINAR": "verdict-eliminate",
        "PENDING": "verdict-pending",
    }.get(verdict, "verdict-pending")

    hard_gates = []
    for key, gate in (details.get("gates") or {}).items():
        if not isinstance(gate, dict) or "passed" not in gate:
            if key != "frequency":
                continue
        hard_gates.append(
            {
                "key": key,
                "passed": bool(gate.get("passed")) if "passed" in gate else gate.get("status") == "OK",
                "status": gate.get("status") if "status" in gate else ("PASS" if gate.get("passed") else "FAIL"),
                "value": gate,
            }
        )

    failing_metrics = []
    if checkpoint == "CP2":
        for key, value in (details.get("metrics_evaluation") or {}).items():
            if value.get("status") == "failing":
                failing_metrics.append(key)
    elif checkpoint == "CP3":
        for key, value in (details.get("metrics_scores") or {}).items():
            if isinstance(value, dict) and _incubation_metric_band_state(
                value.get("live"),
                value.get("mc95"),
                value.get("mc50"),
                key in {"max_dd_pct", "max_consec_losses", "stagnation_days"},
            )["score_band"] == "below_mc95":
                failing_metrics.append(key)

    verdict_reading = _incubation_verdict_reading(evaluation, hard_gates, failing_metrics)

    if verdict == "ELIMINAR":
        if details.get("freq_deadline"):
            bt_mo = details.get("bt_monthly", "?")
            act_mo = details.get("actual_monthly", "?")
            ddl = details.get("deadline_days", "?")
            reason = f"Frecuencia perdida: {act_mo}/mes vs BT {bt_mo}/mes — {days_incubating}d > límite {ddl}d"
        elif hard_gates:
            failed = [g["key"] for g in hard_gates if not g["passed"]]
            reason = "Hard gates: " + ", ".join(failed) if failed else "Below reference range"
        elif failing_metrics:
            reason = "Failing metrics: " + ", ".join(failing_metrics)
        else:
            reason = "Score below threshold"
    elif verdict == "OBSERVAR":
        reason = "Failing metrics: " + ", ".join(failing_metrics) if failing_metrics else "Needs monitoring"
    elif verdict == "APROBAR":
        reason = "Checkpoint approved"
    elif verdict == "CONTINUAR":
        reason = "Hard gates passed"
    else:
        reason = "Pending evaluation"

    # Build freq deadline info for PRE_CP1 display
    freq_deadline_info = None
    if checkpoint == "PRE_CP1":
        deadline_days = details.get("deadline_days")
        bt_monthly = details.get("bt_monthly")
        actual_monthly = details.get("actual_monthly", 0.0)
        if deadline_days is not None:
            days_remaining = max(deadline_days - days_incubating, 0)
            deadline_pct = min(int(days_incubating / deadline_days * 100), 100) if deadline_days else 0
            freq_deadline_info = {
                "deadline_days": deadline_days,
                "days_incubating": days_incubating,
                "days_remaining": days_remaining,
                "deadline_pct": deadline_pct,
                "bt_monthly": bt_monthly,
                "actual_monthly": actual_monthly,
                "exceeded": details.get("freq_deadline", False),
            }

    return {
        "checkpoint": checkpoint,
        "score": evaluation.get("score"),
        "verdict": verdict,
        "verdict_class": verdict_class,
        "summary": metric_summary_for_tooltip(evaluation),
        "hard_gates": hard_gates,
        "reason": reason,
        "verdict_reading": verdict_reading,
        "failing_metrics": failing_metrics,
        "escalation_from_cp2": bool(details.get("escalation_from_cp2")),
        "category_scores": details.get("category_scores", {}),
        "freq_deadline_info": freq_deadline_info,
    }


def _incubation_fmt_pct(value):
    if isinstance(value, (int, float)):
        return f"{value:.0f}%" if float(value).is_integer() else f"{value:.2f}%"
    return "—"


def _incubation_verdict_reading(evaluation, hard_gates, failing_metrics):
    details = evaluation.get("details", {}) if evaluation else {}
    verdict = evaluation.get("verdict", "PENDING") if evaluation else "PENDING"
    checkpoint = evaluation.get("current_checkpoint", "PENDING") if evaluation else "PENDING"
    total_trades = evaluation.get("total_trades") or details.get("total_trades") or 0

    base = {
        "icon": "ℹ",
        "title": "Lectura del veredicto",
        "message": "Estado pendiente de interpretación.",
        "tone": "neutral",
    }

    if verdict == "ELIMINAR":
        base.update({"icon": "✕", "title": "Motivo principal de eliminación", "tone": "danger"})

        if details.get("freq_deadline"):
            base["message"] = "No generó suficientes operaciones dentro del tiempo esperado para esta etapa."
            return base

        failed = [gate for gate in hard_gates if not gate.get("passed")]
        failed_keys = [gate.get("key") for gate in failed]

        if "win_rate_binomial" in failed_keys:
            gate = next((g.get("value", {}) for g in failed if g.get("key") == "win_rate_binomial"), {})
            live_wr = gate.get("live_wr")
            n = gate.get("n") or total_trades
            base["message"] = f"Ganó muy pocas operaciones para esta etapa: solo {_incubation_fmt_pct(live_wr)} de aciertos en {n} trades."
            return base

        if "dd_extreme" in failed_keys:
            gate = next((g.get("value", {}) for g in failed if g.get("key") == "dd_extreme"), {})
            live = _incubation_fmt_pct(gate.get("live_value"))
            threshold = _incubation_fmt_pct(gate.get("threshold"))
            base["message"] = f"El drawdown superó el límite esperado para esta etapa: {live} frente a un límite de {threshold}."
            return base

        if "max_consec_losses" in failed_keys:
            gate = next((g.get("value", {}) for g in failed if g.get("key") == "max_consec_losses"), {})
            live = gate.get("live_value", "—")
            limit = gate.get("mc95_value", "—")
            base["message"] = f"Acumuló demasiadas pérdidas consecutivas para esta etapa: {live} seguidas frente a un límite de {limit}."
            return base

        if failing_metrics:
            base["message"] = _incubation_metric_reason_message(failing_metrics[0], eliminated=True)
            return base

        base["message"] = "El resultado quedó por debajo del mínimo esperado para esta etapa."
        return base

    if verdict == "OBSERVAR":
        base.update({"icon": "⚠", "title": "Motivo principal de observación", "tone": "warning"})
        if failing_metrics:
            base["message"] = _incubation_metric_reason_message(failing_metrics[0], eliminated=False)
        else:
            base["message"] = "La estrategia muestra señales de deterioro, pero todavía no suficientes para eliminarla."
        return base

    if verdict == "APROBAR":
        base.update({"icon": "✓", "title": "Motivo principal de aprobación", "tone": "success"})
        base["message"] = "Completó la incubación manteniéndose dentro de los parámetros esperados."
        return base

    if verdict == "CONTINUAR":
        base.update({"icon": "✓", "title": "Motivo principal para continuar", "tone": "success"})
        if checkpoint == "CP1":
            base["message"] = "Pasó los controles principales y necesita más trades para confirmar su comportamiento."
        elif checkpoint == "CP2":
            base["message"] = "Se mantiene dentro de las bandas esperadas y debe seguir acumulando evidencia."
        else:
            base["message"] = "La estrategia sigue dentro de los límites esperados para esta etapa."
        return base

    if verdict == "PENDING":
        base.update({"icon": "…", "title": "Motivo del estado pendiente", "tone": "neutral"})
        base["message"] = "Todavía está acumulando operaciones; necesita más datos antes de tomar una decisión."
        return base

    return base


def _incubation_metric_reason_message(metric_key, eliminated=False):
    severity = "fuera de lo esperado" if eliminated else "por debajo de lo ideal"
    messages = {
        "win_rate": f"Ganó menos operaciones de las esperadas y quedó {severity}.",
        "profit_factor": f"La relación entre ganancias y pérdidas quedó {severity}.",
        "expectancy": f"El resultado promedio por operación quedó {severity}.",
        "avg_trade": f"El beneficio promedio por trade quedó {severity}.",
        "max_dd_pct": f"El drawdown quedó {severity} para esta etapa.",
        "max_consec_losses": f"La racha de pérdidas consecutivas quedó {severity}.",
        "payout_ratio": f"La relación entre trades ganadores y perdedores quedó {severity}.",
        "ret_dd_ratio": f"La recompensa frente al drawdown quedó {severity}.",
        "stagnation_days": f"Pasó demasiado tiempo sin recuperar un nuevo máximo de equity.",
        "frequency": f"La frecuencia de operaciones quedó {severity}.",
    }
    return messages.get(metric_key, f"Una métrica clave quedó {severity}.")


def build_timeline_from_entry(entry):
    checkpoints = entry.get("checkpoints") or {}
    timeline = []
    for key, label in [("cp1", "CP1"), ("cp2", "CP2"), ("cp3", "CP3")]:
        cp = checkpoints.get(key)
        if cp:
            details = cp.get("details") if isinstance(cp.get("details"), dict) else {}
            timeline.append(
                {
                    "key": key,
                    "label": label,
                    "date": cp.get("timestamp") or details.get("timestamp", "—"),
                    "trades": cp.get("total_trades") or details.get("total_trades", "—"),
                    "score": cp.get("score"),
                    "verdict": cp.get("verdict", "PENDING"),
                    "verdict_class": {
                        "APROBAR": "verdict-approve",
                        "CONTINUAR": "verdict-continue",
                        "OBSERVAR": "verdict-observe",
                        "ELIMINAR": "verdict-eliminate",
                        "PENDING": "verdict-pending",
                    }.get(cp.get("verdict", "PENDING"), "verdict-pending"),
                    "details": details.get("reason")
                    or details.get("summary")
                    or "Checkpoint evaluado",
                }
            )
        else:
            timeline.append(
                {
                    "key": key,
                    "label": label,
                    "date": "—",
                    "trades": "—",
                    "score": "—",
                    "verdict": "Pending",
                    "verdict_class": "verdict-pending",
                    "details": "Checkpoint pendiente",
                }
            )
    return timeline
