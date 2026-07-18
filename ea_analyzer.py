"""
ea_analyzer.py - EA Analyzer & Validator
Flask web application for analyzing MetaTrader 5 Expert Advisor performance.

Usage: python ea_analyzer.py
Opens http://localhost:5000 in the default browser.
"""

import hashlib
import hmac
import json
import logging
import math
import os
import secrets
import threading
import time
import webbrowser
from datetime import date, datetime, timedelta
from urllib.parse import quote, unquote

from flask import (
    Flask,
    abort,
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
from local_json import load_local_json, save_local_json
from trade_matching import trade_matches_ea
import cache_store
from incubation_domain import (
    build_comparison_rows,
    build_distribution_payload,
    build_monthly_performance,
    build_reference_form_values,
    build_timeline_from_entry,
    build_verdict_card,
    checkpoint_for_trades,
    compute_spp_ratios,
    count_cp1_hard_gates,
    current_result_from_entry,
    days_since_first_trade,
    evaluate_ea,
    metric_summary_for_tooltip,
    parse_reference_form,
    reference_ready,
    reference_sections_for_render,
)

# ─────────────────────────────────────────────────────────────────────────────
# Setup
# ─────────────────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(APP_DIR, "templates")
STATIC_DIR = os.path.join(APP_DIR, "static")
UPLOAD_FOLDER = os.path.join(APP_DIR, "uploads")
CACHE_DIR = os.path.join(APP_DIR, "runtime_cache")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
SECRET_KEY_PATH = os.path.join(APP_DIR, ".secret_key")

app = Flask(__name__, template_folder=TEMPLATES_DIR, static_folder=STATIC_DIR)


def _resolve_secret_key(persist_if_created=False):
    """Resolve the Flask secret key without writing to disk at import time (1A).

    Precedence: the EA_ANALYZER_SECRET_KEY env var (the WSGI-friendly source,
    stable across workers with no file involved) -> the persisted .secret_key
    file if it already exists -> a fresh ephemeral key. The ephemeral key is
    only written to disk when persist_if_created is True, which happens at real
    startup (__main__) -- never on plain import or under tests, so importing
    this module has no filesystem side effects."""
    env_key = os.environ.get("EA_ANALYZER_SECRET_KEY")
    if env_key:
        return env_key.encode("utf-8")
    if os.path.exists(SECRET_KEY_PATH):
        with open(SECRET_KEY_PATH, "rb") as f:
            return f.read()
    key = os.urandom(24)
    if persist_if_created:
        with open(SECRET_KEY_PATH, "wb") as f:
            f.write(key)
    return key


# Set at import so sessions/CSRF work immediately (incl. test_client), but never
# writing a file here -- the durable key is persisted at startup (see __main__).
app.secret_key = _resolve_secret_key()


def _ensure_dir(path):
    """Create a runtime directory on demand. Called at each write site instead
    of at import so that importing this module touches no filesystem (1A)."""
    os.makedirs(path, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# Metrics cache (in-memory, por sesión, TTL 120 segundos)
# Evita recalcular calculate_all_metrics() en cada llamada API del dashboard.
# ─────────────────────────────────────────────────────────────────────────────

_metrics_cache: dict = {}  # { "cache_key:config_hash": {"ts": float, "result": dict} }
_metrics_cache_lock = threading.Lock()
_METRICS_TTL = 120  # segundos
LIVE_CACHE_PREFIX = "cache_"
ANALYSIS_MODES = {
    "live": "Live Validation",
    "incubation": "Incubation Screening",
}
INCUBATION_CACHE_PREFIX = "incubation_cache_"
INCUBATION_STORE_PATH = os.path.join(APP_DIR, "incubation_store.json")
INCUBATION_CONFIG_PATH = os.path.join(APP_DIR, "incubation_config.json")
LIVE_CONFIG_DEFAULT = {
    "mappings": {},
    "last_file": None,
    "last_updated": None,
    "loaded_files_live": [],
}
INCUBATION_CONFIG_DEFAULT = {
    "mappings": {},
    "last_file": None,
    "last_updated": None,
    "loaded_files_incubation": [],
}


def _config_metrics_hash(config: dict) -> str:
    """Stable hash of the config subset that affects computed metrics."""
    payload = json.dumps(config.get("mappings", {}), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _get_metrics_cached(parsed_data: dict, config: dict) -> dict:
    """
    Devuelve calculate_all_metrics() desde cache si sigue vigente.
    La clave combina el cache_key de sesión con un hash del config que
    afecta a las métricas (mappings): un config reemplazado produce una
    clave distinta y nunca puede servir una entrada calculada con el
    config anterior -- en ningún proceso/worker, sin depender de que cada
    call site recuerde invalidar.
    """
    from metrics import calculate_all_metrics

    cache_key = session.get("cache_key", "__no_key__")
    combined_key = f"{cache_key}:{_config_metrics_hash(config)}"
    now = time.time()

    with _metrics_cache_lock:
        entry = _metrics_cache.get(combined_key)
    if entry and (now - entry["ts"]) < _METRICS_TTL:
        return entry["result"]

    # Slow computation stays outside the lock.
    result = calculate_all_metrics(parsed_data, config)

    with _metrics_cache_lock:
        _metrics_cache[combined_key] = {"ts": now, "result": result}

        # Limpiar entradas viejas (evitar memoria ilimitada)
        stale = [k for k, v in _metrics_cache.items() if now - v["ts"] > _METRICS_TTL * 10]
        for k in stale:
            _metrics_cache.pop(k, None)

    return result


def invalidate_metrics_cache():
    """Llamar tras subir un nuevo archivo para forzar recálculo."""
    cache_key = session.get("cache_key", "__no_key__")
    prefix = f"{cache_key}:"
    with _metrics_cache_lock:
        stale = [k for k in _metrics_cache if k.startswith(prefix)]
        for k in stale:
            _metrics_cache.pop(k, None)


# ─────────────────────────────────────────────────────────────────────────────
# Config helpers
# ─────────────────────────────────────────────────────────────────────────────


def load_config():
    return load_local_json(CONFIG_PATH, LIVE_CONFIG_DEFAULT)


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


# ─────────────────────────────────────────────────────────────────────────────
# CSRF protection (dependency-free, session-bound token)
# ─────────────────────────────────────────────────────────────────────────────


def _get_csrf_token():
    """Return this session's CSRF token, creating one on first use."""
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_hex(32)
        session["csrf_token"] = token
    return token


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": _get_csrf_token}


@app.context_processor
def inject_display_thresholds():
    """Single source for the cut constants the templates color and gate by, so
    the UI can never silently color by a threshold the engine no longer uses
    (4D). Engine-owned verdict thresholds are read from the engine itself; the
    checkpoint gates mirror incubation_validator.get_checkpoint_for_trades and
    are pinned to it by an anti-drift test; the SQN/PF cuts are UI-only color
    choices with no engine equivalent, defined here once instead of in every
    template."""
    from validator import CONFIG as _VC

    return {
        "TH": {
            "score_continuar": _VC["thresh_continuar"],    # 70
            "score_monitorear": _VC["thresh_monitorear"],  # 45
            "cp1_min": 5,
            "cp2_min": 20,
            "cp3_min": 40,
            "sqn_good": 2.0,
            "sqn_bad": 1.6,
            "pf_good": 1.5,
            "pf_bad": 1.0,
            "borderline_eps": 2.0,
        }
    }


def _tokens_match(expected, provided):
    # compare_digest raises TypeError on non-ASCII str, and `provided` is
    # client-supplied -- compare raw bytes so a junk token is rejected, not a 500.
    return hmac.compare_digest(expected.encode("utf-8"), provided.encode("utf-8"))


@app.before_request
def _enforce_csrf():
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None

    expected = session.get("csrf_token")
    provided = request.form.get("csrf_token") or request.headers.get("X-CSRFToken", "")
    if not expected or not provided or not _tokens_match(expected, provided):
        abort(400)
    return None


def save_config(config):
    save_local_json(CONFIG_PATH, config)


def load_incubation_store():
    return load_local_json(INCUBATION_STORE_PATH, {})


def save_incubation_store(data):
    save_local_json(INCUBATION_STORE_PATH, data)


def load_incubation_config():
    return load_local_json(INCUBATION_CONFIG_PATH, INCUBATION_CONFIG_DEFAULT)


def save_incubation_config(data):
    save_local_json(INCUBATION_CONFIG_PATH, data)


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

        # Defensive backfill (design §2, F4 correction): every entry should
        # carry a `date_added` -- the authoritative incubation clock start
        # (`_incubation_start_date` in incubation_validator.py). Not
        # currently reachable (existing stores have no entries lacking it,
        # since ea_analyzer.py stamps it on every save), but a migration
        # must not leave an entry without a clock start if one is ever
        # loaded from an older/foreign store.
        if not data.get("date_added"):
            data["date_added"] = str(date.today())
            modified = True

    if modified:
        save_incubation_store(store)


migrate_incubation_store()


# ─────────────────────────────────────────────────────────────────────────────
# Cache helpers
# ─────────────────────────────────────────────────────────────────────────────


# The disk storage layer lives in cache_store.py (pure of Flask/session). These
# thin wrappers bind it to this module's runtime config (CACHE_DIR / APP_DIR and
# the live/incubation prefixes), read at call time so tests can monkeypatch them.


def _serialize_parsed_data(data):
    return cache_store.serialize_parsed_data(data)


def _cache_file_path(cache_key, prefix):
    return cache_store.cache_file_path(CACHE_DIR, cache_key, prefix)


def _legacy_cache_file_path(cache_key, prefix):
    return cache_store.legacy_cache_file_path(APP_DIR, cache_key, prefix)


def _resolve_cache_path(cache_key, prefix):
    return cache_store.resolve_cache_path(CACHE_DIR, APP_DIR, cache_key, prefix)


def _delete_cache_file(cache_key, prefix):
    return cache_store.delete_cache_file(CACHE_DIR, APP_DIR, cache_key, prefix)


def _atomic_write_json(path, data):
    return cache_store.atomic_write_json(path, data)


def save_cache(data):
    """Save parsed live data to a cache file. Returns the cache key."""
    return cache_store.save_cache(CACHE_DIR, data, LIVE_CACHE_PREFIX)


def load_cache(cache_key):
    """Load cached live parsed data. Returns dict or None."""
    return cache_store.load_cache(CACHE_DIR, APP_DIR, cache_key, LIVE_CACHE_PREFIX)


def save_incubation_cache(data):
    """Save incubation parsed data to a separate cache file."""
    return cache_store.save_cache(CACHE_DIR, data, INCUBATION_CACHE_PREFIX)


def load_incubation_cache(cache_key):
    """Load incubation cached parsed data. Returns dict or None."""
    return cache_store.load_cache(CACHE_DIR, APP_DIR, cache_key, INCUBATION_CACHE_PREFIX)


def cleanup_old_caches(keep_live_key=None, keep_incubation_key=None):
    """
    Delete cache files older than 2 hours across the live and incubation
    prefixes (canonical and legacy dirs).

    Cache files backing the CURRENT session (`keep_live_key` /
    `keep_incubation_key`) are never deleted here, no matter their mtime --
    an actively used dataset must survive a re-upload made more than 2h
    after the previous one.
    """
    protected_paths = set()
    if keep_live_key:
        protected_paths.add(_cache_file_path(keep_live_key, LIVE_CACHE_PREFIX))
        protected_paths.add(_legacy_cache_file_path(keep_live_key, LIVE_CACHE_PREFIX))
    if keep_incubation_key:
        protected_paths.add(_cache_file_path(keep_incubation_key, INCUBATION_CACHE_PREFIX))
        protected_paths.add(_legacy_cache_file_path(keep_incubation_key, INCUBATION_CACHE_PREFIX))

    cache_store.cleanup_old_caches(
        CACHE_DIR,
        APP_DIR,
        [LIVE_CACHE_PREFIX, INCUBATION_CACHE_PREFIX],
        protected_paths,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Display helpers
# ─────────────────────────────────────────────────────────────────────────────


def get_display_label(ea_name, config):
    mapping = config.get("mappings", {}).get(ea_name, {})
    magic = mapping.get("magic")
    alias = mapping.get("alias", "") or ea_name
    return f"{magic} - {alias}" if magic else alias


def _ea_is_active(ea_name, config):
    """Same active-flag default used everywhere else: unmapped EAs stay active."""
    return config.get("mappings", {}).get(ea_name, {}).get("active", True)


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


def _apply_capital_value(entry, capital_val, ea_name):
    """
    Parse a submitted capital value into `entry["capital"]`.

    Matches the magic-number policy: an invalid, non-positive or non-finite
    submission never clobbers a previously saved value -- it just keeps
    whatever was there (or the 5000.0 default for a brand-new EA) and warns
    the user by name instead of silently corrupting every capital-scaled
    metric downstream.
    """
    if not capital_val:
        entry.setdefault("capital", 5000.0)
        return

    try:
        parsed_capital = float(capital_val)
    except ValueError:
        parsed_capital = None

    if parsed_capital is not None and math.isfinite(parsed_capital) and parsed_capital > 0:
        entry["capital"] = parsed_capital
    else:
        had_previous = "capital" in entry
        entry.setdefault("capital", 5000.0)
        kept = "se mantiene el valor anterior" if had_previous else "se usa el valor por defecto"
        flash(f"Capital inválido para {ea_name}, {kept} ({entry['capital']:g}).", "warn")


def build_mapping_rows(parsed_data, config):
    trades = parsed_data.get("closed_trades", [])
    rows = []

    for ea_name in parsed_data.get("ea_names", []):
        ea_trades = [t for t in trades if trade_matches_ea(t, ea_name, config)]
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
    sin_datos_count = 0
    active_mappings = config.get("mappings", {})
    store_dirty = False

    try:
        for ea_name, mapping in active_mappings.items():
            if not mapping.get("active", True):
                continue

            entry = store.get(ea_name, {})
            evaluation_bundle = evaluate_ea(ea_name, parsed_data, config, entry)
            if evaluation_bundle and evaluation_bundle["reference_ready"]:
                store[ea_name] = evaluation_bundle["entry"]
                store_dirty = True
            metrics = evaluation_bundle["metrics"] if evaluation_bundle else None
            evaluation = evaluation_bundle["evaluation"] if evaluation_bundle else None
            total_trades = int(metrics.get("total_trades") or 0) if metrics else 0
            days = days_since_first_trade(evaluation_bundle["trades"] if evaluation_bundle else [])
            checkpoint_key, checkpoint_label, checkpoint_class = checkpoint_for_trades(total_trades)

            # JD-5 C6: has_reference used to come from the evaluate_ea bundle,
            # but evaluate_ea() returns None whenever the EA has zero matched
            # trades -- even when the store entry's reference data is fully
            # loaded (evaluation_bundle is then None too). Derive readiness
            # from the store entry itself, via the same reference_ready()
            # helper the strategy detail page already uses, so a zero-trade
            # EA with complete reference data is not reported as missing it.
            has_reference = reference_ready(entry)
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
                tooltip = metric_summary_for_tooltip(evaluation)
                url = url_for("incubation_strategy", ea_name=ea_name)
                cp = evaluation.get("current_checkpoint", "")
                details = evaluation.get("details", {})
                if verdict == "SIN DATOS":
                    # Missing required reference/live data (design §1): never
                    # borrow a checkpoint-specific status label built for a
                    # confident verdict.
                    missing = evaluation.get("missing") or []
                    status_label = f"SIN DATOS ({len(missing)})" if missing else "SIN DATOS"
                    status_class = "verdict-no-data"
                elif cp == "PRE_CP1":
                    if verdict == "ELIMINAR":
                        # JD-5 C7: PRE_CP1 can be eliminated by the engine on a
                        # frequency-deadline breach (evaluate_incubation); do
                        # not assert "still waiting for trades" for an EA the
                        # engine already eliminated.
                        status_label = "Frecuencia perdida"
                        status_class = "verdict-eliminate"
                    else:
                        status_label = "Esperando trades"
                        status_class = "verdict-pending"
                elif cp == "CP1":
                    hg = details.get("gates", {})
                    # JD-5 C4: count via the shared helper (matches the
                    # tooltip's rule for the "frequency" gate, which never
                    # carries a "passed" key) so a fully-passing CP1 EA shows
                    # 4/4 here, not "3/4".
                    passed, total = count_cp1_hard_gates(hg)
                    status_label = f"Gates {passed}/{total}"
                    status_class = "verdict-continue" if verdict == "CONTINUAR" else "verdict-eliminate"
                elif cp == "CP2":
                    failing_count = details.get("failing_count")
                    if failing_count is None:
                        # JD-5 C5: the CP2 hard-gate branch never evaluates
                        # bands (metrics_evaluation:{}, failing_count: None) --
                        # do not assert "Bandas MC OK" for bands the engine
                        # never checked. Surface the hard-gate failure instead.
                        hard_gate_failures = details.get("hard_gate_failures") or []
                        status_label = (
                            "Hard gates: " + ", ".join(hard_gate_failures)
                            if hard_gate_failures
                            else "Hard gate failed"
                        )
                    else:
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
                elif verdict == "SIN DATOS":
                    sin_datos_count += 1
            elif has_reference:
                # JD-5 C6: reference data is complete but the EA has zero
                # matched trades yet, so evaluate_ea() returned None before
                # ever reaching a verdict. Keep this "no trades yet" state
                # visually and textually distinct from "reference data
                # missing" -- do not run the full engine evaluation here,
                # that is out of scope for this fix.
                verdict = "PENDING"
                tooltip = "Sin trades registrados todavía para esta estrategia."
                status_label = "Sin trades"
                status_class = "verdict-pending"
                # JD-5 C9: no enlazar a incubation_strategy, que para una EA sin
                # trades hace evaluate_ea() -> None y rebota al dashboard con un
                # flash. Llevar a los datos de referencia de esta EA, que sí
                # existen y son lo único revisable en este estado.
                url = url_for("incubation_reference_edit", ea_name=ea_name)
                pending_count += 1

            # F5 correction: SIN DATOS is deliberately never persisted into a
            # cp1/cp2/cp3 slot (design §1), so `current_result_from_entry`
            # would fall back to whatever CONFIDENT result is still sitting
            # in a stale slot and overwrite score_display with a numeric
            # score that no longer applies -- a row would render
            # "SIN DATOS" next to a stale "72.50". Skip the stale-slot
            # lookup entirely when the current verdict is SIN DATOS so the
            # placeholder set above ("--") survives.
            if evaluation and verdict != "SIN DATOS":
                checkpoint_record = current_result_from_entry(evaluation_bundle["entry"])
                # JD-5 C2: current_result_from_entry() prefers cp3 > cp2 >
                # cp1 regardless of the checkpoint just evaluated, so a
                # stale higher-slot score (e.g. a leftover CP3 result) would
                # overwrite a CURRENT CP1/CP2 score_display that the engine
                # never computed. Only trust the stored record when it is
                # actually for the checkpoint being rendered right now.
                if checkpoint_record and checkpoint_record.get("current_checkpoint") == cp:
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
                        "SIN DATOS": "verdict-no-data",
                    }.get(verdict, "verdict-pending"),
                    "has_reference": has_reference,
                    "url": url,
                    "tooltip": tooltip,
                    "no_data": not has_reference,
                }
            )
    finally:
        if store_dirty:
            save_incubation_store(store)

    return {
        "rows": rows,
        "total_active": len(rows),
        "pending_bt_mc": pending_bt_mc,
        "eliminar_count": eliminar_count,
        "aprobar_count": aprobar_count,
        "observar_count": observar_count,
        "continuar_count": continuar_count,
        "pending_count": pending_count,
        "sin_datos_count": sin_datos_count,
    }


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


def _canonical_trade(t: dict) -> dict:
    """
    Return a copy of trade dict `t` with every datetime value normalized to
    its ISO string form (via .isoformat()); all other values pass through
    unchanged.

    Needed because cached trades (loaded from JSON via load_cache) carry
    open_time/close_time as ISO strings — _serialize_parsed_data() converted
    them on the way to disk — while merge_trades() is new-wins, so any
    overlapping trade in a fresh merge carries the freshly-parsed datetime
    objects that parser.py always produces. Comparing those dicts with raw
    equality would report a change on type alone even when the values are
    identical, so both sides must be normalized to the same canonical form
    before comparison.
    """
    return {
        k: (v.isoformat() if isinstance(v, datetime) else v)
        for k, v in t.items()
    }


def _merge_changed_content(existing_trades: list, merged_trades: list) -> bool:
    """
    True if merging produced any real content change vs existing_trades —
    either new position_ids were added, OR an existing position_id's trade
    dict was replaced with different field values (e.g. a broker
    correction re-upload: same position_id, updated commission/net_pnl).

    False only when merged_trades is content-identical to existing_trades
    (the legitimate "same file re-uploaded, nothing changed" fast path).

    A plain count comparison (len(merged) - len(existing) == 0) is NOT
    enough: a corrections-only re-upload has the same position_ids, so
    the count never changes even though field values did — silently
    discarding the "new data wins" merge that merge_trades() performs.

    Trades are compared via _canonical_trade() on BOTH sides, normalizing
    any datetime value to its ISO string form. This is required because the
    cache round-trip (JSON) turns open_time/close_time into ISO strings,
    while a fresh merge carries datetime objects for the same field — the
    types differ even when the values are identical, and a raw dict
    equality check would wrongly report a change (defeating this exact
    fast path) without it.
    """
    if len(merged_trades) != len(existing_trades):
        return True
    existing_by_id = {t["position_id"]: _canonical_trade(t) for t in existing_trades}
    for t in merged_trades:
        pid = t["position_id"]
        if pid not in existing_by_id or existing_by_id[pid] != _canonical_trade(t):
            return True
    return False


@app.route("/upload", methods=["POST"])
def upload():
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
    _ensure_dir(UPLOAD_FOLDER)
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
        # Run stale-cache GC only after the current session's own data was
        # read, and never let it delete the file that read just came from.
        cleanup_old_caches(
            keep_live_key=session.get("cache_key"),
            keep_incubation_key=old_inc_cache_key,
        )
        added_count_inc = len(new_data["closed_trades"])

        if existing_inc_data:
            existing_trades_inc = existing_inc_data.get("closed_trades", [])
            from parser import merge_trades
            merged_inc = merge_trades(existing_trades_inc, new_data["closed_trades"])
            added_count_inc = len(merged_inc) - len(existing_trades_inc)
            if not _merge_changed_content(existing_trades_inc, merged_inc):
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
        session["incubation_filename"] = safe_name

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
    # Run stale-cache GC only after the current session's own data was read,
    # and never let it delete the file that read just came from.
    cleanup_old_caches(
        keep_live_key=old_cache_key,
        keep_incubation_key=session.get("incubation_cache_key"),
    )
    added_count = len(new_data["closed_trades"])

    if existing_data:
        existing_trades = existing_data.get("closed_trades", [])
        merged_trades = merge_trades(existing_trades, new_data["closed_trades"])
        added_count = len(merged_trades) - len(existing_trades)

        # Same file re-uploaded with truly nothing changed: keep the current
        # session data and reopen the app. A corrections-only re-upload
        # (same position_ids, updated fields) must NOT hit this fast path —
        # added_count alone can't detect that, so check real content change.
        if not _merge_changed_content(existing_trades, merged_trades):
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
    guard = _require_live_mode()
    if guard:
        return guard

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
    guard = _require_live_mode()
    if guard:
        return guard

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
    save_validator_store({})

    flash("Reset completo Live — todos los datos eliminados.", "success")
    return redirect(url_for("index"))


def _clear_incubation_session_cache():
    incubation_cache_key = session.get("incubation_cache_key")
    _delete_cache_file(incubation_cache_key, INCUBATION_CACHE_PREFIX)

    session.pop("incubation_cache_key", None)
    session.pop("incubation_filename", None)


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
        filename=session.get("incubation_filename", ""),
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
        _apply_capital_value(entry, capital_val, ea_name)

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
    invalidate_metrics_cache()

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
        _apply_capital_value(entry, capital_val, ea_name)

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
    invalidate_metrics_cache()

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
        filename=session.get("incubation_filename", ""),
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
    form_values = build_reference_form_values(entry=entry)
    spp_ratios = compute_spp_ratios(entry.get("backtest", {}), entry.get("spp", {}))

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
        sections=reference_sections_for_render(entry),
        spp_ratios=spp_ratios,
        warnings=warnings,
        errors={},
        show_sidebar=False,
        active_page="incubation_reference_data",
        filename=session.get("incubation_filename", ""),
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

    payload, errors, warnings = parse_reference_form(request.form)
    if errors:
        form_values = build_reference_form_values(form=request.form)
        spp_ratios = compute_spp_ratios(form_values.get("backtest", {}), form_values.get("spp", {}))
        return render_template(
            "incubation_reference_edit.html",
            ea_name=ea_name,
            mapping=mapping,
            entry=payload,
            form_values=form_values,
            sections=reference_sections_for_render(payload),
            spp_ratios=spp_ratios,
            warnings=warnings,
            errors=errors,
            show_sidebar=False,
            active_page="incubation_reference_data",
            filename=session.get("incubation_filename", ""),
        )

    store = load_incubation_store()
    existing = store.get(ea_name, {})
    checkpoints = existing.get("checkpoints") or {"cp1": None, "cp2": None, "cp3": None}
    date_added = existing.get("date_added") or str(date.today())

    # JD-5 C1: a fully-blank Backtest submission parses to {} with ZERO
    # form errors (parse_reference_form only errors a required section
    # when it has SOME value), so an unguarded assignment here silently
    # wipes the 12 mandatory backtest fields on every re-save that leaves
    # this section blank. Preserve the existing backtest the same way
    # MC/SPP already do below, so timeframe/bt_period (sourced from this
    # same dict a few lines down) are protected too.
    backtest = payload["backtest"] or existing.get("backtest", {})
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
    parsed_data = get_incubation_parsed_data()
    config = load_incubation_config()
    store = load_incubation_store()
    entry = store.get(ea_name, {})
    bundle = evaluate_ea(ea_name, parsed_data, config, entry)
    if bundle is None:
        flash("No hay trades de incubación para esa estrategia.", "warn")
        return redirect(url_for("incubation_dashboard"))

    if bundle["reference_ready"]:
        store[ea_name] = bundle["entry"]
        save_incubation_store(store)

    metrics = bundle["metrics"]
    config = bundle["config"]
    entry = bundle["entry"]
    evaluation = bundle["evaluation"]
    ea_trades = bundle["trades"]
    mapping = config.get("mappings", {}).get(ea_name, {})

    total_trades = int(metrics.get("total_trades") or 0)
    days = days_since_first_trade(ea_trades)
    checkpoint_key, checkpoint_label, checkpoint_class = checkpoint_for_trades(total_trades)
    has_reference = reference_ready(entry)
    display_rows = build_comparison_rows(metrics, entry)
    verdict_card = build_verdict_card(evaluation)
    checkpoint_timeline = build_timeline_from_entry(entry)
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

    monthly_perf = build_monthly_performance(metrics["trades"])
    chart_label = metrics.get("label") or mapping.get("alias") or ea_name
    inc_equity_series = {
        "equity": metrics.get("equity_curve", []),
        "drawdown": metrics.get("drawdown_curve", []),
        "label": chart_label,
        "color": "#4FC3F7",
    }
    inc_distribution = build_distribution_payload(metrics)

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
        filename=session.get("incubation_filename", ""),
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
        sin_datos_count=dashboard_data["sin_datos_count"],
        show_sidebar=True,
        active_page="incubation_dashboard",
        filename=session.get("incubation_filename", ""),
    )


