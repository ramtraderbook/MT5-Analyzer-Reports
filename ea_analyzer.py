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
os.makedirs(os.path.join(APP_DIR, "test_data"), exist_ok=True)


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


def save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


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


def save_cache(data):
    """Save parsed data to a cache file. Returns the cache key."""
    cache_key = str(uuid.uuid4())
    cache_path = os.path.join(APP_DIR, f"cache_{cache_key}.json")
    serialized = _serialize_parsed_data(data)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(serialized, f, default=str)
    return cache_key


def load_cache(cache_key):
    """Load cached parsed data. Returns dict or None."""
    if not cache_key:
        return None
    cache_path = os.path.join(APP_DIR, f"cache_{cache_key}.json")
    if not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def cleanup_old_caches():
    """Delete cache files older than 2 hours."""
    pattern = os.path.join(APP_DIR, "cache_*.json")
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


# ─────────────────────────────────────────────────────────────────────────────
# Routes: Upload
# ─────────────────────────────────────────────────────────────────────────────


@app.route("/")
def index():
    config = load_config()
    return render_template(
        "upload.html", last_file=config.get("last_file"), show_sidebar=False
    )


@app.route("/upload", methods=["POST"])
def upload():
    cleanup_old_caches()

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

    # Save uploaded file
    safe_name = os.path.basename(file.filename)
    filepath = os.path.join(UPLOAD_FOLDER, safe_name)
    file.save(filepath)

    # Parse
    try:
        from parser import parse_mt5_report

        parsed_data = parse_mt5_report(filepath)
    except ValueError as e:
        return render_template("upload.html", error=str(e), show_sidebar=False)
    except Exception as e:
        return render_template(
            "upload.html",
            error=f"Error inesperado al parsear el archivo: {e}",
            show_sidebar=False,
        )

    # Cache parsed data
    cache_key = save_cache(parsed_data)
    session["cache_key"] = cache_key
    session["filename"] = safe_name

    # Update config with last file
    config = load_config()
    config["last_file"] = safe_name
    config["last_updated"] = str(date.today())
    save_config(config)

    return redirect(url_for("mapping"))


# ─────────────────────────────────────────────────────────────────────────────
# Routes: Magic Number Mapping
# ─────────────────────────────────────────────────────────────────────────────


@app.route("/mapping")
def mapping():
    parsed_data = get_parsed_data()
    if not parsed_data:
        return redirect(url_for("index"))

    config = load_config()
    trades = parsed_data.get("closed_trades", [])

    ea_list = []
    for ea_name in parsed_data.get("ea_names", []):
        ea_trades = [t for t in trades if t.get("comment") == ea_name]
        existing = config.get("mappings", {}).get(ea_name, {})

        # Detect instrument from trades
        symbols = list(set(t["symbol"] for t in ea_trades if t.get("symbol")))
        instrument = (
            symbols[0]
            if len(symbols) == 1
            else (", ".join(sorted(symbols)) if symbols else "")
        )

        ea_list.append(
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
    parsed_data = get_parsed_data()
    if not parsed_data:
        return redirect(url_for("index"))

    config = load_config()
    mappings = config.setdefault("mappings", {})

    for ea_name in parsed_data.get("ea_names", []):
        magic_val = request.form.get(f"magic_{ea_name}", "").strip()
        instrument_val = request.form.get(f"instrument_{ea_name}", "").strip()
        capital_val = request.form.get(f"capital_{ea_name}", "").strip()

        # Preserve existing mapping but update all fields
        existing = mappings.get(ea_name, {})

        entry = dict(existing)  # start from existing to preserve any extra fields
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

        # Alias (display name) — optional, empty = use original name
        alias_val = request.form.get(f"alias_{ea_name}", "").strip()
        entry["alias"] = alias_val  # store empty string if not set

        # Checkbox: present in form = checked, absent = unchecked
        entry["active"] = f"include_{ea_name}" in request.form

        if entry:
            mappings[ea_name] = entry

    config["last_updated"] = str(date.today())
    save_config(config)

    return redirect(url_for("dashboard"))


# ─────────────────────────────────────────────────────────────────────────────
# Routes: Dashboard
# ─────────────────────────────────────────────────────────────────────────────


@app.route("/dashboard")
def dashboard():
    parsed_data = get_parsed_data()
    if not parsed_data:
        return redirect(url_for("index"))

    config = load_config()
    from metrics import calculate_all_metrics

    all_metrics = calculate_all_metrics(parsed_data, config)

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

    # Portfolio monthly performance table
    from collections import defaultdict

    port_trades = portfolio.get("trades", [])
    port_monthly_data = defaultdict(lambda: defaultdict(float))
    port_monthly_has = defaultdict(set)
    for t in port_trades:
        ct = t.get("close_time")
        if ct:
            if isinstance(ct, str):
                ct = datetime.fromisoformat(ct)
            port_monthly_data[ct.year][ct.month] += t.get("net_pnl", 0)
            port_monthly_has[ct.year].add(ct.month)

    portfolio_monthly_perf = []
    for year in sorted(port_monthly_data.keys(), reverse=True):
        months_vals = []
        for mo in range(1, 13):
            if mo in port_monthly_has[year]:
                months_vals.append(round(port_monthly_data[year][mo], 2))
            else:
                months_vals.append(None)
        ytd = round(
            sum(port_monthly_data[year][mo] for mo in port_monthly_has[year]), 2
        )
        portfolio_monthly_perf.append({"year": year, "months": months_vals, "ytd": ytd})

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

    # Build monthly performance table
    from collections import defaultdict

    monthly_data = defaultdict(lambda: defaultdict(float))
    monthly_has_data = defaultdict(set)
    for t in m["trades"]:
        ct = t.get("close_time")
        if ct:
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
    parsed_data = get_parsed_data()
    if not parsed_data:
        return redirect(url_for("index"))

    config = load_config()
    from metrics import calculate_all_metrics

    all_metrics = calculate_all_metrics(parsed_data, config)

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
    from metrics import EA_COLORS, calculate_all_metrics

    all_metrics = calculate_all_metrics(parsed_data, config)

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

    all_metrics = calculate_all_metrics(parsed_data, config)

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

    all_metrics = calculate_all_metrics(parsed_data, config)

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


@app.route("/api/portfolio_analytics")
def api_portfolio_analytics():
    parsed_data = get_parsed_data()
    if not parsed_data:
        return jsonify({"error": "No hay datos cargados"}), 400

    config = load_config()
    from metrics import calculate_all_metrics

    all_metrics = calculate_all_metrics(parsed_data, config)
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
# Entry point
# ─────────────────────────────────────────────────────────────────────────────


def open_browser():
    time.sleep(1.2)
    webbrowser.open("http://localhost:5000")


if __name__ == "__main__":
    print("=" * 50)
    print("  EA Analyzer - iniciando servidor...")
    print("  Abriendo http://localhost:5000")
    print("  Presiona Ctrl+C para detener")
    print("=" * 50)
    threading.Thread(target=open_browser, daemon=True).start()
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
