from datetime import datetime
from io import BytesIO

import pytest

# Fixed CSRF token used by every POST in this file -- the session and the
# posted form field must agree, or ea_analyzer's before_request CSRF check
# rejects the request with 400 before the route body ever runs.
CSRF_TOKEN = "test-csrf-token"


def test_upload_same_file_reopens_dashboard_without_appending(monkeypatch, tmp_path):
    """
    F1/F2 regression: `existing_data` simulates load_cache() — cache trades
    are always ISO strings (_serialize_parsed_data() converts them on the
    way to disk). `parsed_same_file` simulates the REAL parse_mt5_report(),
    which ALWAYS returns datetime objects for open_time/close_time
    (parser.py's _parse_date never returns a string) — mocking it with a
    string here would mask the exact type mismatch this fast path must
    tolerate. Both represent the same instant.
    """
    import ea_analyzer

    existing_data = {
        "closed_trades": [
            {
                "position_id": 101,
                "comment": "MyEA",
                "net_pnl": 10.0,
                "open_time": "2026-01-01T09:00:00",
                "close_time": "2026-01-01T12:00:00",
            }
        ]
    }
    parsed_same_file = {
        "closed_trades": [
            {
                "position_id": 101,
                "comment": "MyEA",
                "net_pnl": 10.0,
                "open_time": datetime(2026, 1, 1, 9, 0, 0),
                "close_time": datetime(2026, 1, 1, 12, 0, 0),
            }
        ],
        "ea_names": ["MyEA"],
        "total_closed": 1,
        "unknown_trades": 0,
        "account": {},
        "open_positions": [],
    }

    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()

    import parser

    monkeypatch.setattr(ea_analyzer, "UPLOAD_FOLDER", str(uploads_dir))
    monkeypatch.setattr(ea_analyzer, "cleanup_old_caches", lambda *a, **k: None)
    monkeypatch.setattr(ea_analyzer, "load_cache", lambda cache_key: existing_data)
    monkeypatch.setattr(parser, "parse_mt5_report", lambda filepath: parsed_same_file)

    saved_configs = []

    monkeypatch.setattr(
        ea_analyzer,
        "save_config",
        lambda config: saved_configs.append(config),
    )
    monkeypatch.setattr(
        ea_analyzer,
        "invalidate_metrics_cache",
        lambda: None,
    )

    client = ea_analyzer.app.test_client()
    with client.session_transaction() as sess:
        sess["cache_key"] = "existing-cache"
        sess["csrf_token"] = CSRF_TOKEN

    response = client.post(
        "/upload",
        data={"file": (BytesIO(b"dummy"), "same.xlsx"), "csrf_token": CSRF_TOKEN},
        content_type="multipart/form-data",
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    assert saved_configs == []


def test_merge_changed_content_detects_corrections_only_reupload():
    """
    R6: added_count alone can't detect a corrections-only re-upload (same
    position_ids, updated fields) — _merge_changed_content must return
    True in that case so the upload route doesn't silently discard the
    "new data wins" merge. Named shape from the ledger: pos 555 goes from
    commission=-2.00/net_pnl=17.80 to commission=-1.00/net_pnl=18.80.
    """
    import ea_analyzer

    existing_trades = [
        {"position_id": 555, "commission": -2.00, "net_pnl": 17.80, "comment": "MyEA"},
    ]
    # merge_trades() would replace pos 555 with the corrected trade (new data wins)
    merged_corrected = [
        {"position_id": 555, "commission": -1.00, "net_pnl": 18.80, "comment": "MyEA"},
    ]

    assert ea_analyzer._merge_changed_content(existing_trades, merged_corrected) is True


def test_merge_changed_content_false_for_identical_reupload():
    """
    R6: the legitimate fast path — re-uploading the exact same file with
    no new trades and no field changes — must still short-circuit.

    F1/F2 regression: `existing_trades` simulates the cache (ISO strings
    for open_time/close_time — load_cache()'s shape), while
    `merged_identical` simulates what merge_trades() actually hands back
    for an unchanged position_id (new-wins: the freshly-parsed trade,
    which always carries datetime objects for those same fields). Without
    the F1 fix, this comparison reports a change on type alone even
    though the values represent the same instant.
    """
    import ea_analyzer

    existing_trades = [
        {
            "position_id": 555,
            "commission": -2.00,
            "net_pnl": 17.80,
            "comment": "MyEA",
            "open_time": "2026-01-10T09:00:00",
            "close_time": "2026-01-10T12:00:00",
        },
    ]
    merged_identical = [
        {
            "position_id": 555,
            "commission": -2.00,
            "net_pnl": 17.80,
            "comment": "MyEA",
            "open_time": datetime(2026, 1, 10, 9, 0, 0),
            "close_time": datetime(2026, 1, 10, 12, 0, 0),
        },
    ]

    assert ea_analyzer._merge_changed_content(existing_trades, merged_identical) is False


def test_merge_changed_content_datetime_vs_iso_string_equivalence():
    """
    F1 direct regression: cached trades (load_cache()'s shape) hold ISO
    strings for open_time/close_time — _serialize_parsed_data() converts
    them on the way to disk. merge_trades() is new-wins, so the merged
    trade for an unchanged position_id carries the freshly-parsed trade,
    which always has datetime objects for those same fields (parser.py's
    _parse_date never returns a string). _merge_changed_content() must
    treat those as equal — comparing raw dicts would report a change on
    type alone even though the underlying instant is identical, defeating
    the "same file re-uploaded, nothing changed" fast path.
    """
    import ea_analyzer
    from parser import merge_trades

    cached = [
        {
            "position_id": 555,
            "net_pnl": 17.8,
            "close_time": "2026-01-10T12:00:00",
            "open_time": "2026-01-10T09:00:00",
        }
    ]
    fresh = [
        {
            "position_id": 555,
            "net_pnl": 17.8,
            "close_time": datetime(2026, 1, 10, 12, 0),
            "open_time": datetime(2026, 1, 10, 9, 0),
        }
    ]

    merged = merge_trades(cached, fresh)

    assert ea_analyzer._merge_changed_content(cached, merged) is False


def test_upload_live_mode_saves_corrections_only_reupload(monkeypatch, tmp_path):
    """
    R6 route-level regression: a correction-only re-upload (same
    position_id, updated commission/net_pnl) has added_count == 0, but
    must NOT hit the early "nothing changed" return — it must proceed to
    save the corrected data, or the cache keeps the stale value forever.
    """
    import ea_analyzer
    import parser

    existing_data = {
        "closed_trades": [
            {
                "position_id": 555,
                "comment": "MyEA",
                "commission": -2.00,
                "net_pnl": 17.80,
                "close_time": "2026-01-01T12:00:00",
            }
        ]
    }
    parsed_correction = {
        "closed_trades": [
            {
                "position_id": 555,
                "comment": "MyEA",
                "commission": -1.00,
                "net_pnl": 18.80,
                "close_time": "2026-01-01T12:00:00",
            }
        ],
        "ea_names": ["MyEA"],
        "total_closed": 1,
        "unknown_trades": 0,
        "account": {},
        "open_positions": [],
    }

    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()

    monkeypatch.setattr(ea_analyzer, "UPLOAD_FOLDER", str(uploads_dir))
    monkeypatch.setattr(ea_analyzer, "cleanup_old_caches", lambda *a, **k: None)
    monkeypatch.setattr(ea_analyzer, "load_cache", lambda cache_key: existing_data)
    monkeypatch.setattr(parser, "parse_mt5_report", lambda filepath: parsed_correction)

    saved_caches = []
    monkeypatch.setattr(
        ea_analyzer,
        "save_cache",
        lambda data: saved_caches.append(data) or "new-cache-key",
    )
    monkeypatch.setattr(ea_analyzer, "save_config", lambda config: None)
    monkeypatch.setattr(ea_analyzer, "invalidate_metrics_cache", lambda: None)
    monkeypatch.setattr(ea_analyzer, "_delete_cache_file", lambda *a, **k: None)

    client = ea_analyzer.app.test_client()
    with client.session_transaction() as sess:
        sess["cache_key"] = "existing-cache"
        sess["csrf_token"] = CSRF_TOKEN

    response = client.post(
        "/upload",
        data={"file": (BytesIO(b"dummy"), "correction.xlsx"), "csrf_token": CSRF_TOKEN},
        content_type="multipart/form-data",
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/mapping")
    assert len(saved_caches) == 1
    saved_trade = saved_caches[0]["closed_trades"][0]
    assert saved_trade["commission"] == pytest.approx(-1.00)
    assert saved_trade["net_pnl"] == pytest.approx(18.80)


def test_upload_incubation_mode_routes_to_mapping(monkeypatch, tmp_path):
    import ea_analyzer
    import parser

    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()

    parsed_data = {
        "closed_trades": [
            {
                "position_id": 201,
                "comment": "IncubationEA",
                "net_pnl": 25.0,
                "close_time": "2026-01-01T12:00:00",
            }
        ]
    }

    monkeypatch.setattr(ea_analyzer, "UPLOAD_FOLDER", str(uploads_dir))
    monkeypatch.setattr(ea_analyzer, "cleanup_old_caches", lambda *a, **k: None)
    monkeypatch.setattr(parser, "parse_mt5_report", lambda filepath: parsed_data)
    monkeypatch.setattr(
        ea_analyzer,
        "get_parsed_data",
        lambda: (_ for _ in ()).throw(AssertionError("live flow should not run")),
    )
    monkeypatch.setattr(
        ea_analyzer,
        "save_cache",
        lambda data: (_ for _ in ()).throw(AssertionError("save_cache should not run")),
    )
    monkeypatch.setattr(
        ea_analyzer,
        "save_config",
        lambda config: (_ for _ in ()).throw(AssertionError("save_config should not run")),
    )
    monkeypatch.setattr(
        ea_analyzer,
        "invalidate_metrics_cache",
        lambda: (_ for _ in ()).throw(AssertionError("invalidate should not run")),
    )

    client = ea_analyzer.app.test_client()
    with client.session_transaction() as sess:
        sess["csrf_token"] = CSRF_TOKEN

    response = client.post(
        "/upload",
        data={
            "analysis_mode": "incubation",
            "file": (BytesIO(b"dummy"), "incubation.xlsx"),
            "csrf_token": CSRF_TOKEN,
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Mapping de estrategias" in response.data
    with client.session_transaction() as sess:
        assert sess["analysis_mode"] == "incubation"


def test_upload_live_mode_keeps_existing_flow(monkeypatch, tmp_path):
    import ea_analyzer
    import parser

    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()

    parsed_data = {
        "closed_trades": [
            {
                "position_id": 301,
                "comment": "LiveEA",
                "net_pnl": 12.5,
                "close_time": "2026-01-01T12:00:00",
            }
        ]
    }

    saved_configs = []

    monkeypatch.setattr(ea_analyzer, "UPLOAD_FOLDER", str(uploads_dir))
    monkeypatch.setattr(ea_analyzer, "cleanup_old_caches", lambda *a, **k: None)
    monkeypatch.setattr(parser, "parse_mt5_report", lambda filepath: parsed_data)
    monkeypatch.setattr(ea_analyzer, "get_parsed_data", lambda: None)
    monkeypatch.setattr(ea_analyzer, "save_cache", lambda data: "live-cache-key")
    monkeypatch.setattr(
        ea_analyzer,
        "save_config",
        lambda config: saved_configs.append(config),
    )
    monkeypatch.setattr(ea_analyzer, "invalidate_metrics_cache", lambda: None)

    client = ea_analyzer.app.test_client()
    with client.session_transaction() as sess:
        sess["csrf_token"] = CSRF_TOKEN

    response = client.post(
        "/upload",
        data={
            "analysis_mode": "live",
            "file": (BytesIO(b"dummy"), "live.xlsx"),
            "csrf_token": CSRF_TOKEN,
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/mapping")
    with client.session_transaction() as sess:
        assert sess["analysis_mode"] == "live"
        assert sess["cache_key"] == "live-cache-key"
    assert saved_configs


def test_incubation_mapping_requires_incubation_mode(monkeypatch):
    import ea_analyzer

    client = ea_analyzer.app.test_client()
    with client.session_transaction() as sess:
        sess["analysis_mode"] = "live"

    response = client.get("/incubation/mapping", follow_redirects=True)

    assert response.status_code == 200
    assert b"Seleccione modo Incubation primero" in response.data


def test_incubation_mapping_and_save_flow(monkeypatch):
    import ea_analyzer

    parsed_data = {
        "closed_trades": [
            {
                "position_id": 401,
                "comment": "IncubationEA",
                "symbol": "USDJPY",
                "net_pnl": 30.0,
                "close_time": "2026-01-01T12:00:00",
            }
        ],
        "ea_names": ["IncubationEA"],
        "unknown_trades": 0,
        "account": {"number": "123"},
    }
    captured = {}

    monkeypatch.setattr(ea_analyzer, "load_incubation_cache", lambda key: parsed_data)
    monkeypatch.setattr(
        ea_analyzer,
        "load_incubation_config",
        lambda: {"mappings": {"IncubationEA": {"alias": "", "capital": 5000, "active": True}}},
    )
    monkeypatch.setattr(
        ea_analyzer,
        "save_incubation_config",
        lambda data: captured.setdefault("config", data),
    )

    client = ea_analyzer.app.test_client()
    with client.session_transaction() as sess:
        sess["analysis_mode"] = "incubation"
        sess["incubation_cache_key"] = "cache-1"
        sess["incubation_filename"] = "incubation.xlsx"
        sess["csrf_token"] = CSRF_TOKEN

    response = client.get("/incubation/mapping")
    assert response.status_code == 200
    assert b"Mapping de estrategias" in response.data
    assert b"IncubationEA" in response.data

    response = client.post(
        "/incubation/mapping/save",
        data={
            "magic_IncubationEA": "555",
            "alias_IncubationEA": "IncEA",
            "capital_IncubationEA": "7500",
            "instrument_IncubationEA": "USDJPY",
            "include_IncubationEA": "on",
            "csrf_token": CSRF_TOKEN,
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/incubation/reference_data")
    assert captured["config"]["mappings"]["IncubationEA"]["magic"] == 555
    assert captured["config"]["mappings"]["IncubationEA"]["alias"] == "IncEA"
    assert captured["config"]["mappings"]["IncubationEA"]["capital"] == 7500.0
    assert captured["config"]["mappings"]["IncubationEA"]["instrument"] == "USDJPY"
    assert captured["config"]["mappings"]["IncubationEA"]["active"] is True


def test_incubation_reference_list_and_edit_save(monkeypatch):
    import ea_analyzer
    import incubation_domain

    captured = {}

    monkeypatch.setattr(
        ea_analyzer,
        "load_incubation_config",
        lambda: {
            "mappings": {
                "EA_A": {"alias": "Alpha", "instrument": "USDJPY", "active": True},
                "EA_B": {"alias": "Beta", "instrument": "XAUUSD", "active": False},
            }
        },
    )
    monkeypatch.setattr(
        ea_analyzer,
        "load_incubation_store",
        lambda: {
            "EA_A": {
                "backtest": {"net_profit": 1},
                "monte_carlo": {"confidence_95": {"max_dd_pct": 1}},
            }
        },
    )
    monkeypatch.setattr(
        ea_analyzer,
        "save_incubation_store",
        lambda data: captured.setdefault("store", data),
    )

    client = ea_analyzer.app.test_client()
    with client.session_transaction() as sess:
        sess["analysis_mode"] = "incubation"
        sess["csrf_token"] = CSRF_TOKEN

    response = client.get("/incubation/reference_data")
    assert response.status_code == 200
    assert b"EA_A" in response.data
    assert b"datos cargados" in response.data
    assert b"EA_B" not in response.data

    response = client.get("/incubation/reference_data/edit/EA_A")
    assert response.status_code == 200
    assert b"EA_A" in response.data
    assert b"BACKTEST" in response.data

    form_data = {"csrf_token": CSRF_TOKEN}
    for section in incubation_domain.INCUBATION_REFERENCE_SECTIONS:
        for field in section["fields"]:
            key = f"{section['key']}_{field['key']}"
            if field["key"] == "bt_period":
                form_data[key] = "2017.10.02 - 2026.01.28"
            elif field["key"] == "timeframe":
                form_data[key] = "H1"
            elif field["key"] == "method":
                form_data[key] = "Randomize trades order"
            elif field["key"] == "simulations":
                form_data[key] = "1000"
            else:
                form_data[key] = "1.5"

    response = client.post(
        "/incubation/reference_data/save/EA_A",
        data=form_data,
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/incubation/reference_data")
    stored = captured["store"]["EA_A"]
    assert stored["ea_name"] == "EA_A"
    assert stored["status"] == "incubating"
    assert stored["backtest"]["net_profit"] == 1.5
    assert stored["monte_carlo"]["confidence_95"]["max_dd_pct"] == 1.5


def test_validator_edit_uses_trade_matches_ea_for_mapping_key_comment_mismatch(monkeypatch):
    """
    Regression for the validator EDIT page: ea_name here is resolved from the
    config MAPPING KEY ("USDJPY_1104"), while trade comments in the parsed
    report use MT5's own free-text format ("USDJPY 1104"). The validator
    TABLE (validator()) already uses trade_matches_ea() for this; the EDIT
    route must use it too, or it will show "no live data" for an EA that the
    table scores fine.
    """
    from datetime import datetime

    import ea_analyzer

    parsed_data = {
        "closed_trades": [
            {
                "position_id": 1,
                "symbol": "USDJPY",
                "direction": "buy",
                "volume": 0.1,
                "open_time": datetime(2026, 1, 1, 10, 0, 0),
                "close_time": datetime(2026, 1, 1, 12, 0, 0),
                "open_price": 150.0,
                "close_price": 150.5,
                "sl": None,
                "tp": None,
                "commission": -1.0,
                "swap": 0.0,
                "profit": 11.0,
                "net_pnl": 10.0,
                "duration_hours": 2.0,
                "comment": "USDJPY 1104",
            }
        ],
        "ea_names": ["USDJPY 1104"],
        "account": {},
    }

    monkeypatch.setattr(ea_analyzer, "get_parsed_data", lambda: parsed_data)
    monkeypatch.setattr(
        ea_analyzer,
        "load_config",
        lambda: {
            "mappings": {
                "USDJPY_1104": {"magic": "555", "alias": "", "active": True}
            }
        },
    )
    monkeypatch.setattr(ea_analyzer, "load_validator_store", lambda: {})

    client = ea_analyzer.app.test_client()
    response = client.get("/validator/edit/555")

    assert response.status_code == 200
    assert b"val-preview" in response.data


def test_incubation_dashboard_smoke_with_matched_trades_returns_200(monkeypatch):
    """
    Route-level smoke test for FIX 1 (incubation_domain.py:589): any EA with
    >=1 matched trade used to raise UnboundLocalError on
    `reference_ready = reference_ready(entry)` inside evaluate_ea, turning
    this route into an unhandled 500. Reference data is intentionally left
    empty -- the crash happened before reference readiness was even checked.
    """
    from datetime import datetime

    import ea_analyzer

    parsed_data = {
        "closed_trades": [
            {
                "position_id": 1,
                "symbol": "EURUSD",
                "direction": "buy",
                "volume": 0.1,
                "open_time": datetime(2026, 1, 1, 10, 0, 0),
                "close_time": datetime(2026, 1, 1, 12, 0, 0),
                "open_price": 1.1000,
                "close_price": 1.1010,
                "sl": None,
                "tp": None,
                "commission": -1.0,
                "swap": 0.0,
                "profit": 11.0,
                "net_pnl": 10.0,
                "duration_hours": 2.0,
                "comment": "IncEA",
            }
        ],
        "ea_names": ["IncEA"],
        "account": {},
    }

    monkeypatch.setattr(ea_analyzer, "get_incubation_parsed_data", lambda: parsed_data)
    monkeypatch.setattr(
        ea_analyzer,
        "load_incubation_config",
        lambda: {"mappings": {"IncEA": {"alias": "", "capital": 5000, "active": True}}},
    )
    monkeypatch.setattr(ea_analyzer, "load_incubation_store", lambda: {})
    monkeypatch.setattr(ea_analyzer, "save_incubation_store", lambda data: None)

    client = ea_analyzer.app.test_client()
    with client.session_transaction() as sess:
        sess["analysis_mode"] = "incubation"

    response = client.get("/incubation/dashboard")

    assert response.status_code == 200
    assert b"IncEA" in response.data


def test_incubation_strategy_smoke_with_matched_trades_returns_200(monkeypatch):
    """
    Same regression as above, for the /incubation/strategy/<ea_name> route.

    Uses one winning and one losing trade so profit_factor stays finite,
    keeping this test focused on the reference_ready regression only. The
    infinite-profit_factor ("∞" string) case -- where metrics.py's fmt_pf()
    used to crash incubation_domain._incubation_format_metric() (no
    reference data) or _incubation_metric_band_state() (reference data
    present) -- is covered separately by
    test_incubation_strategy_smoke_zero_loss_ea_returns_200 below.
    """
    from datetime import datetime

    import ea_analyzer

    parsed_data = {
        "closed_trades": [
            {
                "position_id": 1,
                "symbol": "EURUSD",
                "direction": "buy",
                "volume": 0.1,
                "open_time": datetime(2026, 1, 1, 10, 0, 0),
                "close_time": datetime(2026, 1, 1, 12, 0, 0),
                "open_price": 1.1000,
                "close_price": 1.1010,
                "sl": None,
                "tp": None,
                "commission": -1.0,
                "swap": 0.0,
                "profit": 11.0,
                "net_pnl": 10.0,
                "duration_hours": 2.0,
                "comment": "IncEA",
            },
            {
                "position_id": 2,
                "symbol": "EURUSD",
                "direction": "sell",
                "volume": 0.1,
                "open_time": datetime(2026, 1, 2, 10, 0, 0),
                "close_time": datetime(2026, 1, 2, 12, 0, 0),
                "open_price": 1.1000,
                "close_price": 1.0990,
                "sl": None,
                "tp": None,
                "commission": -1.0,
                "swap": 0.0,
                "profit": -4.0,
                "net_pnl": -5.0,
                "duration_hours": 2.0,
                "comment": "IncEA",
            },
        ],
        "ea_names": ["IncEA"],
        "account": {},
    }

    monkeypatch.setattr(ea_analyzer, "get_incubation_parsed_data", lambda: parsed_data)
    monkeypatch.setattr(
        ea_analyzer,
        "load_incubation_config",
        lambda: {"mappings": {"IncEA": {"alias": "", "capital": 5000, "active": True}}},
    )
    monkeypatch.setattr(ea_analyzer, "load_incubation_store", lambda: {})
    monkeypatch.setattr(ea_analyzer, "save_incubation_store", lambda data: None)

    client = ea_analyzer.app.test_client()
    with client.session_transaction() as sess:
        sess["analysis_mode"] = "incubation"

    response = client.get("/incubation/strategy/IncEA")

    assert response.status_code == 200
    assert b"IncEA" in response.data


def test_incubation_strategy_smoke_zero_loss_ea_returns_200(monkeypatch):
    """
    Regression test for the infinite profit_factor crash: metrics.py's
    fmt_pf() pre-formats profit_factor/payout_ratio to the string "∞" when
    an EA has winning trades and zero losses (division-by-zero guard).
    Before the fix, this route 500'd for any such EA because
    incubation_domain._incubation_format_metric() tried `f"{value:.2f}"` on
    the string "∞" (its `value == float("inf")` guard never matches a str),
    raising ValueError. Uses a single winning trade with no losses.
    """
    from datetime import datetime

    import ea_analyzer

    parsed_data = {
        "closed_trades": [
            {
                "position_id": 1,
                "symbol": "EURUSD",
                "direction": "buy",
                "volume": 0.1,
                "open_time": datetime(2026, 1, 1, 10, 0, 0),
                "close_time": datetime(2026, 1, 1, 12, 0, 0),
                "open_price": 1.1000,
                "close_price": 1.1010,
                "sl": None,
                "tp": None,
                "commission": -1.0,
                "swap": 0.0,
                "profit": 11.0,
                "net_pnl": 10.0,
                "duration_hours": 2.0,
                "comment": "IncEA",
            }
        ],
        "ea_names": ["IncEA"],
        "account": {},
    }

    monkeypatch.setattr(ea_analyzer, "get_incubation_parsed_data", lambda: parsed_data)
    monkeypatch.setattr(
        ea_analyzer,
        "load_incubation_config",
        lambda: {"mappings": {"IncEA": {"alias": "", "capital": 5000, "active": True}}},
    )
    monkeypatch.setattr(ea_analyzer, "load_incubation_store", lambda: {})
    monkeypatch.setattr(ea_analyzer, "save_incubation_store", lambda data: None)

    client = ea_analyzer.app.test_client()
    with client.session_transaction() as sess:
        sess["analysis_mode"] = "incubation"

    response = client.get("/incubation/strategy/IncEA")

    assert response.status_code == 200
    assert b"IncEA" in response.data
    assert "∞".encode("utf-8") in response.data