@app.route("/incubation/force_evaluate/<path:ea_name>", methods=["POST"])
def incubation_force_evaluate(ea_name):
    guard = _require_incubation_mode()
    if guard:
        return guard

    ea_name = unquote(ea_name)
    parsed_data = get_incubation_parsed_data()
    config = load_incubation_config()
    store = load_incubation_store()
    entry = store.get(ea_name, {})
    bundle = evaluate_ea(ea_name, parsed_data, config, entry)
    if bundle is None:
        flash("No hay trades de incubación para reevaluar.", "warn")
        return redirect(url_for("incubation_dashboard"))

    if bundle["reference_ready"]:
        store[ea_name] = bundle["entry"]
        save_incubation_store(store)

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

    portfolio_monthly_perf = build_monthly_performance(portfolio.get("trades", []))

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
    if not _ea_is_active(ea_name, config):
        flash("Esa estrategia está desactivada.", "warn")
        return redirect(url_for("dashboard"))

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

    monthly_perf = build_monthly_performance(m["trades"])

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


DAYS_PARAM_MAX = 36500  # ~100 years -- generous upper bound, blocks OverflowError


def _validated_days_param():
    """
    Parse the optional `days` query arg shared by chart endpoints.
    Returns (days, error_response): `error_response` is a ready-to-return
    (jsonify(...), status) pair on invalid input, or None when `days` is
    absent (both None) or a valid positive int within DAYS_PARAM_MAX.
    """
    raw = request.args.get("days")
    if raw is None:
        return None, None

    try:
        days = int(raw)
    except (TypeError, ValueError):
        return None, (jsonify({"error": "Parámetro 'days' inválido"}), 400)

    if not (1 <= days <= DAYS_PARAM_MAX):
        return None, (jsonify({"error": "Parámetro 'days' fuera de rango"}), 400)

    return days, None


