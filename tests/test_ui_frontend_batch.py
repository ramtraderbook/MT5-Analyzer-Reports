"""
test_ui_frontend_batch.py - Regression tests for the UI/view-model batch.

Covers:
- validator_edit's restored "+ Agregar EA" path, which must VALIDATE the
  submitted magic (present, numeric, known, non-clobbering) before writing the
  validator store -- an invalid magic re-renders the form and writes nothing,
  and a valid known magic keys the store by the normalized integer magic
  (never the "nuevo" sentinel).
- build_sidebar_eas now emitting a color per EA for the sidebar dots.

The engine is untouched; these pin UI/route behavior only.
"""

import ea_analyzer

CSRF_TOKEN = "test-csrf-token"


def _setup(monkeypatch, store):
    """Wire the validator_edit route onto in-memory parsed_data/config/store."""
    parsed_data = {"ea_names": ["MyEA"], "closed_trades": []}
    config = {
        "mappings": {
            "MyEA": {"magic": "9001", "alias": "MyEA", "active": True},
        }
    }
    monkeypatch.setattr(ea_analyzer, "get_parsed_data", lambda: parsed_data)
    monkeypatch.setattr(ea_analyzer, "load_config", lambda: config)
    monkeypatch.setattr(ea_analyzer, "load_validator_store", lambda: store)
    monkeypatch.setattr(ea_analyzer, "save_validator_store", lambda s: None)
    return parsed_data, config


def _client_with_session():
    client = ea_analyzer.app.test_client()
    with client.session_transaction() as sess:
        sess["cache_key"] = "test-cache"
        sess["csrf_token"] = CSRF_TOKEN
    return client


def _post_add(client, magic_value):
    return client.post(
        "/validator/edit/nuevo",
        data={"magic": magic_value, "csrf_token": CSRF_TOKEN},
    )


def test_validator_edit_rejects_non_numeric_magic(monkeypatch):
    store = {}
    _setup(monkeypatch, store)
    client = _client_with_session()

    resp = _post_add(client, "abc")

    assert resp.status_code == 200  # re-rendered form, not a redirect
    assert "nuevo" not in store
    assert "abc" not in store
    assert store == {}  # nothing written


def test_validator_edit_rejects_unknown_magic(monkeypatch):
    store = {}
    _setup(monkeypatch, store)
    client = _client_with_session()

    resp = _post_add(client, "1234")  # numeric but not in mappings

    assert resp.status_code == 200
    assert "1234" not in store
    assert store == {}


def test_validator_edit_rejects_clobbering_existing_entry(monkeypatch):
    store = {"9001": {"instrument": "existing"}}
    _setup(monkeypatch, store)
    client = _client_with_session()

    resp = _post_add(client, "9001")  # known, but already has a store entry

    assert resp.status_code == 200
    # existing entry left intact, not overwritten by the blank add form
    assert store == {"9001": {"instrument": "existing"}}


def test_validator_edit_rejected_add_preserves_typed_fields(monkeypatch):
    store = {}
    _setup(monkeypatch, store)
    client = _client_with_session()

    resp = client.post(
        "/validator/edit/nuevo",
        data={
            "magic": "1234",  # numeric but unknown -> rejected
            "bt_win_rate": "42.4",
            "csrf_token": CSRF_TOKEN,
        },
    )

    assert resp.status_code == 200  # re-rendered form, not a redirect
    assert store == {}  # nothing written on the error path
    html = resp.get_data(as_text=True)
    # the already-typed Backtest value must survive so the user only re-fixes
    # the magic number, not the whole form
    assert "42.4" in html


def test_validator_edit_accepts_valid_known_magic(monkeypatch):
    store = {}
    _setup(monkeypatch, store)
    client = _client_with_session()

    resp = _post_add(client, "9001")  # known and not yet in store

    assert resp.status_code == 302  # redirect to /validator on success
    assert "9001" in store  # keyed by the normalized magic, not "nuevo"
    assert "nuevo" not in store


def test_build_sidebar_eas_includes_color_per_ea():
    parsed_data = {"ea_names": ["MyEA", "OtherEA"]}
    config = {"mappings": {"MyEA": {"active": True}, "OtherEA": {"active": True}}}
    ea_colors = {"MyEA": "#111111", "OtherEA": "#222222"}

    with ea_analyzer.app.test_request_context("/"):
        sidebar = ea_analyzer.build_sidebar_eas(
            parsed_data, config, ea_colors=ea_colors
        )

    assert len(sidebar) == 2
    colors = {item["name"]: item["color"] for item in sidebar}
    assert colors == {"MyEA": "#111111", "OtherEA": "#222222"}


def test_build_sidebar_eas_color_defaults_when_missing():
    parsed_data = {"ea_names": ["MyEA"]}
    config = {"mappings": {"MyEA": {"active": True}}}

    with ea_analyzer.app.test_request_context("/"):
        sidebar = ea_analyzer.build_sidebar_eas(parsed_data, config)

    assert sidebar[0]["color"] == "#4FC3F7"
