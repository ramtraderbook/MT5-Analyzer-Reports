import json

import pytest

import local_json


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


def test_load_local_json_preserves_corrupt_file_instead_of_overwriting_with_example(tmp_path):
    """
    C2 regression: load_local_json used to treat "failed to parse" the same
    as "not present" -- it fell through to the .example candidate and then
    WROTE the example content over the real (corrupt) file, permanently
    destroying whatever user data survived the corruption. The corrupt file
    must instead be preserved untouched under a `.corrupt` sibling, and only
    a genuinely ABSENT real file may be seeded from the example.
    """
    path = tmp_path / "config.json"
    example_path = tmp_path / "config.example.json"

    corrupt_bytes = b'{"mappings": {"MyEA": {"magic": 1}'  # truncated, invalid JSON
    path.write_bytes(corrupt_bytes)
    example_path.write_text(json.dumps({"mappings": {}, "seeded": True}), encoding="utf-8")

    result = local_json.load_local_json(str(path), {"mappings": {}, "default": True})

    corrupt_path = tmp_path / "config.json.corrupt"
    assert corrupt_path.exists()
    assert corrupt_path.read_bytes() == corrupt_bytes
    assert result == {"mappings": {}, "seeded": True}


def test_save_local_json_never_truncates_existing_file_on_write_failure(tmp_path):
    """
    C2 regression: save_local_json used to open(path, "w") and json.dump
    directly into the real file -- opening in "w" mode truncates it
    immediately, so a failure partway through dump() (e.g. a
    non-serializable value, or a crash/disk-full) left the real file
    EMPTY/truncated with no backup. The atomic tmp-file + os.replace()
    design means a failed write can only ever touch the .tmp file, never
    the original.
    """
    path = tmp_path / "store.json"
    path.write_text(json.dumps({"safe": "data"}), encoding="utf-8")

    class Unserializable:
        pass

    with pytest.raises(TypeError):
        local_json.save_local_json(str(path), {"bad": Unserializable()})

    assert json.loads(path.read_text(encoding="utf-8")) == {"safe": "data"}
    assert not (tmp_path / "store.json.tmp").exists()


def test_second_corruption_does_not_clobber_the_first_preserved_snapshot(tmp_path):
    """
    A `.corrupt` snapshot is the only surviving copy of that data. A second
    corruption of the same file must not os.replace() over it.
    """
    path = tmp_path / "config.json"

    path.write_text('{"first": ', encoding="utf-8")
    local_json.load_local_json(str(path), {"default": True})

    path.write_text('{"second": ', encoding="utf-8")
    local_json.load_local_json(str(path), {"default": True})

    assert (tmp_path / "config.json.corrupt").read_text(encoding="utf-8") == '{"first": '
    assert (tmp_path / "config.json.corrupt.1").read_text(encoding="utf-8") == '{"second": '