@app.route("/api/equity_curves")
def api_equity_curves():
    parsed_data = get_parsed_data()
    if not parsed_data:
        return jsonify({"error": "No hay datos cargados"}), 400

    days_param, days_error = _validated_days_param()
    if days_error:
        return days_error

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

    days_param, days_error = _validated_days_param()
    if days_error:
        return days_error

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

    days_param, days_error = _validated_days_param()
    if days_error:
        return days_error

    config = load_config()
    if not _ea_is_active(ea_name, config):
        return jsonify({"error": "EA inactiva"}), 404

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
    if not _ea_is_active(ea_name, config):
        return jsonify({"error": "EA inactiva"}), 404

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

    days_param, days_error = _validated_days_param()
    if days_error:
        return days_error

    config = load_incubation_config()
    ea_trades = [
        t
        for t in parsed_data.get("closed_trades", [])
        if trade_matches_ea(t, ea_name, config)
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
        if trade_matches_ea(t, ea_name, config)
    ]

    from metrics import calculate_ea_metrics

    m = calculate_ea_metrics(ea_name, ea_trades, config)
    payload = build_distribution_payload(m)
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
            if trade_matches_ea(t, ea_name, config)
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
    if not _ea_is_active(ea_name, config):
        return jsonify({"error": "EA inactiva"}), 404

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
    # Real startup: persist a durable secret key so sessions survive restarts,
    # and create the runtime directories up front. Neither happens on import.
    app.secret_key = _resolve_secret_key(persist_if_created=True)
    _ensure_dir(UPLOAD_FOLDER)
    _ensure_dir(CACHE_DIR)
    print("=" * 50)
    print(f"  EA Analyzer - iniciando servidor...")
    print(f"  Abriendo http://localhost:{PORT}")
    print(f"  Presiona Ctrl+C para detener")
    print("=" * 50)
    threading.Thread(target=open_browser, daemon=True).start()
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)
