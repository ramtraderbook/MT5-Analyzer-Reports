from io import BytesIO


def test_upload_same_file_reopens_dashboard_without_appending(monkeypatch, tmp_path):
    import ea_analyzer

    existing_data = {
        "closed_trades": [
            {
                "position_id": 101,
                "comment": "MyEA",
                "net_pnl": 10.0,
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

    import parser

    monkeypatch.setattr(ea_analyzer, "UPLOAD_FOLDER", str(uploads_dir))
    monkeypatch.setattr(ea_analyzer, "cleanup_old_caches", lambda: None)
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

    response = client.post(
        "/upload",
        data={"file": (BytesIO(b"dummy"), "same.xlsx")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    assert saved_configs == []


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
    monkeypatch.setattr(ea_analyzer, "cleanup_old_caches", lambda: None)
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
    response = client.post(
        "/upload",
        data={
            "analysis_mode": "incubation",
            "file": (BytesIO(b"dummy"), "incubation.xlsx"),
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
    monkeypatch.setattr(ea_analyzer, "cleanup_old_caches", lambda: None)
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
    response = client.post(
        "/upload",
        data={
            "analysis_mode": "live",
            "file": (BytesIO(b"dummy"), "live.xlsx"),
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
        sess["filename"] = "incubation.xlsx"

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

    response = client.get("/incubation/reference_data")
    assert response.status_code == 200
    assert b"EA_A" in response.data
    assert b"datos cargados" in response.data
    assert b"EA_B" not in response.data

    response = client.get("/incubation/reference_data/edit/EA_A")
    assert response.status_code == 200
    assert b"EA_A" in response.data
    assert b"BACKTEST" in response.data

    form_data = {}
    for section in ea_analyzer.INCUBATION_REFERENCE_SECTIONS:
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
