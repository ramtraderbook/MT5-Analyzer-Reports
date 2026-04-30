import json


def test_load_config_bootstraps_local_file_from_example(monkeypatch, tmp_path):
    import ea_analyzer

    config_path = tmp_path / "config.json"
    example_path = tmp_path / "config.example.json"
    expected = {
        "mappings": {},
        "last_file": None,
        "last_updated": None,
        "loaded_files_live": [],
    }
    example_path.write_text(json.dumps(expected), encoding="utf-8")

    monkeypatch.setattr(ea_analyzer, "CONFIG_PATH", str(config_path))

    loaded = ea_analyzer.load_config()

    assert loaded == expected
    assert json.loads(config_path.read_text(encoding="utf-8")) == expected


def test_load_validator_store_bootstraps_local_file_from_example(monkeypatch, tmp_path):
    import validator

    store_path = tmp_path / "validator_store.json"
    example_path = tmp_path / "validator_store.example.json"
    expected = {"9001": {"bt": {"win_rate": 55.0}}}
    example_path.write_text(json.dumps(expected), encoding="utf-8")

    monkeypatch.setattr(validator, "VALIDATOR_STORE_PATH", str(store_path))

    loaded = validator.load_validator_store()

    assert loaded == expected
    assert json.loads(store_path.read_text(encoding="utf-8")) == expected
