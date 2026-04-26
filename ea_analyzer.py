"""
ea_analyzer.py - EA Analyzer & Validator
Flask web application for analyzing MetaTrader 5 Expert Advisor performance.

Usage: python ea_analyzer.py
Opens http://localhost:5000 in the default browser.
"""

import glob
import json
import os
import threading
import time
import uuid
import webbrowser
from datetime import date, datetime, timedelta
from urllib.parse import quote, unquote

from flask import (
    Flask,
    jsonify,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from validator import (
    _safe_float,
    calculate_validator_score,
    get_all_validator_results,
    load_validator_store,
    save_validator_store,
    timeframe_to_hours,
)

# ─────────────────────────────────────────────────────────────────────────────
# Setup
# ─────────────────────────────────────────────────────────────────────────────

APP_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(APP_DIR, "templates")
STATIC_DIR = os.path.join(APP_DIR, "static")
UPLOAD_FOLDER = os.path.join(APP_DIR, "uploads")
CACHE_DIR = os.path.join(APP_DIR, "runtime_cache")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
SECRET_KEY_PATH = os.path.join(APP_DIR, ".secret_key")

app = Flask(__name__, template_folder=TEMPLATES_DIR, static_folder=STATIC_DIR)

# Persistent secret key (survives restarts)
if os.path.exists(SECRET_KEY_PATH):
    with open(SECRET_KEY_PATH, "rb") as f:
        app.secret_key = f.read()
else:
    key = os.urandom(24)
    with open(SECRET_KEY_PATH, "wb") as f:
        f.write(key)
    app.secret_key = key

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(os.path.join(APP_DIR, "test_data"), exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# Metrics cache (in-memory, por sesión, TTL 120 segundos)
# Evita recalcular calculate_all_metrics() en cada llamada API del dashboard.
# ─────────────────────────────────────────────────────────────────────────────

_metrics_cache: dict = {}  # { cache_key: {"ts": float, "result": dict} }
_METRICS_TTL = 120  # segundos
LIVE_CACHE_PREFIX = "cache_"
ANALYSIS_MODES = {
    "live": "Live Validation",
    "incubation": "Incubation Screening",
}
INCUBATION_CACHE_PREFIX = "incubation_cache_"
INCUBATION_STORE_PATH = os.path.join(APP_DIR, "incubation_store.json")
INCUBATION_CONFIG_PATH = os.path.join(APP_DIR, "incubation_config.json")


def _get_metrics_cached(parsed_data: dict, config: dict) -> dict:
    """
    Devuelve calculate_all_metrics() desde cache si sigue vigente.
    La clave es el cache_key de sesión + hash ligero del config.
    Se invalida automáticamente al subir un nuevo archivo (nuevo cache_key).
    """
    from metrics import calculate_all_metrics

    cache_key = session.get("cache_key", "__no_key__")
    now = time.time()

    entry = _metrics_cache.get(cache_key)
    if entry and (now - entry["ts"]) < _METRICS_TTL:
        return entry["result"]

    result = calculate_all_metrics(parsed_data, config)
    _metrics_cache[cache_key] = {"ts": now, "result": result}

    # Limpiar entradas viejas (evitar memoria ilimitada)
    stale = [k for k, v in _metrics_cache.items() if now - v["ts"] > _METRICS_TTL * 10]
    for k in stale:
        del _metrics_cache[k]

    return result


def invalidate_metrics_cache():
    """Llamar tras subir un nuevo archivo para forzar recálculo."""
    cache_key = session.get("cache_key", "__no_key__")
    _metrics_cache.pop(cache_key, None)


# ─────────────────────────────────────────────────────────────────────────────
# Config helpers
# ─────────────────────────────────────────────────────────────────────────────


def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"mappings": {}, "last_file": None, "last_updated": None}


def _normalize_analysis_mode(value):
    mode = str(value or "live").strip().lower()
    return mode if mode in ANALYSIS_MODES else "live"


@app.context_processor
def inject_analysis_mode():
    mode = _normalize_analysis_mode(session.get("analysis_mode"))
    switch_target = "incubation" if mode == "live" else "live"
    return {
        "analysis_mode": mode,
        "analysis_mode_label": ANALYSIS_MODES[mode],
        "analysis_mode_badge": f"{ANALYSIS_MODES[mode].upper()} MODE",
        "analysis_mode_switch_target": switch_target,
        "analysis_mode_switch_label": ANALYSIS_MODES[switch_target],
    }


def save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def load_incubation_store():
    if os.path.exists(INCUBATION_STORE_PATH):
        try:
            with open(INCUBATION_STORE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_incubation_store(data):
    with open(INCUBATION_STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_incubation_config():
    if os.path.exists(INCUBATION_CONFIG_PATH):
        try:
            with open(INCUBATION_CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"mappings": {}, "last_file": None, "last_updated": None}


def save_incubation_config(data):
    with open(INCUBATION_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def migrate_incubation_store():
    """Migrate legacy single-MC incubation data to dual MC structure."""
    store = load_incubation_store()
    modified = False

    for ea_name, data in store.items():
        if not isinstance(data, dict):
            continue

        if data.get("monte_carlo") and not data.get("mc_manipulation"):
            data["mc_manipulation"] = data["monte_carlo"]
            data["mc_retest"] = None
            data["monte_carlo"] = None
            data["checkpoints"] = {"cp1": None, "cp2": None, "cp3": None}
            modified = True

        if "mc_manipulation" not in data:
            data["mc_manipulation"] = None
        if "mc_retest" not in data:
            data["mc_retest"] = None
        if "monte_carlo" not in data:
            data["monte_carlo"] = None

    if modified:
        save_incubation_store(store)


migrate_incubation_store()


# ─────────────────────────────────────────────────────────────────────────────
# Cache helpers
# ─────────────────────────────────────────────────────────────────────────────


def _serialize_parsed_data(data):
    """Convert datetime objects to ISO strings for JSON serialization."""
    import copy

    d = copy.deepcopy(data)
    for trade in d.get("closed_trades", []):
        for k in ("open_time", "close_time"):
            val = trade.get(k)
            if isinstance(val, datetime):
                trade[k] = val.isoformat()
    for pos in d.get("open_positions", []):
        val = pos.get("open_time")
        if isinstance(val, datetime):
            pos["open_time"] = val.isoformat()
    return d


def _cache_file_path(cache_key, prefix):
    return os.path.join(CACHE_DIR, f"{prefix}{cache_key}.json")


def _legacy_cache_file_path(cache_key, prefix):
    return os.path.join(APP_DIR, f"{prefix}{cache_key}.json")


def _resolve_cache_path(cache_key, prefix):
    if not cache_key:
        return None

    cache_path = _cache_file_path(cache_key, prefix)
    if os.path.exists(cache_path):
        return cache_path

    legacy_path = _legacy_cache_file_path(cache_key, prefix)
    if not os.path.exists(legacy_path):
        return cache_path

    try:
        os.replace(legacy_path, cache_path)
        return cache_path
    except OSError:
        return legacy_path


def _delete_cache_file(cache_key, prefix):
    if not cache_key:
        return

    for cache_path in {
        _cache_file_path(cache_key, prefix),
        _legacy_cache_file_path(cache_key, prefix),
    }:
        try:
            if os.path.exists(cache_path):
                os.remove(cache_path)
        except OSError:
            pass


def save_cache(data):
    """Save parsed data to a cache file. Returns the cache key."""
    cache_key = str(uuid.uuid4())
    cache_path = _cache_file_path(cache_key, LIVE_CACHE_PREFIX)
    serialized = _serialize_parsed_data(data)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(serialized, f, default=str)
    return cache_key


def load_cache(cache_key):
    """Load cached parsed data. Returns dict or None."""
    if not cache_key:
        return None
    cache_path = _resolve_cache_path(cache_key, LIVE_CACHE_PREFIX)
    if not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_incubation_cache(data):
    """Save incubation parsed data to a separate cache file."""
    cache_key = str(uuid.uuid4())
    cache_path = _cache_file_path(cache_key, INCUBATION_CACHE_PREFIX)
    serialized = _serialize_parsed_data(data)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(serialized, f, default=str)
    return cache_key


def load_incubation_cache(cache_key):
    """Load incubation cached parsed data. Returns dict or None."""
    if not cache_key:
        return None
    cache_path = _resolve_cache_path(cache_key, INCUBATION_CACHE_PREFIX)
    if not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def cleanup_old_caches():
    """Delete cache files older than 2 hours."""
    patterns = [
        os.path.join(CACHE_DIR, f"{LIVE_CACHE_PREFIX}*.json"),
        os.path.join(CACHE_DIR, f"{INCUBATION_CACHE_PREFIX}*.json"),
        os.path.join(APP_DIR, f"{LIVE_CACHE_PREFIX}*.json"),
        os.path.join(APP_DIR, f"{INCUBATION_CACHE_PREFIX}*.json"),
    ]
    for pattern in patterns:
        for f in glob.glob(pattern):
            try:
                if time.time() - os.path.getmtime(f) > 7200:
                    os.remove(f)
            except OSError:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# Display helpers
# ─────────────────────────────────────────────────────────────────────────────


def get_display_label(ea_name, config):
    mapping = config.get("mappings", {}).get(ea_name, {})
    magic = mapping.get("magic")
    alias = mapping.get("alias", "") or ea_name
    return f"{magic} - {alias}" if magic else alias


def _normalize_trade_key(value):
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _trade_matches_ea(trade, ea_name, config=None):
    comment = trade.get("comment", "")
    if _normalize_trade_key(comment) == _normalize_trade_key(ea_name):
        return True

    if config:
        mapping = config.get("mappings", {}).get(ea_name, {})
        alias = mapping.get("alias", "")
        magic = mapping.get("magic")
        if alias and _normalize_trade_key(comment) == _normalize_trade_key(alias):
            return True
        if magic is not None and _normalize_trade_key(comment) == _normalize_trade_key(magic):
            return True
    return False


def build_sidebar_eas(parsed_data, config, active_ea=None):
    sidebar_eas = []
    mappings = config.get("mappings", {})
    for ea_name in parsed_data.get("ea_names", []):
        # Skip inactive EAs
        if not mappings.get(ea_name, {}).get("active", True):
            continue
        sidebar_eas.append(
            {
                "name": ea_name,
                "label": get_display_label(ea_name, config),
                "url": url_for("strategy", name=quote(ea_name, safe="")),
                "active": (ea_name == active_ea),
            }
        )
    return sidebar_eas


def get_parsed_data():
    """Get parsed data from session cache, or None."""
    cache_key = session.get("cache_key")
    return load_cache(cache_key)


def get_incubation_parsed_data():
    """Get parsed data from the incubation session cache, or None."""
    cache_key = session.get("incubation_cache_key")
    return load_incubation_cache(cache_key)


def build_mapping_rows(parsed_data, config):
    trades = parsed_data.get("closed_trades", [])
    rows = []

    for ea_name in parsed_data.get("ea_names", []):
        ea_trades = [t for t in trades if _trade_matches_ea(t, ea_name, config)]
        existing = config.get("mappings", {}).get(ea_name, {})

        symbols = list(set(t["symbol"] for t in ea_trades if t.get("symbol")))
        instrument = (
            symbols[0]
            if len(symbols) == 1
            else (", ".join(sorted(symbols)) if symbols else "")
        )

        rows.append(
            {
                "name": ea_name,
                "alias": existing.get("alias", ""),
                "instrument": existing.get("instrument", instrument),
                "trade_count": len(ea_trades),
                "magic": existing.get("magic", ""),
                "capital": existing.get("capital", 5000),
                "active": existing.get("active", True),
                "is_new": ea_name not in config.get("mappings", {}),
            }
        )

    return rows


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


def _parse_reference_form(form):
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


def _build_reference_form_values(entry=None, form=None):
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


def _incubation_reference_sections_for_render(entry=None):
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


def _compute_spp_ratios(bt_data, spp_data):
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


def _incubation_reference_section_values(source, section):
    values = {}
    for field in section["fields"]:
        if field.get("store_at") == "group":
            values[field["key"]] = _format_numeric_value(source.get(field["key"]))
        else:
            values[field["key"]] = _format_numeric_value(source.get(field["key"]))
    return values


def _incubation_reference_has_values(source):
    if not isinstance(source, dict):
        return False
    for value in source.values():
        if value not in (None, "", {}):
            return True
    return False


def _require_incubation_mode():
    if _normalize_analysis_mode(session.get("analysis_mode")) != "incubation":
        flash("Seleccione modo Incubation primero", "warn")
        return redirect(url_for("index"))
    return None


def _mode_home(mode):
    if mode == "incubation":
        return url_for("incubation_dashboard") if get_incubation_parsed_data() else url_for("index")
    return url_for("dashboard") if get_parsed_data() else url_for("index")


def _require_live_mode(redirect_endpoint=None):
    if _normalize_analysis_mode(session.get("analysis_mode")) != "live":
        flash("Seleccione modo Live primero", "warn")
        if redirect_endpoint:
            return redirect(url_for(redirect_endpoint))
        return redirect(_mode_home("incubation"))
    return None


def _incubation_checkpoint_for_trades(total_trades):
    if total_trades < 5:
        return "pre_cp1", "Pre-CP1", "pre"
    if total_trades < 20:
        return "cp1", "CP1 (5-20)", "cp1"
    if total_trades < 40:
        return "cp2", "CP2 (20-40)", "cp2"
    return "cp3", "CP3 (40+)", "cp3"


def _incubation_days_since_first_trade(trades):
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


def _build_incubation_dashboard():
    parsed_data = get_incubation_parsed_data()
    if not parsed_data:
        return None

    config = load_incubation_config()
    store = load_incubation_store()
    trades = parsed_data.get("closed_trades", [])

    rows = []
    pending_bt_mc = 0
    eliminar_count = 0
    aprobar_count = 0
    observar_count = 0
    continuar_count = 0
    pending_count = 0
    active_mappings = config.get("mappings", {})

    for ea_name, mapping in active_mappings.items():
        if not mapping.get("active", True):
            continue

        entry = store.get(ea_name, {})
        evaluation_bundle = _incubation_evaluate_ea(ea_name)
        metrics = evaluation_bundle["metrics"] if evaluation_bundle else None
        evaluation = evaluation_bundle["evaluation"] if evaluation_bundle else None
        total_trades = int(metrics.get("total_trades") or 0) if metrics else 0
        days = _incubation_days_since_first_trade(evaluation_bundle["trades"] if evaluation_bundle else [])
        checkpoint_key, checkpoint_label, checkpoint_class = _incubation_checkpoint_for_trades(total_trades)

        has_reference = evaluation_bundle["reference_ready"] if evaluation_bundle else False
        if not has_reference:
            pending_bt_mc += 1
            pending_count += 1

        verdict = "NO DATA"
        score_display = "—"
        tooltip = "Cargar datos de referencia BT/MC/SPP"
        status_label = "NO DATA"
        status_class = "verdict-no-data"
        url = url_for("incubation_reference_data")
        if has_reference and evaluation:
            verdict = evaluation.get("verdict", "PENDING")
            score = evaluation.get("score")
            if score is not None:
                score_display = f"{score:.2f}" if isinstance(score, (int, float)) else str(score)
            tooltip = _incubation_metric_summary_for_tooltip(evaluation)
            url = url_for("incubation_strategy", ea_name=ea_name)
            cp = evaluation.get("current_checkpoint", "")
            details = evaluation.get("details", {})
            if cp == "PRE_CP1":
                status_label = "Esperando trades"
                status_class = "verdict-pending"
            elif cp == "CP1":
                hg = details.get("gates", {})
                gate_keys = ["dd_extreme", "win_rate_binomial", "max_consec_losses", "frequency"]
                passed = sum(1 for k in gate_keys if hg.get(k, {}).get("passed"))
                status_label = f"Gates {passed}/{len(gate_keys)}"
                status_class = "verdict-continue" if verdict == "CONTINUAR" else "verdict-eliminate"
            elif cp == "CP2":
                failing_count = details.get("failing_count") or 0
                status_label = f"{failing_count} métricas fuera" if failing_count else "Bandas MC OK"
                status_class = "verdict-observe" if verdict == "OBSERVAR" else ("verdict-eliminate" if verdict == "ELIMINAR" else "verdict-continue")
            elif cp == "CP3":
                status_label = f"Score {score_display}"
                status_class = {
                    "APROBAR": "verdict-approve",
                    "OBSERVAR": "verdict-observe",
                    "ELIMINAR": "verdict-eliminate",
                }.get(verdict, "verdict-pending")
            else:
                status_label = "Evaluado"
                status_class = "verdict-pending"

            if verdict == "ELIMINAR":
                eliminar_count += 1
            elif verdict == "APROBAR":
                aprobar_count += 1
            elif verdict == "OBSERVAR":
                observar_count += 1
            elif verdict == "CONTINUAR":
                continuar_count += 1
            elif verdict == "PENDING":
                pending_count += 1

        if evaluation:
            checkpoint_record = _incubation_current_result_from_entry(evaluation_bundle["entry"])
            if checkpoint_record:
                score_display = (
                    f"{checkpoint_record.get('score'):.2f}"
                    if isinstance(checkpoint_record.get("score"), (int, float))
                    else score_display
                )

        rows.append(
            {
                "name": ea_name,
                "label": mapping.get("alias", "") or ea_name,
                "trades": total_trades,
                "days": days,
                "checkpoint_key": checkpoint_key,
                "checkpoint_label": checkpoint_label,
                "checkpoint_class": checkpoint_class,
                "status_class": status_class,
                "status_label": status_label,
                "score": score_display,
                "verdict": verdict,
                "verdict_class": {
                    "APROBAR": "verdict-approve",
                    "CONTINUAR": "verdict-continue",
                    "OBSERVAR": "verdict-observe",
                    "ELIMINAR": "verdict-eliminate",
                    "PENDING": "verdict-pending",
                    "NO DATA": "verdict-no-data",
                }.get(verdict, "verdict-pending"),
                "has_reference": has_reference,
                "url": url,
                "tooltip": tooltip,
                "no_data": not has_reference,
            }
        )

    return {
        "rows": rows,
        "total_active": len(rows),
        "pending_bt_mc": pending_bt_mc,
        "eliminar_count": eliminar_count,
        "aprobar_count": aprobar_count,
        "observar_count": observar_count,
        "continuar_count": continuar_count,
        "pending_count": pending_count,
    }


def _incubation_load_ea_metrics(ea_name):
    parsed_data = get_incubation_parsed_data()
    if not parsed_data:
        return None, None, None

    config = load_incubation_config()
    ea_trades = [
        t
        for t in parsed_data.get("closed_trades", [])
        if _trade_matches_ea(t, ea_name, config)
    ]

    if not ea_trades:
        return None, config, []

    from metrics import calculate_ea_metrics

    metrics = calculate_ea_metrics(ea_name, ea_trades, config)
    return metrics, config, ea_trades


def _incubation_metric_value(source: dict, key: str):
    return source.get(key) if isinstance(source, dict) else None


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


def _incubation_normalize_metric(value):
    if value in (None, "", "—"):
        return None
    if value == "∞" or value == float("inf"):
        return float("inf")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _incubation_reference_sections(entry):
    bt = entry.get("backtest", {}) or {}
    mc95 = entry.get("monte_carlo", {}).get("confidence_95", {}) or {}
    mc50 = entry.get("monte_carlo", {}).get("confidence_50", {}) or {}
    spp = entry.get("spp", {}) or {}

    rows = [
        {
            "metric": "Win Rate",
            "kind": "pct",
            "inverse": False,
            "incubation": None,
            "bt": bt.get("win_rate"),
            "mc95": mc95.get("win_rate"),
            "mc50": mc50.get("win_rate"),
            "spp": None,
        },
        {
            "metric": "Profit Factor",
            "kind": "ratio",
            "inverse": False,
            "incubation": None,
            "bt": bt.get("profit_factor"),
            "mc95": mc95.get("profit_factor"),
            "mc50": mc50.get("profit_factor"),
            "spp": None,
        },
        {
            "metric": "Expectancy",
            "kind": "money",
            "inverse": False,
            "incubation": None,
            "bt": bt.get("expectancy"),
            "mc95": mc95.get("expectancy"),
            "mc50": mc50.get("expectancy"),
            "spp": spp.get("median_avg_trade"),
        },
        {
            "metric": "Max DD%",
            "kind": "pct",
            "inverse": True,
            "incubation": None,
            "bt": bt.get("max_dd_pct"),
            "mc95": mc95.get("max_dd_pct"),
            "mc50": mc50.get("max_dd_pct"),
            "spp": spp.get("median_max_dd_pct"),
        },
        {
            "metric": "Max Consec Losses",
            "kind": "int",
            "inverse": True,
            "incubation": None,
            "bt": bt.get("max_consec_losses"),
            "mc95": mc95.get("max_consec_losses"),
            "mc50": mc50.get("max_consec_losses"),
            "spp": None,
        },
        {
            "metric": "Payout Ratio",
            "kind": "ratio",
            "inverse": False,
            "incubation": None,
            "bt": bt.get("payout_ratio"),
            "mc95": mc95.get("payout_ratio"),
            "mc50": mc50.get("payout_ratio"),
            "spp": spp.get("median_payout_ratio"),
        },
        {
            "metric": "SQN Score",
            "kind": "ratio",
            "inverse": False,
            "incubation": None,
            "bt": bt.get("sqn_score"),
            "mc95": mc95.get("sqn_score"),
            "mc50": mc50.get("sqn_score"),
            "spp": spp.get("median_sqn_score"),
        },
        {
            "metric": "Stagnation",
            "kind": "days",
            "inverse": True,
            "incubation": None,
            "bt": bt.get("stagnation_days"),
            "mc95": mc95.get("stagnation_days"),
            "mc50": mc50.get("stagnation_days"),
            "spp": spp.get("median_stagnation_days"),
        },
        {
            "metric": "Ret/DD",
            "kind": "ratio",
            "inverse": False,
            "incubation": None,
            "bt": bt.get("ret_dd_ratio"),
            "mc95": mc95.get("ret_dd_ratio"),
            "mc50": mc50.get("ret_dd_ratio"),
            "spp": spp.get("median_ret_dd_ratio"),
        },
    ]

    return rows


def _incubation_compare_state(value, mc95, mc50, inverse=False):
    if value is None or mc95 is None or mc50 is None:
        return {"label": "—", "class": "cmp-neutral"}

    low = min(mc95, mc50)
    high = max(mc95, mc50)

    if inverse:
        if value <= low:
            return {"label": "🟢", "class": "cmp-green"}
        if value <= high:
            return {"label": "🟡", "class": "cmp-yellow"}
        return {"label": "🔴", "class": "cmp-red"}

    if value >= high:
        return {"label": "🟢", "class": "cmp-green"}
    if value >= low:
        return {"label": "🟡", "class": "cmp-yellow"}
    return {"label": "🔴", "class": "cmp-red"}


def _incubation_checkpoint_timeline(entry, current_key, current_label, total_trades, days):
    checkpoints = entry.get("checkpoints") or {}
    timeline = []
    for key, label in [("cp1", "CP1"), ("cp2", "CP2"), ("cp3", "CP3")]:
        cp = checkpoints.get(key)
        if cp:
            verdict = cp.get("verdict", "PENDING")
            status_class = "cmp-neutral"
            if verdict == "CONTINUAR":
                status_class = "cmp-green"
            elif verdict == "MONITOREAR":
                status_class = "cmp-yellow"
            elif verdict == "ELIMINAR":
                status_class = "cmp-red"
            timeline.append(
                {
                    "key": key,
                    "label": label,
                    "status": verdict,
                    "status_class": status_class,
                    "date": cp.get("updated_at") or "—",
                    "trades": cp.get("trades", "—"),
                    "details": cp.get("checkpoint") or label,
                }
            )
        else:
            timeline.append(
                {
                    "key": key,
                    "label": label,
                    "status": "Pending",
                    "status_class": "cmp-neutral",
                    "date": "—",
                    "trades": "—",
                    "details": "Checkpoint pendiente",
                }
            )

    current = {
        "key": current_key,
        "label": current_label,
        "status": "PENDING",
        "status_class": "cmp-neutral",
        "date": str(date.today()),
        "trades": total_trades,
        "details": f"{days}d operando",
    }

    return timeline, current


def _incubation_distribution_payload(metrics):
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


def _build_monthly_performance(trades):
    from collections import defaultdict

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


def _incubation_reference_ready(entry):
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


def _incubation_metric_summary_for_tooltip(evaluation):
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


def _incubation_evaluate_ea(ea_name, force=False):
    metrics, config, ea_trades = _incubation_load_ea_metrics(ea_name)
    if metrics is None:
        return None

    store = load_incubation_store()
    entry = store.get(ea_name, {})
    reference_ready = _incubation_reference_ready(entry)

    if not reference_ready:
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
    store[ea_name] = entry
    save_incubation_store(store)

    return {
        "ea_name": ea_name,
        "metrics": metrics,
        "entry": entry,
        "config": config,
        "reference_ready": True,
        "evaluation": evaluation,
        "trades": ea_trades,
    }


def _incubation_current_result_from_entry(entry):
    checkpoints = entry.get("checkpoints") or {}
    for slot in ("cp3", "cp2", "cp1"):
        result = checkpoints.get(slot)
        if result:
            return result
    return entry.get("last_evaluation")


def _incubation_build_comparison_rows(metrics, entry):
    if not metrics:
        return []

    bt = entry.get("backtest", {}) or {}
    mc95 = entry.get("monte_carlo", {}).get("confidence_95", {}) or {}
    mc50 = entry.get("monte_carlo", {}).get("confidence_50", {}) or {}
    spp = entry.get("spp", {}) or {}

    rows = [
        {
            "metric": "Win Rate",
            "kind": "pct",
            "inverse": False,
            "live": metrics.get("win_rate"),
            "bt": bt.get("win_rate"),
            "mc95": mc95.get("win_rate"),
            "mc50": mc50.get("win_rate"),
            "spp": None,
        },
        {
            "metric": "Profit Factor",
            "kind": "ratio",
            "inverse": False,
            "live": metrics.get("profit_factor"),
            "bt": bt.get("profit_factor"),
            "mc95": mc95.get("profit_factor"),
            "mc50": mc50.get("profit_factor"),
            "spp": None,
        },
        {
            "metric": "Expectancy",
            "kind": "money",
            "inverse": False,
            "live": metrics.get("expectancy"),
            "bt": bt.get("expectancy"),
            "mc95": mc95.get("expectancy"),
            "mc50": mc50.get("expectancy"),
            "spp": spp.get("median_avg_trade"),
        },
        {
            "metric": "Max DD%",
            "kind": "pct",
            "inverse": True,
            "live": metrics.get("max_dd_pct"),
            "bt": bt.get("max_dd_pct"),
            "mc95": mc95.get("max_dd_pct"),
            "mc50": mc50.get("max_dd_pct"),
            "spp": spp.get("median_max_dd_pct"),
        },
        {
            "metric": "Max Consec Losses",
            "kind": "int",
            "inverse": True,
            "live": metrics.get("max_consec_losses"),
            "bt": bt.get("max_consec_losses"),
            "mc95": mc95.get("max_consec_losses"),
            "mc50": mc50.get("max_consec_losses"),
            "spp": None,
        },
        {
            "metric": "Payout Ratio",
            "kind": "ratio",
            "inverse": False,
            "live": metrics.get("payout_ratio"),
            "bt": bt.get("payout_ratio"),
            "mc95": mc95.get("payout_ratio"),
            "mc50": mc50.get("payout_ratio"),
            "spp": spp.get("median_payout_ratio"),
        },
        {
            "metric": "SQN Score",
            "kind": "ratio",
            "inverse": False,
            "live": metrics.get("sqn"),
            "bt": bt.get("sqn_score"),
            "mc95": mc95.get("sqn_score"),
            "mc50": mc50.get("sqn_score"),
            "spp": spp.get("median_sqn_score"),
        },
        {
            "metric": "Stagnation",
            "kind": "days",
            "inverse": True,
            "live": metrics.get("stagnation_days"),
            "bt": bt.get("stagnation_days"),
            "mc95": mc95.get("stagnation_days"),
            "mc50": mc50.get("stagnation_days"),
            "spp": spp.get("median_stagnation_days"),
        },
        {
            "metric": "Ret/DD",
            "kind": "ratio",
            "inverse": False,
            "live": metrics.get("ret_dd"),
            "bt": bt.get("ret_dd_ratio"),
            "mc95": mc95.get("ret_dd_ratio"),
            "mc50": mc50.get("ret_dd_ratio"),
            "spp": spp.get("median_ret_dd_ratio"),
        },
    ]

    display = []
    for row in rows:
        state = _incubation_metric_band_state(row["live"], row["mc95"], row["mc50"], row["inverse"])
        display.append(
            {
                "metric": row["metric"],
                "live": _incubation_format_metric(row["live"], row["kind"]),
                "bt": _incubation_format_metric(row["bt"], row["kind"]),
                "mc95": _incubation_format_metric(row["mc95"], row["kind"]),
                "mc50": _incubation_format_metric(row["mc50"], row["kind"]),
                "spp": _incubation_format_metric(row["spp"], row["kind"]),
                "state": state,
                "score_band": state["score_band"],
            }
        )
    return display


def _incubation_build_comparison_rows(metrics, entry):
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

    from incubation_validator import get_worst_case_mc

    worst95 = get_worst_case_mc(mc_manipulation, mc_retest, "confidence_95")
    worst50 = get_worst_case_mc(mc_manipulation, mc_retest, "confidence_50")
    current_result = _incubation_current_result_from_entry(entry) or {}
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


def _incubation_verdict_card(evaluation):
    if not evaluation:
        return {
            "checkpoint": "PRE_CP1",
            "score": None,
            "verdict": "NO DATA",
            "verdict_class": "verdict-no-data",
            "summary": "Cargar datos de referencia BT/MC/SPP",
            "hard_gates": [],
            "reason": "No reference data",
            "failing_metrics": [],
        }

    details = evaluation.get("details", {})
    checkpoint = evaluation.get("current_checkpoint", "PENDING")
    verdict = evaluation.get("verdict", "PENDING")
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

    return {
        "checkpoint": checkpoint,
        "score": evaluation.get("score"),
        "verdict": verdict,
        "verdict_class": verdict_class,
        "summary": _incubation_metric_summary_for_tooltip(evaluation),
        "hard_gates": hard_gates,
        "reason": reason,
        "failing_metrics": failing_metrics,
        "escalation_from_cp2": bool(details.get("escalation_from_cp2")),
        "category_scores": details.get("category_scores", {}),
    }


def _incubation_timeline_from_entry(entry):
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


# ─────────────────────────────────────────────────────────────────────────────
# Routes: Upload
# ─────────────────────────────────────────────────────────────────────────────


@app.route("/")
def index():
    config = load_config()
    inc_config = load_incubation_config()
    parsed_data = get_parsed_data()
    total_trades_live = len(parsed_data.get("closed_trades", [])) if parsed_data else 0
    inc_parsed = get_incubation_parsed_data()
    total_trades_inc = len(inc_parsed.get("closed_trades", [])) if inc_parsed else 0

    # Support both old and new key for live
    loaded_files_live = config.get("loaded_files_live", config.get("loaded_files", []))
    loaded_files_inc = inc_config.get("loaded_files_incubation", [])

    analysis_mode = session.get("analysis_mode", "live")
    return render_template(
        "upload.html",
        last_file=config.get("last_file"),
        show_sidebar=False,
        loaded_files_live=loaded_files_live,
        loaded_files_inc=loaded_files_inc,
        total_trades_live=total_trades_live,
        total_trades_inc=total_trades_inc,
        analysis_mode=analysis_mode,
    )


@app.route("/upload", methods=["POST"])
def upload():
    cleanup_old_caches()
    analysis_mode = _normalize_analysis_mode(request.form.get("analysis_mode"))
    session["analysis_mode"] = analysis_mode

    file = request.files.get("file")
    if not file or not file.filename:
        return render_template(
            "upload.html", error="Por favor selecciona un archivo.", show_sidebar=False
        )

    if not file.filename.lower().endswith(".xlsx"):
        return render_template(
            "upload.html",
            error="El archivo debe ser .xlsx exportado de MT5.",
            show_sidebar=False,
        )

    # Save uploaded file — use secure_filename to sanitize client-supplied names
    from werkzeug.utils import secure_filename
    safe_name = secure_filename(file.filename) or "upload.xlsx"
    filepath = os.path.join(UPLOAD_FOLDER, safe_name)
    file.save(filepath)

    # Parse new file
    try:
        from parser import parse_mt5_report, merge_trades

        new_data = parse_mt5_report(filepath)
    except ValueError as e:
        return render_template("upload.html", error=str(e), show_sidebar=False)
    except Exception as e:
        return render_template(
            "upload.html",
            error=f"Error inesperado al parsear el archivo: {e}",
            show_sidebar=False,
        )

    if analysis_mode == "incubation":
        # Merge incubation trades (append mode, same as live)
        inc_config = load_incubation_config()
        existing_inc_data = get_incubation_parsed_data()
        old_inc_cache_key = session.get("incubation_cache_key")
        added_count_inc = len(new_data["closed_trades"])

        if existing_inc_data:
            existing_trades_inc = existing_inc_data.get("closed_trades", [])
            from parser import merge_trades
            merged_inc = merge_trades(existing_trades_inc, new_data["closed_trades"])
            added_count_inc = len(merged_inc) - len(existing_trades_inc)
            if added_count_inc == 0:
                return redirect(url_for("incubation_dashboard"))
            merged_ea_names_inc = sorted(set(
                t["comment"] for t in merged_inc
                if t.get("comment") and t["comment"] != "Unknown"
            ))
            new_data["closed_trades"] = merged_inc
            new_data["total_closed"] = len(merged_inc)
            new_data["ea_names"] = merged_ea_names_inc

        incubation_cache_key = save_incubation_cache(new_data)
        if old_inc_cache_key and old_inc_cache_key != incubation_cache_key:
            _delete_cache_file(old_inc_cache_key, INCUBATION_CACHE_PREFIX)

        session["incubation_cache_key"] = incubation_cache_key
        session["filename"] = safe_name

        loaded_files_inc = inc_config.get("loaded_files_incubation", [])
        loaded_files_inc.append({
            "name": safe_name,
            "date": str(date.today()),
            "trades_added": added_count_inc,
        })
        inc_config["loaded_files_incubation"] = loaded_files_inc
        inc_config["last_file"] = safe_name
        inc_config["last_updated"] = str(date.today())
        save_incubation_config(inc_config)

        return redirect(url_for("incubation_mapping"))

    # Append mode: merge with existing cache if available
    config = load_config()
    existing_data = get_parsed_data()
    old_cache_key = session.get("cache_key")  # track to delete after merge
    added_count = len(new_data["closed_trades"])

    if existing_data:
        existing_trades = existing_data.get("closed_trades", [])
        merged_trades = merge_trades(existing_trades, new_data["closed_trades"])
        added_count = len(merged_trades) - len(existing_trades)

        # Same file / no new trades: keep the current session data and reopen the app.
        if added_count == 0:
            return redirect(url_for("dashboard"))

        # Rebuild ea_names from merged trades
        merged_ea_names = sorted(set(
            t["comment"] for t in merged_trades
            if t.get("comment") and t["comment"] != "Unknown"
        ))

        # Recompute unknown_trades from the full merged dataset
        merged_unknown = sum(1 for t in merged_trades if t.get("comment") == "Unknown")

        new_data["closed_trades"] = merged_trades
        new_data["total_closed"] = len(merged_trades)
        new_data["ea_names"] = merged_ea_names
        new_data["unknown_trades"] = merged_unknown
        # open_positions and account info come from the most recent file (new_data)

    # Invalidar cache de métricas ANTES de save_cache (key aún no cambiada)
    invalidate_metrics_cache()

    # Cache merged data — generates a new UUID key
    cache_key = save_cache(new_data)

    # CRITICAL fix: delete the old cache file to avoid orphaned files on disk
    if old_cache_key and old_cache_key != cache_key:
        _delete_cache_file(old_cache_key, LIVE_CACHE_PREFIX)

    session["cache_key"] = cache_key
    session["filename"] = safe_name

    # Update config: track loaded files history per mode
    loaded_files = config.get("loaded_files_live", config.get("loaded_files", []))
    loaded_files.append({
        "name": safe_name,
        "date": str(date.today()),
        "trades_added": added_count,
    })
    config["loaded_files_live"] = loaded_files
    config.pop("loaded_files", None)  # migrate old key
    config["last_file"] = safe_name
    config["last_updated"] = str(date.today())
    save_config(config)

    return redirect(url_for("mapping"))


@app.route("/reset", methods=["POST"])
def reset_history():
    """Clear live trade history (trades + file log). Keeps mappings and BT data."""
    cache_key = session.get("cache_key")
    _delete_cache_file(cache_key, LIVE_CACHE_PREFIX)

    invalidate_metrics_cache()
    session.pop("cache_key", None)
    session.pop("filename", None)

    config = load_config()
    config["loaded_files_live"] = []
    config.pop("loaded_files", None)
    config["last_file"] = None
    save_config(config)

    return redirect(url_for("index"))


@app.route("/reset_all_live", methods=["POST"])
def reset_all_live():
    """Full live reset: clears trades, file log, mappings, and validator BT data."""
    cache_key = session.get("cache_key")
    _delete_cache_file(cache_key, LIVE_CACHE_PREFIX)

    invalidate_metrics_cache()
    session.pop("cache_key", None)
    session.pop("filename", None)

    # Clear all live config and validator store
    config = load_config()
    config["loaded_files_live"] = []
    config.pop("loaded_files", None)
    config["last_file"] = None
    config["last_updated"] = None
    config["mappings"] = {}
    save_config(config)

    # Clear validator backtest data
    validator_store_path = os.path.join(APP_DIR, "validator_store.json")
    try:
        with open(validator_store_path, "w", encoding="utf-8") as f:
            json.dump({}, f)
    except OSError:
        pass

    flash("Reset completo Live — todos los datos eliminados.", "success")
    return redirect(url_for("index"))


def _clear_incubation_session_cache():
    incubation_cache_key = session.get("incubation_cache_key")
    _delete_cache_file(incubation_cache_key, INCUBATION_CACHE_PREFIX)

    session.pop("incubation_cache_key", None)
    session.pop("filename", None)


@app.route("/incubation/reset", methods=["POST"])
def incubation_reset():
    """Clear incubation trade cache and file log. Keeps mappings and reference data."""
    guard = _require_incubation_mode()
    if guard:
        return guard

    _clear_incubation_session_cache()

    inc_config = load_incubation_config()
    inc_config["loaded_files_incubation"] = []
    inc_config["last_file"] = None
    save_incubation_config(inc_config)

    flash("Trades de incubación eliminados.", "success")
    return redirect(url_for("index"))


@app.route("/incubation/reset_all", methods=["POST"])
def incubation_reset_all():
    """Full incubation reset: clears trades, file log, mappings, and all reference/checkpoint data."""
    guard = _require_incubation_mode()
    if guard:
        return guard

    _clear_incubation_session_cache()
    save_incubation_store({})
    config = load_incubation_config()
    config["mappings"] = {}
    config["last_file"] = None
    config["last_updated"] = None
    config["loaded_files_incubation"] = []
    save_incubation_config(config)
    flash("Reset completo Incubación — todos los datos eliminados.", "success")
    return redirect(url_for("index"))


# ─────────────────────────────────────────────────────────────────────────────
# Routes: Magic Number Mapping
# ─────────────────────────────────────────────────────────────────────────────


@app.route("/incubation/mapping")
def incubation_mapping():
    guard = _require_incubation_mode()
    if guard:
        return guard

    parsed_data = get_incubation_parsed_data()
    if not parsed_data:
        flash("Seleccione modo Incubation primero", "warn")
        return redirect(url_for("index"))

    config = load_incubation_config()
    ea_list = build_mapping_rows(parsed_data, config)
    return render_template(
        "incubation_mapping.html",
        ea_list=ea_list,
        account=parsed_data.get("account", {}),
        filename=session.get("filename", ""),
        unknown_trades=parsed_data.get("unknown_trades", 0),
        show_sidebar=False,
        active_page="incubation_mapping",
    )


@app.route("/mapping")
def mapping():
    guard = _require_live_mode()
    if guard:
        return guard

    parsed_data = get_parsed_data()
    if not parsed_data:
        return redirect(url_for("index"))

    config = load_config()
    ea_list = build_mapping_rows(parsed_data, config)

    return render_template(
        "mapping.html",
        ea_list=ea_list,
        account=parsed_data.get("account", {}),
        filename=session.get("filename", ""),
        unknown_trades=parsed_data.get("unknown_trades", 0),
        show_sidebar=False,
    )


@app.route("/mapping/save", methods=["POST"])
def mapping_save():
    guard = _require_live_mode("incubation_mapping")
    if guard:
        return guard

    parsed_data = get_parsed_data()
    if not parsed_data:
        return redirect(url_for("index"))

    config = load_config()
    mappings = config.setdefault("mappings", {})

    for ea_name in parsed_data.get("ea_names", []):
        magic_val = request.form.get(f"magic_{ea_name}", "").strip()
        instrument_val = request.form.get(f"instrument_{ea_name}", "").strip()
        capital_val = request.form.get(f"capital_{ea_name}", "").strip()

        existing = mappings.get(ea_name, {})
        entry = dict(existing)
        if instrument_val:
            entry["instrument"] = instrument_val
        if capital_val:
            try:
                entry["capital"] = float(capital_val)
            except ValueError:
                entry["capital"] = 5000.0
        else:
            entry.setdefault("capital", 5000.0)

        if magic_val:
            try:
                entry["magic"] = int(magic_val)
            except ValueError:
                pass

        alias_val = request.form.get(f"alias_{ea_name}", "").strip()
        entry["alias"] = alias_val
        entry["active"] = f"include_{ea_name}" in request.form

        if entry:
            mappings[ea_name] = entry

    config["last_updated"] = str(date.today())
    save_config(config)

    return redirect(url_for("dashboard"))


@app.route("/incubation/mapping/save", methods=["POST"])
def incubation_mapping_save():
    guard = _require_incubation_mode()
    if guard:
        return guard

    parsed_data = get_incubation_parsed_data()
    if not parsed_data:
        flash("Seleccione modo Incubation primero", "warn")
        return redirect(url_for("index"))

    config = load_incubation_config()
    mappings = config.setdefault("mappings", {})

    for ea_name in parsed_data.get("ea_names", []):
        magic_val = request.form.get(f"magic_{ea_name}", "").strip()
        instrument_val = request.form.get(f"instrument_{ea_name}", "").strip()
        capital_val = request.form.get(f"capital_{ea_name}", "").strip()

        existing = mappings.get(ea_name, {})
        entry = dict(existing)

        if instrument_val:
            entry["instrument"] = instrument_val
        if capital_val:
            try:
                entry["capital"] = float(capital_val)
            except ValueError:
                entry["capital"] = 5000.0
        else:
            entry.setdefault("capital", 5000.0)

        if magic_val:
            try:
                entry["magic"] = int(magic_val)
            except ValueError:
                pass

        alias_val = request.form.get(f"alias_{ea_name}", "").strip()
        entry["alias"] = alias_val
        entry["active"] = f"include_{ea_name}" in request.form

        if entry:
            mappings[ea_name] = entry

    config["last_updated"] = str(date.today())
    save_incubation_config(config)

    return redirect(url_for("incubation_reference_data"))


@app.route("/incubation/reference_data")
def incubation_reference_data():
    guard = _require_incubation_mode()
    if guard:
        return guard

    config = load_incubation_config()
    store = load_incubation_store()
    rows = []

    for ea_name, mapping in config.get("mappings", {}).items():
        if not mapping.get("active", True):
            continue

        entry = store.get(ea_name, {})
        has_bt = bool(entry.get("backtest"))
        mc_manipulation = entry.get("mc_manipulation") or entry.get("monte_carlo") or {}
        mc_retest = entry.get("mc_retest") or {}
        has_mc95 = bool(mc_manipulation.get("confidence_95")) or bool(mc_retest.get("confidence_95"))
        has_data = has_bt and has_mc95
        rows.append(
            {
                "name": ea_name,
                "label": mapping.get("alias", "") or ea_name,
                "instrument": mapping.get("instrument", ""),
                "timeframe": entry.get("timeframe", ""),
                "status": "✅ datos cargados" if has_data else "⚠️ pendiente",
                "has_data": has_data,
                "url": url_for("incubation_reference_edit", ea_name=ea_name),
            }
        )

    return render_template(
        "incubation_reference.html",
        rows=rows,
        total_active=len(rows),
        loaded_count=sum(1 for row in rows if row["has_data"]),
        show_sidebar=False,
        active_page="incubation_reference_data",
        filename=session.get("filename", ""),
    )


@app.route("/incubation/reference_data/edit/<path:ea_name>")
def incubation_reference_edit(ea_name):
    guard = _require_incubation_mode()
    if guard:
        return guard

    ea_name = unquote(ea_name)
    config = load_incubation_config()
    mapping = config.get("mappings", {}).get(ea_name)
    if not mapping:
        flash("La estrategia no existe en Incubation Config", "warn")
        return redirect(url_for("incubation_reference_data"))

    store = load_incubation_store()
    entry = store.get(ea_name, {})
    form_values = _build_reference_form_values(entry=entry)
    spp_ratios = _compute_spp_ratios(entry.get("backtest", {}), entry.get("spp", {}))

    warnings = []
    mc_manipulation = entry.get("mc_manipulation") or entry.get("monte_carlo") or {}
    mc_retest = entry.get("mc_retest") or {}
    if not mc_manipulation.get("confidence_95"):
        warnings.append("Monte Carlo Trades Manipulation 95% no está cargado todavía.")
    if not mc_retest.get("confidence_95"):
        warnings.append("Monte Carlo Retest Methods 95% no está cargado todavía.")
    if not mc_manipulation.get("confidence_50"):
        warnings.append("Monte Carlo Trades Manipulation 50% no está cargado todavía.")
    if not mc_retest.get("confidence_50"):
        warnings.append("Monte Carlo Retest Methods 50% no está cargado todavía.")
    if not entry.get("spp"):
        warnings.append("Sin datos SPP, el análisis usará solo BT y MC.")

    return render_template(
        "incubation_reference_edit.html",
        ea_name=ea_name,
        mapping=mapping,
        entry=entry,
        form_values=form_values,
        sections=_incubation_reference_sections_for_render(entry),
        spp_ratios=spp_ratios,
        warnings=warnings,
        errors={},
        show_sidebar=False,
        active_page="incubation_reference_data",
        filename=session.get("filename", ""),
    )


@app.route("/incubation/reference_data/save/<path:ea_name>", methods=["POST"])
def incubation_reference_save(ea_name):
    guard = _require_incubation_mode()
    if guard:
        return guard

    ea_name = unquote(ea_name)
    config = load_incubation_config()
    mapping = config.get("mappings", {}).get(ea_name)
    if not mapping:
        flash("La estrategia no existe en Incubation Config", "warn")
        return redirect(url_for("incubation_reference_data"))

    payload, errors, warnings = _parse_reference_form(request.form)
    if errors:
        form_values = _build_reference_form_values(form=request.form)
        spp_ratios = _compute_spp_ratios(form_values.get("backtest", {}), form_values.get("spp", {}))
        return render_template(
            "incubation_reference_edit.html",
            ea_name=ea_name,
            mapping=mapping,
            entry=payload,
            form_values=form_values,
            sections=_incubation_reference_sections_for_render(payload),
            spp_ratios=spp_ratios,
            warnings=warnings,
            errors=errors,
            show_sidebar=False,
            active_page="incubation_reference_data",
            filename=session.get("filename", ""),
        )

    store = load_incubation_store()
    existing = store.get(ea_name, {})
    checkpoints = existing.get("checkpoints") or {"cp1": None, "cp2": None, "cp3": None}
    date_added = existing.get("date_added") or str(date.today())

    backtest = payload["backtest"]
    mc_manipulation = payload["mc_manipulation"]
    mc_retest = payload["mc_retest"]
    existing_manip = existing.get("mc_manipulation") or existing.get("monte_carlo") or {}
    existing_retest = existing.get("mc_retest") or {}

    final_mc_manip = {
        "confidence_95": mc_manipulation.get("confidence_95", {}) or existing_manip.get("confidence_95", {}),
        "confidence_50": mc_manipulation.get("confidence_50", {}) or existing_manip.get("confidence_50", {}),
    }
    final_mc_retest = {
        "confidence_95": mc_retest.get("confidence_95", {}) or existing_retest.get("confidence_95", {}),
        "confidence_50": mc_retest.get("confidence_50", {}) or existing_retest.get("confidence_50", {}),
    }
    final_mc_manip["simulations"] = mc_manipulation.get("simulations") or existing_manip.get("simulations")
    final_mc_manip["method"] = mc_manipulation.get("method") or existing_manip.get("method", "")
    final_mc_retest["simulations"] = mc_retest.get("simulations") or existing_retest.get("simulations")
    final_mc_retest["method"] = mc_retest.get("method") or existing_retest.get("method", "")
    final_spp = payload["spp"] or existing.get("spp", {})

    store[ea_name] = {
        "ea_name": ea_name,
        "instrument": mapping.get("instrument", ""),
        "timeframe": backtest.get("timeframe", ""),
        "bt_period": backtest.get("bt_period", ""),
        "date_added": date_added,
        "status": "incubating",
        "backtest": backtest,
        "mc_manipulation": final_mc_manip,
        "mc_retest": final_mc_retest,
        "monte_carlo": {
            "confidence_95": final_mc_manip.get("confidence_95", {}),
            "confidence_50": final_mc_manip.get("confidence_50", {}),
        },
        "spp": final_spp,
        "checkpoints": checkpoints,
    }
    save_incubation_store(store)

    if not final_mc_manip.get("confidence_50"):
        flash("Monte Carlo Trades Manipulation 50% no fue completado.", "warn")
    if not final_mc_retest.get("confidence_50"):
        flash("Monte Carlo Retest Methods 50% no fue completado.", "warn")
    if not final_mc_manip.get("confidence_95"):
        flash("Monte Carlo Trades Manipulation 95% no fue completado.", "warn")
    if not final_mc_retest.get("confidence_95"):
        flash("Monte Carlo Retest Methods 95% no fue completado.", "warn")
    if not payload["spp"] and not final_spp:
        flash("Sin datos SPP, el análisis usará solo BT y MC.", "warn")
    if warnings:
        for warning in warnings:
            if warning not in {
                "Monte Carlo Trades Manipulation 50% no fue completado.",
                "Monte Carlo Retest Methods 50% no fue completado.",
                "Monte Carlo Trades Manipulation 95% no fue completado.",
                "Monte Carlo Retest Methods 95% no fue completado.",
                "Sin datos SPP, el análisis usará solo BT y MC.",
            }:
                flash(warning, "warn")

    return redirect(url_for("incubation_reference_data"))


@app.route("/incubation/reference_data/delete/<path:ea_name>", methods=["POST"])
def incubation_reference_delete(ea_name):
    guard = _require_incubation_mode()
    if guard:
        return guard

    ea_name = unquote(ea_name)
    store = load_incubation_store()
    if ea_name in store:
        store.pop(ea_name, None)
        save_incubation_store(store)
    else:
        flash("No había datos de referencia para esa estrategia.", "warn")

    return redirect(url_for("incubation_reference_data"))


# ─────────────────────────────────────────────────────────────────────────────
# Routes: Incubation Dashboard
# ─────────────────────────────────────────────────────────────────────────────


@app.route("/incubation/strategy/<path:ea_name>")
def incubation_strategy(ea_name):
    guard = _require_incubation_mode()
    if guard:
        return guard

    ea_name = unquote(ea_name)
    bundle = _incubation_evaluate_ea(ea_name)
    if bundle is None:
        flash("No hay trades de incubación para esa estrategia.", "warn")
        return redirect(url_for("incubation_dashboard"))

    metrics = bundle["metrics"]
    config = bundle["config"]
    entry = bundle["entry"]
    evaluation = bundle["evaluation"]
    ea_trades = bundle["trades"]
    mapping = config.get("mappings", {}).get(ea_name, {})

    total_trades = int(metrics.get("total_trades") or 0)
    days = _incubation_days_since_first_trade(ea_trades)
    checkpoint_key, checkpoint_label, checkpoint_class = _incubation_checkpoint_for_trades(total_trades)
    has_reference = _incubation_reference_ready(entry)
    display_rows = _incubation_build_comparison_rows(metrics, entry)
    verdict_card = _incubation_verdict_card(evaluation)
    checkpoint_timeline = _incubation_timeline_from_entry(entry)
    current_checkpoint = (
        evaluation.get("current_checkpoint")
        if evaluation
        else ("PRE_CP1" if checkpoint_key == "pre_cp1" else checkpoint_key.upper())
    )
    current_score = evaluation.get("score") if evaluation else None

    def fmt_dt(dt_val):
        if isinstance(dt_val, str):
            try:
                return datetime.fromisoformat(dt_val).strftime("%d/%m/%Y %H:%M")
            except Exception:
                return dt_val
        if isinstance(dt_val, datetime):
            return dt_val.strftime("%d/%m/%Y %H:%M")
        return str(dt_val) if dt_val else ""

    display_trades = []
    for i, t in enumerate(metrics["trades"], 1):
        display_trades.append(
            {
                "num": i,
                "open_time": fmt_dt(t.get("open_time")),
                "close_time": fmt_dt(t.get("close_time")),
                "direction": t.get("direction", "").upper(),
                "volume": t.get("volume", 0),
                "open_price": t.get("open_price", 0),
                "close_price": t.get("close_price", 0),
                "sl": t.get("sl"),
                "tp": t.get("tp"),
                "commission": t.get("commission", 0),
                "swap": t.get("swap", 0),
                "net_pnl": t.get("net_pnl", 0),
                "duration_hours": t.get("duration_hours", 0),
                "is_win": t.get("net_pnl", 0) > 0,
            }
        )

    monthly_perf = _build_monthly_performance(metrics["trades"])
    chart_label = metrics.get("label") or mapping.get("alias") or ea_name
    inc_equity_series = {
        "equity": metrics.get("equity_curve", []),
        "drawdown": metrics.get("drawdown_curve", []),
        "label": chart_label,
        "color": "#4FC3F7",
    }
    inc_distribution = _incubation_distribution_payload(metrics)

    instrument = metrics.get("instrument") or mapping.get("instrument") or "—"
    timeframe = entry.get("timeframe") or entry.get("backtest", {}).get("timeframe") or "—"
    actions_enabled = bool(has_reference)

    return render_template(
        "incubation_strategy.html",
        ea_name=ea_name,
        m=metrics,
        instrument=instrument,
        timeframe=timeframe,
        chart_label=chart_label,
        current_checkpoint=current_checkpoint,
        current_score=current_score,
        current_verdict_card=verdict_card,
        comparison_rows=display_rows,
        checkpoint_timeline=checkpoint_timeline,
        monthly_perf=monthly_perf,
        trades=display_trades,
        inc_equity_series=inc_equity_series,
        inc_distribution=inc_distribution,
        has_reference=has_reference,
        actions_enabled=actions_enabled,
        reference_url=url_for("incubation_reference_data"),
        force_eval_url=url_for("incubation_force_evaluate", ea_name=ea_name),
        reset_url=url_for("incubation_reset_checkpoints", ea_name=ea_name),
        show_sidebar=True,
        active_page="incubation_dashboard",
        filename=session.get("filename", ""),
    )


@app.route("/incubation/dashboard")
def incubation_dashboard():
    guard = _require_incubation_mode()
    if guard:
        return guard

    dashboard_data = _build_incubation_dashboard()
    if dashboard_data is None:
        flash("No hay datos de incubación cargados todavía.", "warn")
        return redirect(url_for("incubation_reference_data"))

    return render_template(
        "incubation_dashboard.html",
        rows=dashboard_data["rows"],
        total_active=dashboard_data["total_active"],
        pending_bt_mc=dashboard_data["pending_bt_mc"],
        eliminar_count=dashboard_data["eliminar_count"],
        aprobar_count=dashboard_data["aprobar_count"],
        show_sidebar=True,
        active_page="incubation_dashboard",
        filename=session.get("filename", ""),
    )


@app.route("/incubation/force_evaluate/<path:ea_name>", methods=["POST"])
def incubation_force_evaluate(ea_name):
    guard = _require_incubation_mode()
    if guard:
        return guard

    ea_name = unquote(ea_name)
    bundle = _incubation_evaluate_ea(ea_name)
    if bundle is None:
        flash("No hay trades de incubación para reevaluar.", "warn")
        return redirect(url_for("incubation_dashboard"))

    flash("Incubation reevaluado", "success")
    return redirect(url_for("incubation_strategy", ea_name=ea_name))


@app.route("/incubation/reset_checkpoints/<path:ea_name>", methods=["POST"])
def incubation_reset_checkpoints(ea_name):
    guard = _require_incubation_mode()
    if guard:
        return guard

    ea_name = unquote(ea_name)
    store = load_incubation_store()
    entry = store.get(ea_name)
    if not entry:
        flash("No hay datos de referencia para esa estrategia.", "warn")
        return redirect(url_for("incubation_dashboard"))

    entry["checkpoints"] = {"cp1": None, "cp2": None, "cp3": None}
    entry.pop("last_evaluation", None)
    store[ea_name] = entry
    save_incubation_store(store)

    flash("Checkpoints reseteados", "success")
    return redirect(url_for("incubation_strategy", ea_name=ea_name))


@app.route("/switch_mode/<mode>")
def switch_mode(mode):
    target = _normalize_analysis_mode(mode)
    if target not in {"live", "incubation"}:
        flash("Modo inválido", "warn")
        return redirect(url_for("index"))

    session["analysis_mode"] = target
    flash(f"Switched to {target} mode", "success")
    return redirect(_mode_home(target))


@app.route("/switch/live-mode")
def switch_to_live_mode():
    return switch_mode("live")


# ─────────────────────────────────────────────────────────────────────────────
# Routes: Dashboard
# ─────────────────────────────────────────────────────────────────────────────


@app.route("/dashboard")
def dashboard():
    guard = _require_live_mode("incubation_dashboard")
    if guard:
        return guard

    parsed_data = get_parsed_data()
    if not parsed_data:
        return redirect(url_for("index"))

    config = load_config()
    from metrics import calculate_all_metrics

    all_metrics = _get_metrics_cached(parsed_data, config)

    portfolio = all_metrics["portfolio"]
    by_ea = all_metrics["by_ea"]
    ea_colors = all_metrics["ea_colors"]

    sidebar_eas = build_sidebar_eas(parsed_data, config)

    # Build EA summary rows for the table
    ea_rows = []
    for ea_name, m in by_ea.items():
        ea_rows.append(
            {
                "name": ea_name,
                "label": m["label"],
                "magic": m["magic"] or "",
                "instrument": m["instrument"],
                "weeks_operating": m.get("weeks_operating", 0.0),
                "total_trades": m["total_trades"],
                "win_rate": m["win_rate"],
                "profit_factor": m["profit_factor"],
                "payout_ratio": m["payout_ratio"],
                "expectancy": m["expectancy"],
                "max_dd_pct": m["max_dd_pct"],
                "ret_dd": m["ret_dd"],
                "sqn": m["sqn"],
                "sqn_note": m["sqn_note"],
                "sharpe_ratio": m["sharpe_ratio"],
                "stagnation_days": m["stagnation_days"],
                "max_consec_losses": m["max_consec_losses"],
                "net_profit": m["net_profit"],
                "url": url_for("strategy", name=quote(ea_name, safe="")),
                "color": ea_colors.get(ea_name, "#4FC3F7"),
            }
        )

    # Period
    trades = parsed_data.get("closed_trades", [])
    period_start = ""
    period_end = ""
    if trades:
        times = [t["close_time"] for t in trades if t.get("close_time")]
        if times:
            times_sorted = sorted(times)

            def fmt_dt(dt_str):
                if isinstance(dt_str, str):
                    try:
                        return datetime.fromisoformat(dt_str).strftime("%d/%m/%Y")
                    except Exception:
                        return dt_str
                return str(dt_str)

            period_start = fmt_dt(times_sorted[0])
            period_end = fmt_dt(times_sorted[-1])

    portfolio_monthly_perf = _build_monthly_performance(portfolio.get("trades", []))

    return render_template(
        "dashboard.html",
        portfolio=portfolio,
        ea_rows=ea_rows,
        ea_colors=ea_colors,
        sidebar_eas=sidebar_eas,
        account=parsed_data.get("account", {}),
        period_start=period_start,
        period_end=period_end,
        total_eas=len(by_ea),
        open_count=parsed_data.get("total_open", 0),
        unknown_count=parsed_data.get("unknown_trades", 0),
        portfolio_monthly_perf=portfolio_monthly_perf,
        show_sidebar=True,
        active_page="dashboard",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Routes: Strategy Detail
# ─────────────────────────────────────────────────────────────────────────────


@app.route("/strategy/<path:name>")
def strategy(name):
    guard = _require_live_mode("incubation_dashboard")
    if guard:
        return guard

    ea_name = unquote(name)
    parsed_data = get_parsed_data()
    if not parsed_data:
        return redirect(url_for("index"))

    config = load_config()
    ea_trades = [
        t for t in parsed_data.get("closed_trades", []) if t.get("comment") == ea_name
    ]

    if not ea_trades:
        return redirect(url_for("dashboard"))

    from metrics import calculate_ea_metrics

    m = calculate_ea_metrics(ea_name, ea_trades, config)

    sidebar_eas = build_sidebar_eas(parsed_data, config, active_ea=ea_name)

    # Format trades for template display
    def fmt_dt(dt_val):
        if isinstance(dt_val, str):
            try:
                return datetime.fromisoformat(dt_val).strftime("%d/%m/%Y %H:%M")
            except Exception:
                return dt_val
        if isinstance(dt_val, datetime):
            return dt_val.strftime("%d/%m/%Y %H:%M")
        return str(dt_val) if dt_val else ""

    display_trades = []
    for i, t in enumerate(m["trades"], 1):
        display_trades.append(
            {
                "num": i,
                "open_time": fmt_dt(t.get("open_time")),
                "close_time": fmt_dt(t.get("close_time")),
                "direction": t.get("direction", "").upper(),
                "volume": t.get("volume", 0),
                "open_price": t.get("open_price", 0),
                "close_price": t.get("close_price", 0),
                "sl": t.get("sl"),
                "tp": t.get("tp"),
                "commission": t.get("commission", 0),
                "swap": t.get("swap", 0),
                "net_pnl": t.get("net_pnl", 0),
                "duration_hours": t.get("duration_hours", 0),
                "is_win": t.get("net_pnl", 0) > 0,
            }
        )

    monthly_perf = _build_monthly_performance(m["trades"])

    return render_template(
        "strategy.html",
        m=m,
        trades=display_trades,
        ea_name=ea_name,
        monthly_perf=monthly_perf,
        sidebar_eas=sidebar_eas,
        account=parsed_data.get("account", {}),
        show_sidebar=True,
        active_ea=ea_name,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Routes: Export
# ─────────────────────────────────────────────────────────────────────────────


@app.route("/export")
def export():
    guard = _require_live_mode("incubation_dashboard")
    if guard:
        return guard

    parsed_data = get_parsed_data()
    if not parsed_data:
        return redirect(url_for("index"))

    config = load_config()
    from metrics import calculate_all_metrics

    all_metrics = _get_metrics_cached(parsed_data, config)

    by_ea = all_metrics["by_ea"]
    sidebar_eas = build_sidebar_eas(parsed_data, config)

    export_rows = []
    for ea_name, m in by_ea.items():
        mapping = config.get("mappings", {}).get(ea_name, {})
        export_rows.append(
            {
                "magic": mapping.get("magic", ""),
                "name": ea_name,
                "label": m["label"],
                "trades": m["total_trades"],
                "weeks": round(m["weeks_operating"], 1),
                "win_rate": round(m["win_rate"], 2),
                "profit_factor": m["profit_factor"]
                if isinstance(m["profit_factor"], str)
                else round(float(m["profit_factor"]), 2),
                "payout": m["payout_ratio"]
                if isinstance(m["payout_ratio"], str)
                else round(float(m["payout_ratio"]), 2),
                "expectancy": round(m["expectancy"], 2),
                "max_dd_pct": round(m["max_dd_pct"], 2),
                "avg_duration": round(m["avg_duration_hours"], 1),
                "max_consec_losses": m["max_consec_losses"],
                "stagnation_days": m["stagnation_days"],
            }
        )

    return render_template(
        "export.html",
        export_rows=export_rows,
        sidebar_eas=sidebar_eas,
        account=parsed_data.get("account", {}),
        show_sidebar=True,
        active_page="export",
    )


# ─────────────────────────────────────────────────────────────────────────────
# API: Chart data endpoints
# ─────────────────────────────────────────────────────────────────────────────


@app.route("/api/equity_curves")
def api_equity_curves():
    parsed_data = get_parsed_data()
    if not parsed_data:
        return jsonify({"error": "No hay datos cargados"}), 400

    days_param = request.args.get("days", type=int)

    config = load_config()
    all_metrics = _get_metrics_cached(parsed_data, config)

    cutoff_str = None
    if days_param is not None:
        cutoff_str = (datetime.now() - timedelta(days=days_param)).isoformat()

    traces = []

    # Portfolio trace (white, thick, visible by default)
    portfolio = all_metrics["portfolio"]
    port_curve = portfolio["equity_curve"]
    if port_curve:
        if cutoff_str:
            port_curve = [p for p in port_curve if p["date"] >= cutoff_str]
        traces.append(
            {
                "name": "PORTFOLIO",
                "x": [p["date"] for p in port_curve],
                "y": [p["equity"] for p in port_curve],
                "color": "#FFFFFF",
                "width": 3,
                "visible": True,
                "is_portfolio": True,
            }
        )

    # EA traces (colored, thin, hidden by default)
    ea_colors = all_metrics["ea_colors"]
    for ea_name, m in all_metrics["by_ea"].items():
        label = get_display_label(ea_name, config)
        curve = m["equity_curve"]
        if not curve:
            continue
        if cutoff_str:
            curve = [p for p in curve if p["date"] >= cutoff_str]
        traces.append(
            {
                "name": label,
                "x": [p["date"] for p in curve],
                "y": [p["equity"] for p in curve],
                "color": ea_colors.get(ea_name, "#4FC3F7"),
                "width": 1.5,
                "visible": "legendonly",
                "is_portfolio": False,
            }
        )

    return jsonify({"traces": traces})


@app.route("/api/drawdown_curves")
def api_drawdown_curves():
    parsed_data = get_parsed_data()
    if not parsed_data:
        return jsonify({"error": "No hay datos cargados"}), 400

    days_param = request.args.get("days", type=int)

    config = load_config()
    from metrics import calculate_all_metrics

    all_metrics = _get_metrics_cached(parsed_data, config)

    cutoff_str = None
    if days_param is not None:
        cutoff_str = (datetime.now() - timedelta(days=days_param)).isoformat()

    traces = []

    # Portfolio DD trace
    portfolio = all_metrics["portfolio"]
    dd_curve = portfolio["drawdown_curve"]
    if dd_curve:
        if cutoff_str:
            dd_curve = [p for p in dd_curve if p["date"] >= cutoff_str]
        traces.append(
            {
                "name": "PORTFOLIO",
                "x": [p["date"] for p in dd_curve],
                "y": [p["dd_pct"] for p in dd_curve],
                "color": "#FFFFFF",
                "width": 2,
                "visible": True,
                "is_portfolio": True,
            }
        )

    # EA DD traces
    ea_colors = all_metrics["ea_colors"]
    for ea_name, m in all_metrics["by_ea"].items():
        label = get_display_label(ea_name, config)
        curve = m["drawdown_curve"]
        if not curve:
            continue
        if cutoff_str:
            curve = [p for p in curve if p["date"] >= cutoff_str]
        traces.append(
            {
                "name": label,
                "x": [p["date"] for p in curve],
                "y": [p["dd_pct"] for p in curve],
                "color": ea_colors.get(ea_name, "#4FC3F7"),
                "width": 1,
                "visible": "legendonly",
                "is_portfolio": False,
            }
        )

    return jsonify({"traces": traces})


@app.route("/api/contribution")
def api_contribution():
    parsed_data = get_parsed_data()
    if not parsed_data:
        return jsonify({"error": "No hay datos cargados"}), 400

    config = load_config()
    from metrics import calculate_all_metrics

    all_metrics = _get_metrics_cached(parsed_data, config)

    items = []
    for ea_name, m in all_metrics["by_ea"].items():
        label = get_display_label(ea_name, config)
        items.append({"label": label, "value": m["net_profit"]})

    # Sort by value descending
    items.sort(key=lambda x: x["value"], reverse=True)

    return jsonify(
        {
            "labels": [i["label"] for i in items],
            "values": [i["value"] for i in items],
            "colors": ["#4CAF50" if i["value"] >= 0 else "#FF5252" for i in items],
        }
    )


@app.route("/api/ea_equity/<path:name>")
def api_ea_equity(name):
    ea_name = unquote(name)
    parsed_data = get_parsed_data()
    if not parsed_data:
        return jsonify({"error": "No hay datos cargados"}), 400

    days_param = request.args.get("days", type=int)

    config = load_config()
    ea_trades = [
        t for t in parsed_data.get("closed_trades", []) if t.get("comment") == ea_name
    ]

    from metrics import calculate_ea_metrics

    m = calculate_ea_metrics(ea_name, ea_trades, config)

    equity_curve = m["equity_curve"]
    drawdown_curve = m["drawdown_curve"]

    if days_param is not None:
        cutoff_str = (datetime.now() - timedelta(days=days_param)).isoformat()
        equity_curve = [p for p in equity_curve if p["date"] >= cutoff_str]
        drawdown_curve = [p for p in drawdown_curve if p["date"] >= cutoff_str]

    return jsonify(
        {
            "equity": equity_curve,
            "drawdown": drawdown_curve,
            "label": m["label"],
            "color": "#4FC3F7",
        }
    )


@app.route("/api/ea_pnl_data/<path:name>")
def api_ea_pnl_data(name):
    ea_name = unquote(name)
    parsed_data = get_parsed_data()
    if not parsed_data:
        return jsonify({"error": "No hay datos cargados"}), 400

    config = load_config()
    ea_trades = [
        t for t in parsed_data.get("closed_trades", []) if t.get("comment") == ea_name
    ]

    from metrics import calculate_ea_metrics

    m = calculate_ea_metrics(ea_name, ea_trades, config)

    pnl_list = [t["net_pnl"] for t in m["trades"]]
    streak_data = []
    for i, t in enumerate(m["trades"]):
        streak_data.append(
            {
                "index": i + 1,
                "pnl": t["net_pnl"],
                "color": "#4CAF50" if t["net_pnl"] > 0 else "#FF5252",
            }
        )

    # P/L by weekday (0=Mon..6=Sun via Python .weekday())
    weekday_pnl = [0.0] * 7
    for t in m["trades"]:
        ct = t.get("close_time")
        if ct:
            if isinstance(ct, str):
                ct = datetime.fromisoformat(ct)
            weekday_pnl[ct.weekday()] += t["net_pnl"]
    weekday_pnl = [round(v, 2) for v in weekday_pnl]

    # P/L by closing hour
    hour_pnl = [0.0] * 24
    for t in m["trades"]:
        ct = t.get("close_time")
        if ct:
            if isinstance(ct, str):
                ct = datetime.fromisoformat(ct)
            hour_pnl[ct.hour] += t["net_pnl"]
    hour_pnl = [round(v, 2) for v in hour_pnl]

    # Long vs Short breakdown
    long_list = [t for t in m["trades"] if t.get("direction") == "buy"]
    short_list = [t for t in m["trades"] if t.get("direction") == "sell"]
    long_short = {
        "long_count": len(long_list),
        "short_count": len(short_list),
        "long_pnl": round(sum(t["net_pnl"] for t in long_list), 2),
        "short_pnl": round(sum(t["net_pnl"] for t in short_list), 2),
        "long_wins": sum(1 for t in long_list if t["net_pnl"] > 0),
        "short_wins": sum(1 for t in short_list if t["net_pnl"] > 0),
    }

    # Duration vs P&L scatter (each trade as a point)
    duration_scatter = [
        {
            "x": round(float(t.get("duration_hours") or 0), 2),
            "y": round(t["net_pnl"], 2),
            "win": t["net_pnl"] > 0,
        }
        for t in m["trades"]
    ]

    return jsonify(
        {
            "pnl_list": pnl_list,
            "streak_data": streak_data,
            "weekday_pnl": weekday_pnl,
            "hour_pnl": hour_pnl,
            "long_short": long_short,
            "duration_scatter": duration_scatter,
        }
    )


@app.route("/api/incubation/ea_equity/<path:name>")
def api_incubation_ea_equity(name):
    ea_name = unquote(name)
    parsed_data = get_incubation_parsed_data()
    if not parsed_data:
        return jsonify({"error": "No hay datos de incubación cargados"}), 400

    days_param = request.args.get("days", type=int)

    config = load_incubation_config()
    ea_trades = [
        t
        for t in parsed_data.get("closed_trades", [])
        if _trade_matches_ea(t, ea_name, config)
    ]

    from metrics import calculate_ea_metrics

    m = calculate_ea_metrics(ea_name, ea_trades, config)
    equity_curve = m["equity_curve"]
    drawdown_curve = m["drawdown_curve"]

    if days_param is not None:
        cutoff = datetime.now() - timedelta(days=days_param)
        cutoff_str = cutoff.isoformat()
        equity_curve = [p for p in equity_curve if p["date"] >= cutoff_str]
        drawdown_curve = [p for p in drawdown_curve if p["date"] >= cutoff_str]

    return jsonify(
        {
            "equity": equity_curve,
            "drawdown": drawdown_curve,
            "label": m["label"],
            "color": "#4FC3F7",
        }
    )


@app.route("/api/incubation/ea_pnl_data/<path:name>")
def api_incubation_ea_pnl_data(name):
    ea_name = unquote(name)
    parsed_data = get_incubation_parsed_data()
    if not parsed_data:
        return jsonify({"error": "No hay datos de incubación cargados"}), 400

    config = load_incubation_config()
    ea_trades = [
        t
        for t in parsed_data.get("closed_trades", [])
        if _trade_matches_ea(t, ea_name, config)
    ]

    from metrics import calculate_ea_metrics

    m = calculate_ea_metrics(ea_name, ea_trades, config)
    payload = _incubation_distribution_payload(m)
    return jsonify(payload)


@app.route("/api/portfolio_analytics")
def api_portfolio_analytics():
    parsed_data = get_parsed_data()
    if not parsed_data:
        return jsonify({"error": "No hay datos cargados"}), 400

    config = load_config()
    all_metrics = _get_metrics_cached(parsed_data, config)
    portfolio = all_metrics["portfolio"]
    port_trades = portfolio.get("trades", [])

    pnl_list = [t["net_pnl"] for t in port_trades]
    streak_data = [
        {"index": i + 1, "pnl": t["net_pnl"]} for i, t in enumerate(port_trades)
    ]

    weekday_pnl = [0.0] * 7
    hour_pnl = [0.0] * 24
    for t in port_trades:
        ct = t.get("close_time")
        if ct:
            if isinstance(ct, str):
                ct = datetime.fromisoformat(ct)
            weekday_pnl[ct.weekday()] += t["net_pnl"]
            hour_pnl[ct.hour] += t["net_pnl"]
    weekday_pnl = [round(v, 2) for v in weekday_pnl]
    hour_pnl = [round(v, 2) for v in hour_pnl]

    long_list = [t for t in port_trades if t.get("direction") == "buy"]
    short_list = [t for t in port_trades if t.get("direction") == "sell"]
    long_short = {
        "long_count": len(long_list),
        "short_count": len(short_list),
        "long_pnl": round(sum(t["net_pnl"] for t in long_list), 2),
        "short_pnl": round(sum(t["net_pnl"] for t in short_list), 2),
        "long_wins": sum(1 for t in long_list if t["net_pnl"] > 0),
        "short_wins": sum(1 for t in short_list if t["net_pnl"] > 0),
    }

    duration_scatter = [
        {
            "x": round(float(t.get("duration_hours") or 0), 2),
            "y": round(t["net_pnl"], 2),
            "win": t["net_pnl"] > 0,
        }
        for t in port_trades
    ]

    return jsonify(
        {
            "pnl_list": pnl_list,
            "streak_data": streak_data,
            "weekday_pnl": weekday_pnl,
            "hour_pnl": hour_pnl,
            "long_short": long_short,
            "duration_scatter": duration_scatter,
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# Routes: Validator (Live vs Backtest comparison)
# ─────────────────────────────────────────────────────────────────────────────


@app.route("/validator")
def validator():
    guard = _require_live_mode("incubation_dashboard")
    if guard:
        return guard

    parsed_data = get_parsed_data()
    if not parsed_data:
        return redirect(url_for("index"))

    config = load_config()
    store = load_validator_store()
    sidebar_eas = build_sidebar_eas(parsed_data, config)

    rows = get_all_validator_results(parsed_data, config, store)

    return render_template(
        "validator.html",
        rows=rows,
        sidebar_eas=sidebar_eas,
        account=parsed_data.get("account", {}),
        show_sidebar=True,
        active_page="validator",
    )


@app.route("/validator/edit/<magic>", methods=["GET", "POST"])
def validator_edit(magic):
    parsed_data = get_parsed_data()
    if not parsed_data:
        return redirect(url_for("index"))

    config = load_config()
    store = load_validator_store()
    sidebar_eas = build_sidebar_eas(parsed_data, config)

    # Find EA name from magic number
    ea_name = None
    ea_label = magic
    for name, mapping in config.get("mappings", {}).items():
        if str(mapping.get("magic", "")) == str(magic):
            ea_name = name
            alias = mapping.get("alias", "") or name
            ea_label = f"{magic} - {alias}"
            break

    entry = store.get(str(magic), {})

    if request.method == "POST":

        def fv(key, default=None):
            v = request.form.get(key, "").strip()
            if v == "":
                return default
            try:
                return float(v)
            except ValueError:
                return default

        new_entry = {
            "instrument": request.form.get("instrument", "").strip(),
            "timeframe": request.form.get("timeframe", "H1").strip(),
            "bt": {
                "win_rate": fv("bt_win_rate"),
                "profit_factor": fv("bt_profit_factor"),
                "payout_ratio": fv("bt_payout_ratio"),
                "expectancy": fv("bt_expectancy"),
                "avg_bars": fv("bt_avg_bars"),
                "max_dd_pct": fv("bt_max_dd_pct"),
                "max_consec_losses": fv("bt_max_consec_losses"),
                "trades_total": fv("bt_trades_total"),
                "months": fv("bt_months"),
                "worst_dd_1m": fv("bt_worst_dd_1m"),
                "worst_dd_3m": fv("bt_worst_dd_3m"),
                "stagnation_days": fv("bt_stagnation_days"),
            },
            "mc_retest": {
                "max_dd": fv("mc_r_max_dd"),
                "profit_factor": fv("mc_r_profit_factor"),
                "win_rate": fv("mc_r_win_rate"),
                "expectancy": fv("mc_r_expectancy"),
                "stability": fv("mc_r_stability"),
            },
            "mc_trades": {
                "max_dd": fv("mc_t_max_dd"),
                "profit_factor": fv("mc_t_profit_factor"),
                "win_rate": fv("mc_t_win_rate"),
                "expectancy": fv("mc_t_expectancy"),
            },
            "spp": {
                "expectancy_median": fv("spp_expectancy_median"),
                "dd_median": fv("spp_dd_median"),
                "stagnation_median": fv("spp_stagnation_median"),
            },
        }

        store[str(magic)] = new_entry
        save_validator_store(store)
        return redirect(url_for("validator"))

    # GET: find live metrics to show as preview
    live_preview = None
    if ea_name:
        ea_trades = [
            t
            for t in parsed_data.get("closed_trades", [])
            if t.get("comment") == ea_name
        ]
        if ea_trades:
            from metrics import calculate_ea_metrics

            m = calculate_ea_metrics(ea_name, ea_trades, config)
            tf = entry.get("timeframe", "H1")
            tf_h = timeframe_to_hours(tf)
            avg_dur = m.get("avg_duration_hours") or 0
            live_preview = {
                "total_trades": m.get("total_trades", 0),
                "weeks_operating": m.get("weeks_operating", 0),
                "win_rate": m.get("win_rate", 0),
                "profit_factor": _safe_float(m.get("profit_factor")) or 0,
                "payout_ratio": _safe_float(m.get("payout_ratio")) or 0,
                "expectancy": m.get("expectancy", 0),
                "max_dd_pct": m.get("max_dd_pct", 0),
                "avg_bars_live": round(avg_dur / tf_h, 1) if tf_h > 0 else 0,
                "max_consec_losses": m.get("max_consec_losses", 0),
                "stagnation_days": m.get("stagnation_days", 0),
            }

    return render_template(
        "validator_input.html",
        magic=magic,
        ea_name=ea_name,
        ea_label=ea_label,
        entry=entry,
        live_preview=live_preview,
        sidebar_eas=sidebar_eas,
        account=parsed_data.get("account", {}),
        show_sidebar=True,
        active_page="validator",
    )


@app.route("/validator/delete/<magic>", methods=["POST"])
def validator_delete(magic):
    store = load_validator_store()
    if str(magic) in store:
        del store[str(magic)]
        save_validator_store(store)
    return redirect(url_for("validator"))


@app.route("/api/rolling_metrics/<path:name>")
def api_rolling_metrics(name):
    ea_name = unquote(name)
    parsed_data = get_parsed_data()
    if not parsed_data:
        return jsonify({"error": "No data"}), 400

    config = load_config()
    ea_trades = [
        t for t in parsed_data.get("closed_trades", []) if t.get("comment") == ea_name
    ]

    window_param = request.args.get("window", type=int)

    from metrics import _calc_rolling_metrics, calculate_ea_metrics

    m = calculate_ea_metrics(ea_name, ea_trades, config)

    total = m["total_trades"]
    if window_param and window_param >= 5:
        window = min(window_param, total)
    else:
        window = m.get("rolling_window", 15)

    rolling = _calc_rolling_metrics(m["trades"], window)

    return jsonify(
        {
            "rolling": rolling,
            "window": window,
            "total_trades": total,
            "insufficient": total < 10,
        }
    )


@app.route("/api/correlation")
def api_correlation():
    parsed_data = get_parsed_data()
    if not parsed_data:
        return jsonify({"error": "No data"}), 400

    config = load_config()
    from collections import defaultdict

    import numpy as np

    from metrics import calculate_all_metrics

    all_metrics = calculate_all_metrics(parsed_data, config)

    if len(all_metrics["by_ea"]) < 2:
        return jsonify({"error": "need_2_eas"})

    # P&L diario por EA
    ea_daily = {}
    for ea_name, m in all_metrics["by_ea"].items():
        label = get_display_label(ea_name, config)
        daily = defaultdict(float)
        for t in m["trades"]:
            ct = t["close_time"]
            if isinstance(ct, str):
                ct = datetime.fromisoformat(ct)
            d = ct.date().isoformat()
            daily[d] += t["net_pnl"]
        ea_daily[label] = daily

    # Todas las fechas con actividad
    all_dates = sorted(set(d for dly in ea_daily.values() for d in dly))

    if len(all_dates) < 5:
        return jsonify({"error": "insufficient_data"})

    ea_names = list(ea_daily.keys())
    matrix_raw = np.array(
        [[ea_daily[n].get(d, 0.0) for d in all_dates] for n in ea_names]
    )

    # Correlación de Pearson; si desv=0 para un EA, devolver 0.0
    corr = np.full((len(ea_names), len(ea_names)), 0.0)
    for i in range(len(ea_names)):
        for j in range(len(ea_names)):
            if i == j:
                corr[i][j] = 1.0
            else:
                xi = matrix_raw[i]
                xj = matrix_raw[j]
                if xi.std() == 0 or xj.std() == 0:
                    corr[i][j] = 0.0
                else:
                    corr[i][j] = round(float(np.corrcoef(xi, xj)[0, 1]), 3)

    # Detectar pares con correlación alta (> 0.7)
    high_corr_pairs = []
    for i in range(len(ea_names)):
        for j in range(i + 1, len(ea_names)):
            c = corr[i][j]
            if abs(c) > 0.7:
                high_corr_pairs.append(
                    {
                        "ea1": ea_names[i],
                        "ea2": ea_names[j],
                        "corr": round(c, 3),
                    }
                )

    return jsonify(
        {
            "labels": ea_names,
            "matrix": corr.tolist(),
            "high_corr_pairs": high_corr_pairs,
            "n_days": len(all_dates),
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────


PORT = int(os.environ.get("EA_PORT", 5000))


def open_browser():
    time.sleep(1.2)
    webbrowser.open(f"http://localhost:{PORT}")


if __name__ == "__main__":
    print("=" * 50)
    print(f"  EA Analyzer - iniciando servidor...")
    print(f"  Abriendo http://localhost:{PORT}")
    print(f"  Presiona Ctrl+C para detener")
    print("=" * 50)
    threading.Thread(target=open_browser, daemon=True).start()
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)
