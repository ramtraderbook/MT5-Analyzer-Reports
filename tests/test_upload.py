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
