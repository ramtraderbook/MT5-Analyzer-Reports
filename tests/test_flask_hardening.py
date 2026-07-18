"""
test_flask_hardening.py - Focused regression tests for the JD-4 confirmed
findings against ea_analyzer.py's Flask surface (C1, C3, H2, H4, W1, O1, O2).
C2 is covered separately in tests/test_local_files.py (local_json.py).
"""

import math
import os
import time
from io import BytesIO

CSRF_TOKEN = "test-csrf-token"


def test_upload_after_2h_does_not_wipe_session_own_cache(monkeypatch, tmp_path):
    """
    C1 regression: cleanup_old_caches() used to run as the very first
    statement of upload(), deleting any cache file whose mtime was older
    than 7200s -- including the CURRENT session's own cache file. Reads
    never refresh mtime, so on any re-upload made more than 2h after the
    previous one, the session's own cache file was deleted before
    load_cache() ever read it, get_parsed_data() returned None, the merge
    branch was skipped, and the new upload silently REPLACED the entire
    accumulated history instead of merging into it.
    """
    import ea_analyzer
    import parser

    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    cache_dir = tmp_path / "runtime_cache"
    cache_dir.mkdir()

    monkeypatch.setattr(ea_analyzer, "UPLOAD_FOLDER", str(uploads_dir))
    monkeypatch.setattr(ea_analyzer, "CACHE_DIR", str(cache_dir))
    # This test lets the real cleanup_old_caches() run, and it also sweeps the
    # legacy APP_DIR paths -- keep it away from a live install's own files.
    monkeypatch.setattr(ea_analyzer, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(ea_analyzer, "save_config", lambda config: None)

    existing_data = {
        "closed_trades": [
            {
                "position_id": 1,
                "comment": "MyEA",
                "net_pnl": 10.0,
                "close_time": "2026-01-01T12:00:00",
            }
        ],
        "ea_names": ["MyEA"],
        "total_closed": 1,
        "unknown_trades": 0,
        "account": {},
        "open_positions": [],
    }
    cache_key = ea_analyzer.save_cache(existing_data)

    # Simulate a re-upload made > 2h after the previous one.
    cache_path = ea_analyzer._cache_file_path(cache_key, ea_analyzer.LIVE_CACHE_PREFIX)
    old_time = time.time() - 7300
    os.utime(cache_path, (old_time, old_time))

    new_data = {
        "closed_trades": [
            {
                "position_id": 2,
                "comment": "MyEA",
                "net_pnl": 5.0,
                "close_time": "2026-01-02T12:00:00",
            }
        ],
        "ea_names": ["MyEA"],
        "total_closed": 1,
        "unknown_trades": 0,
        "account": {},
        "open_positions": [],
    }
    monkeypatch.setattr(parser, "parse_mt5_report", lambda filepath: new_data)

    client = ea_analyzer.app.test_client()
    with client.session_transaction() as sess:
        sess["cache_key"] = cache_key
        sess["csrf_token"] = CSRF_TOKEN

    response = client.post(
        "/upload",
        data={"file": (BytesIO(b"dummy"), "reupload.xlsx"), "csrf_token": CSRF_TOKEN},
        content_type="multipart/form-data",
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/mapping")

    with client.session_transaction() as sess:
        new_cache_key = sess["cache_key"]

    merged = ea_analyzer.load_cache(new_cache_key)
    position_ids = {t["position_id"] for t in merged["closed_trades"]}
    assert position_ids == {1, 2}  # merged, not replaced


def test_metrics_cache_key_includes_config_hash_not_just_session_cache_key():
    """
    C3 regression (WSGI-only): _get_metrics_cached used to key purely on
    session["cache_key"]. config.json is a single GLOBAL file, so under
    multi-worker/multi-session WSGI, two requests sharing the same session
    cache_key but hitting DIFFERENT in-memory config states (e.g. worker A
    just ran mapping_save and deactivated an EA, worker B hasn't reloaded)
    used to collide on the same cache entry and serve a stale, materially
    wrong verdict as current. Hashing the config into the key makes a
    changed config land on a structurally different entry.
    """
    import ea_analyzer

    ea_analyzer._metrics_cache.clear()

    trades = [
        {
            "position_id": 1,
            "comment": "MyEA",
            "symbol": "EURUSD",
            "direction": "buy",
            "net_pnl": 10.0,
            "duration_hours": 1.0,
            "open_time": "2026-01-01T09:00:00",
            "close_time": "2026-01-01T10:00:00",
            "volume": 0.1,
        }
    ]
    parsed_data = {
        "closed_trades": trades,
        "ea_names": ["MyEA"],
        "open_positions": [],
        "total_closed": 1,
        "unknown_trades": 0,
    }

    config_active = {"mappings": {"MyEA": {"active": True, "capital": 5000.0}}}
    config_inactive = {"mappings": {"MyEA": {"active": False, "capital": 5000.0}}}

    with ea_analyzer.app.test_request_context():
        from flask import session as flask_session

        flask_session["cache_key"] = "shared-key"
        result_active = ea_analyzer._get_metrics_cached(parsed_data, config_active)

    with ea_analyzer.app.test_request_context():
        from flask import session as flask_session

        flask_session["cache_key"] = "shared-key"
        result_inactive = ea_analyzer._get_metrics_cached(parsed_data, config_inactive)

    assert "MyEA" in result_active["by_ea"]
    assert "MyEA" not in result_inactive["by_ea"]


def test_api_ea_equity_returns_404_for_inactive_ea(monkeypatch):
    """
    H2 regression: build_sidebar_eas() and metrics.calculate_all_metrics()
    both exclude EAs whose mapping has active: False, but the single-EA API
    routes filtered only by comment equality and never checked the active
    flag -- a deactivated EA stayed fully computed and servable on direct
    request (bookmark, history, copied link), contradicting the aggregate
    contract.
    """
    import ea_analyzer

    parsed_data = {
        "closed_trades": [
            {
                "position_id": 1,
                "comment": "MyEA",
                "symbol": "EURUSD",
                "direction": "buy",
                "net_pnl": 10.0,
                "duration_hours": 1.0,
                "open_time": "2026-01-01T09:00:00",
                "close_time": "2026-01-01T10:00:00",
                "volume": 0.1,
            }
        ],
        "ea_names": ["MyEA"],
        "open_positions": [],
        "total_closed": 1,
        "unknown_trades": 0,
    }

    monkeypatch.setattr(ea_analyzer, "get_parsed_data", lambda: parsed_data)
    monkeypatch.setattr(
        ea_analyzer,
        "load_config",
        lambda: {"mappings": {"MyEA": {"active": False}}},
    )

    client = ea_analyzer.app.test_client()
    response = client.get("/api/ea_equity/MyEA")

    assert response.status_code == 404


def test_incubation_upload_does_not_clobber_live_filename(monkeypatch, tmp_path):
    """
    H4 regression: session["filename"] used to be a single key shared by
    both modes -- uploading in incubation mode overwrote the filename the
    LIVE /mapping page displays, and a live reset conversely blanked the
    incubation filename even though its trade data was untouched. Live and
    incubation now use separate session keys.
    """
    import ea_analyzer
    import parser

    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()

    parsed_data = {
        "closed_trades": [
            {
                "position_id": 1,
                "comment": "IncEA",
                "net_pnl": 5.0,
                "close_time": "2026-01-01T12:00:00",
            }
        ],
        "ea_names": ["IncEA"],
        "total_closed": 1,
        "unknown_trades": 0,
        "account": {},
        "open_positions": [],
    }

    monkeypatch.setattr(ea_analyzer, "UPLOAD_FOLDER", str(uploads_dir))
    monkeypatch.setattr(ea_analyzer, "cleanup_old_caches", lambda *a, **k: None)
    monkeypatch.setattr(parser, "parse_mt5_report", lambda filepath: parsed_data)
    monkeypatch.setattr(ea_analyzer, "get_incubation_parsed_data", lambda: None)
    monkeypatch.setattr(ea_analyzer, "save_incubation_cache", lambda data: "inc-cache-key")
    monkeypatch.setattr(ea_analyzer, "save_incubation_config", lambda config: None)

    client = ea_analyzer.app.test_client()
    with client.session_transaction() as sess:
        sess["filename"] = "live_report.xlsx"  # pre-existing live upload
        sess["csrf_token"] = CSRF_TOKEN

    response = client.post(
        "/upload",
        data={
            "analysis_mode": "incubation",
            "file": (BytesIO(b"dummy"), "incubation_report.xlsx"),
            "csrf_token": CSRF_TOKEN,
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 302

    with client.session_transaction() as sess:
        assert sess["filename"] == "live_report.xlsx"
        assert sess["incubation_filename"] == "incubation_report.xlsx"


def test_mapping_save_keeps_existing_capital_on_invalid_input(monkeypatch):
    """
    W1 regression: a non-numeric capital used to silently REPLACE the EA's
    previously saved capital with the 5000.0 default instead of keeping the
    existing value -- magic already got the opposite (safer) treatment
    (except ValueError: pass, keeping the old value). One typo used to
    corrupt every capital-scaled metric (DD%, return%) with no feedback.
    """
    import ea_analyzer

    parsed_data = {
        "closed_trades": [],
        "ea_names": ["MyEA"],
        "account": {},
        "unknown_trades": 0,
    }
    captured = {}

    monkeypatch.setattr(ea_analyzer, "get_parsed_data", lambda: parsed_data)
    monkeypatch.setattr(
        ea_analyzer,
        "load_config",
        lambda: {"mappings": {"MyEA": {"capital": 12345.0, "active": True}}},
    )
    monkeypatch.setattr(
        ea_analyzer,
        "save_config",
        lambda config: captured.setdefault("config", config),
    )
    monkeypatch.setattr(ea_analyzer, "invalidate_metrics_cache", lambda: None)

    client = ea_analyzer.app.test_client()
    with client.session_transaction() as sess:
        sess["csrf_token"] = CSRF_TOKEN

    response = client.post(
        "/mapping/save",
        data={
            "capital_MyEA": "not-a-number",
            "magic_MyEA": "",
            "instrument_MyEA": "",
            "alias_MyEA": "",
            "include_MyEA": "on",
            "csrf_token": CSRF_TOKEN,
        },
    )

    assert response.status_code == 302
    assert captured["config"]["mappings"]["MyEA"]["capital"] == 12345.0


def test_mapping_save_rejects_non_finite_capital(monkeypatch):
    """
    O2 regression (orchestrator-proven): float("nan") and float("inf")
    parse successfully via Python's float(), so entry["capital"] =
    float(capital_val) accepted them silently. json.dumps({'c':
    float('nan')}) emits the bare token NaN, which is invalid JSON and
    breaks the front-end JSON.parse() on any chart response reading that
    config. math.isfinite() must reject non-finite capital as part of the
    same validation that already rejects invalid/non-positive input.
    """
    import ea_analyzer

    parsed_data = {
        "closed_trades": [],
        "ea_names": ["MyEA"],
        "account": {},
        "unknown_trades": 0,
    }
    captured = {}

    monkeypatch.setattr(ea_analyzer, "get_parsed_data", lambda: parsed_data)
    monkeypatch.setattr(
        ea_analyzer,
        "load_config",
        lambda: {"mappings": {"MyEA": {"capital": 5000.0, "active": True}}},
    )
    monkeypatch.setattr(
        ea_analyzer,
        "save_config",
        lambda config: captured.setdefault("config", config),
    )
    monkeypatch.setattr(ea_analyzer, "invalidate_metrics_cache", lambda: None)

    client = ea_analyzer.app.test_client()
    with client.session_transaction() as sess:
        sess["csrf_token"] = CSRF_TOKEN

    response = client.post(
        "/mapping/save",
        data={
            "capital_MyEA": "nan",
            "magic_MyEA": "",
            "instrument_MyEA": "",
            "alias_MyEA": "",
            "include_MyEA": "on",
            "csrf_token": CSRF_TOKEN,
        },
    )

    assert response.status_code == 302
    saved_capital = captured["config"]["mappings"]["MyEA"]["capital"]
    assert math.isfinite(saved_capital)
    assert saved_capital == 5000.0


def test_api_equity_curves_rejects_absurd_days_param(monkeypatch):
    """
    O1 regression (orchestrator-proven): request.args.get("days", type=int)
    accepted ANY int, including one so large that
    datetime.now() - timedelta(days=days_param) raises an unhandled
    OverflowError ("Python int too large to convert to C int"), turning a
    simple chart request into a 500. `days` must be validated against a
    sane bound at every endpoint that accepts it.
    """
    import ea_analyzer

    parsed_data = {
        "closed_trades": [
            {
                "position_id": 1,
                "comment": "MyEA",
                "symbol": "EURUSD",
                "direction": "buy",
                "net_pnl": 10.0,
                "duration_hours": 1.0,
                "open_time": "2026-01-01T09:00:00",
                "close_time": "2026-01-01T10:00:00",
                "volume": 0.1,
            }
        ],
        "ea_names": ["MyEA"],
        "open_positions": [],
        "total_closed": 1,
        "unknown_trades": 0,
    }

    monkeypatch.setattr(ea_analyzer, "get_parsed_data", lambda: parsed_data)
    monkeypatch.setattr(ea_analyzer, "load_config", lambda: {"mappings": {}})

    client = ea_analyzer.app.test_client()
    response = client.get("/api/equity_curves?days=10000000000")

    assert response.status_code == 400
    assert response.is_json
    assert "error" in response.get_json()


def test_csrf_rejects_non_ascii_token_with_400_not_500():
    """
    The submitted token is client-controlled, and hmac.compare_digest raises
    TypeError on non-ASCII str -- a junk token must be rejected, not crash.
    """
    import ea_analyzer

    ea_analyzer.app.config["TESTING"] = True
    client = ea_analyzer.app.test_client()
    with client.session_transaction() as sess:
        sess["csrf_token"] = CSRF_TOKEN

    assert client.post("/reset", data={"csrf_token": "ñoño"}).status_code == 400
    assert client.post("/reset", data={"csrf_token": "wrong"}).status_code == 400


# ── 1A: importing ea_analyzer must have no filesystem side effects ───────────


def test_resolve_secret_key_never_writes_unless_persisting(monkeypatch, tmp_path):
    """1A: import sets app.secret_key via _resolve_secret_key() with
    persist_if_created=False, so a fresh checkout that imports the module must
    never create a .secret_key file. Precedence env -> file -> ephemeral, and
    only startup (persist_if_created=True) writes the key."""
    import ea_analyzer

    keyfile = tmp_path / ".secret_key"
    monkeypatch.setattr(ea_analyzer, "SECRET_KEY_PATH", str(keyfile))
    monkeypatch.delenv("EA_ANALYZER_SECRET_KEY", raising=False)

    # No env, no file -> ephemeral key, and NOTHING written (the import case).
    k1 = ea_analyzer._resolve_secret_key(persist_if_created=False)
    assert len(k1) == 24
    assert not keyfile.exists()

    # Startup persists the durable key so sessions survive restarts.
    k2 = ea_analyzer._resolve_secret_key(persist_if_created=True)
    assert keyfile.exists()
    assert keyfile.read_bytes() == k2

    # An existing file is read back (persistence across "restarts").
    assert ea_analyzer._resolve_secret_key() == k2

    # The env var wins and touches no file (the WSGI-friendly source).
    keyfile.unlink()
    monkeypatch.setenv("EA_ANALYZER_SECRET_KEY", "from-env")
    assert ea_analyzer._resolve_secret_key(persist_if_created=True) == b"from-env"
    assert not keyfile.exists()


def test_atomic_write_json_creates_its_directory_on_demand(tmp_path):
    """1A: directory creation moved out of import into the write sites, so a
    cache write into a not-yet-existing runtime dir must create it lazily
    instead of relying on an import-time os.makedirs."""
    import ea_analyzer

    target = tmp_path / "runtime_cache" / "cache_x.json"
    assert not target.parent.exists()
    ea_analyzer._atomic_write_json(str(target), {"ok": 1})
    assert target.exists()
    assert '"ok": 1' in target.read_text(encoding="utf-8")
