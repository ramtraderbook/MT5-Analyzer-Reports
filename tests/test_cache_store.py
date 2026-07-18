"""
test_cache_store.py - Characterization tests for the disk cache / storage layer
in ea_analyzer.py (the _serialize/_resolve/save/load/cleanup/_delete functions).

These pin the current behavior BEFORE any extraction of the layer into its own
module (P-C 1B gate: the glue must be covered and green on current code first,
because a bug here corrupts the runtime cache). Every test drives the real
functions against a tmp CACHE_DIR / APP_DIR, never the repo's runtime_cache.
"""

import json
import os
import time
from datetime import datetime

import pytest

import ea_analyzer


@pytest.fixture
def cache_dirs(monkeypatch, tmp_path):
    """Point the cache layer at isolated tmp dirs (canonical + legacy)."""
    cache_dir = tmp_path / "runtime_cache"
    app_dir = tmp_path / "app"
    cache_dir.mkdir()
    app_dir.mkdir()
    monkeypatch.setattr(ea_analyzer, "CACHE_DIR", str(cache_dir))
    monkeypatch.setattr(ea_analyzer, "APP_DIR", str(app_dir))
    return cache_dir, app_dir


def _parsed(dt=datetime(2026, 1, 2, 10, 0, 0)):
    return {
        "closed_trades": [
            {"position_id": 1, "open_time": dt, "close_time": dt, "net_pnl": 10.0}
        ],
        "open_positions": [{"position_id": 2, "open_time": dt}],
        "ea_names": ["MyEA"],
    }


# ── serialization ───────────────────────────────────────────────────────────


def test_serialize_parsed_data_converts_datetimes_to_iso_and_does_not_mutate_input():
    dt = datetime(2026, 1, 2, 10, 0, 0)
    src = _parsed(dt)
    out = ea_analyzer._serialize_parsed_data(src)
    # closed + open datetimes become ISO strings
    assert out["closed_trades"][0]["open_time"] == dt.isoformat()
    assert out["closed_trades"][0]["close_time"] == dt.isoformat()
    assert out["open_positions"][0]["open_time"] == dt.isoformat()
    # the original object is untouched (deep-copied)
    assert src["closed_trades"][0]["open_time"] is dt


# ── save / load roundtrip ───────────────────────────────────────────────────


def test_save_then_load_roundtrips_through_disk(cache_dirs):
    key = ea_analyzer.save_cache(_parsed())
    assert key  # a uuid string
    loaded = ea_analyzer.load_cache(key)
    assert loaded["ea_names"] == ["MyEA"]
    # datetimes come back as the ISO strings they were serialized to
    assert loaded["closed_trades"][0]["open_time"] == datetime(2026, 1, 2, 10, 0, 0).isoformat()


def test_live_and_incubation_caches_do_not_collide(cache_dirs):
    live_key = ea_analyzer.save_cache({"ea_names": ["LIVE"]})
    inc_key = ea_analyzer.save_incubation_cache({"ea_names": ["INC"]})
    assert ea_analyzer.load_cache(live_key)["ea_names"] == ["LIVE"]
    assert ea_analyzer.load_incubation_cache(inc_key)["ea_names"] == ["INC"]
    # a live key must not resolve through the incubation loader and vice versa
    assert ea_analyzer.load_incubation_cache(live_key) is None
    assert ea_analyzer.load_cache(inc_key) is None


def test_load_cache_returns_none_for_missing_key_and_empty_key(cache_dirs):
    assert ea_analyzer.load_cache("does-not-exist") is None
    assert ea_analyzer.load_cache("") is None
    assert ea_analyzer.load_cache(None) is None


def test_load_cache_returns_none_for_corrupt_json(cache_dirs):
    cache_dir, _ = cache_dirs
    bad = cache_dir / f"{ea_analyzer.LIVE_CACHE_PREFIX}broken.json"
    bad.write_text("{ not json", encoding="utf-8")
    assert ea_analyzer.load_cache("broken") is None


# ── legacy path migration ───────────────────────────────────────────────────


def test_resolve_cache_path_migrates_legacy_file_into_cache_dir(cache_dirs):
    cache_dir, app_dir = cache_dirs
    # a dataset written by an older version lives in APP_DIR (legacy location)
    legacy = app_dir / f"{ea_analyzer.LIVE_CACHE_PREFIX}old.json"
    legacy.write_text(json.dumps({"ea_names": ["OLD"]}), encoding="utf-8")

    resolved = ea_analyzer._resolve_cache_path("old", ea_analyzer.LIVE_CACHE_PREFIX)

    canonical = cache_dir / f"{ea_analyzer.LIVE_CACHE_PREFIX}old.json"
    assert resolved == str(canonical)
    assert canonical.exists()      # moved into the canonical dir
    assert not legacy.exists()     # and removed from the legacy location
    assert ea_analyzer.load_cache("old")["ea_names"] == ["OLD"]


# ── cleanup ─────────────────────────────────────────────────────────────────


def _age(path, seconds):
    old = time.time() - seconds
    os.utime(path, (old, old))


def test_cleanup_reaps_old_files_but_protects_the_active_keys(cache_dirs):
    cache_dir, _ = cache_dirs
    stale_key = ea_analyzer.save_cache({"ea_names": ["STALE"]})
    active_key = ea_analyzer.save_cache({"ea_names": ["ACTIVE"]})
    inc_active = ea_analyzer.save_incubation_cache({"ea_names": ["INC"]})

    # age all three past the 2h cutoff
    for k, prefix in [
        (stale_key, ea_analyzer.LIVE_CACHE_PREFIX),
        (active_key, ea_analyzer.LIVE_CACHE_PREFIX),
        (inc_active, ea_analyzer.INCUBATION_CACHE_PREFIX),
    ]:
        _age(cache_dir / f"{prefix}{k}.json", 7201)

    ea_analyzer.cleanup_old_caches(keep_live_key=active_key, keep_incubation_key=inc_active)

    # the unprotected stale one is gone; both protected keys survive despite age
    assert ea_analyzer.load_cache(stale_key) is None
    assert ea_analyzer.load_cache(active_key)["ea_names"] == ["ACTIVE"]
    assert ea_analyzer.load_incubation_cache(inc_active)["ea_names"] == ["INC"]


def test_cleanup_keeps_recent_files(cache_dirs):
    key = ea_analyzer.save_cache({"ea_names": ["FRESH"]})
    # freshly written (mtime ~now) -> must not be reaped even if not protected
    ea_analyzer.cleanup_old_caches()
    assert ea_analyzer.load_cache(key)["ea_names"] == ["FRESH"]


def test_cleanup_reaps_from_the_legacy_dir_too(cache_dirs):
    cache_dir, app_dir = cache_dirs
    legacy = app_dir / f"{ea_analyzer.LIVE_CACHE_PREFIX}legacy.json"
    legacy.write_text(json.dumps({"ea_names": ["LEG"]}), encoding="utf-8")
    _age(legacy, 7201)
    ea_analyzer.cleanup_old_caches()
    assert not legacy.exists()


# ── deletion ────────────────────────────────────────────────────────────────


def test_delete_cache_file_removes_both_canonical_and_legacy(cache_dirs):
    cache_dir, app_dir = cache_dirs
    canonical = cache_dir / f"{ea_analyzer.LIVE_CACHE_PREFIX}k.json"
    legacy = app_dir / f"{ea_analyzer.LIVE_CACHE_PREFIX}k.json"
    canonical.write_text("{}", encoding="utf-8")
    legacy.write_text("{}", encoding="utf-8")

    ea_analyzer._delete_cache_file("k", ea_analyzer.LIVE_CACHE_PREFIX)

    assert not canonical.exists()
    assert not legacy.exists()


def test_delete_cache_file_is_a_noop_for_empty_key(cache_dirs):
    # must not raise
    ea_analyzer._delete_cache_file("", ea_analyzer.LIVE_CACHE_PREFIX)
    ea_analyzer._delete_cache_file(None, ea_analyzer.LIVE_CACHE_PREFIX)


# ── path-injection defense (§11) ──────────────────────────────────────────────


import cache_store


@pytest.mark.parametrize("bad_key", [
    "../evil", r"..\evil", "a/b", r"a\b", "..", "foo/../bar", "/abs", "sub/key",
])
def test_cache_file_path_rejects_keys_with_separators_or_dotdot(bad_key):
    """cache_file_path / legacy_cache_file_path must reject any key carrying a
    path separator or '..' so `{prefix}{key}.json` can never escape the cache
    dir (path-injection defense-in-depth §11)."""
    with pytest.raises(ValueError):
        cache_store.cache_file_path("/cache", bad_key, "live_")
    with pytest.raises(ValueError):
        cache_store.legacy_cache_file_path("/app", bad_key, "live_")


def test_cache_file_path_accepts_normal_uuid_like_key():
    key = "1b964ec0-0adb-4005-b9ad-986ac5a80705"
    path = cache_store.cache_file_path("/cache", key, "live_")
    assert path.endswith(f"live_{key}.json")


@pytest.mark.parametrize("bad_key", ["../evil", "a/b", "..", "foo/../bar"])
def test_resolve_and_delete_treat_forged_keys_as_noop(cache_dirs, bad_key):
    """resolve_cache_path returns None and delete_cache_file is a no-op for a
    forged key -- neither raises into the request handler nor touches disk
    outside the cache dir."""
    cache_dir, app_dir = cache_dirs
    assert cache_store.resolve_cache_path(str(cache_dir), str(app_dir), bad_key, "live_") is None
    # must not raise, must not remove anything outside
    cache_store.delete_cache_file(str(cache_dir), str(app_dir), bad_key, "live_")


@pytest.mark.parametrize("bad_key", ["../evil", "a/b", r"..\x", "..", "foo/../bar"])
def test_load_cache_treats_forged_key_as_a_clean_miss(cache_dirs, bad_key):
    """load_cache must return a miss (None) for a forged key, never crash with
    os.path.exists(None) -- resolve_cache_path returns None for an unsafe key
    and load_cache must guard that rather than pass it to os.path.exists."""
    cache_dir, app_dir = cache_dirs
    assert cache_store.load_cache(str(cache_dir), str(app_dir), bad_key, "live_") is None


@pytest.mark.parametrize("bad_key", ["../evil", r"..\x", "a/b"])
def test_cleanup_old_caches_tolerates_a_forged_keep_key(cache_dirs, bad_key):
    """cleanup_old_caches() (ea_analyzer) is called from upload() with keep-keys
    sourced from the session. A forged keep-key must be skipped gracefully, not
    raise ValueError from cache_file_path() and 500 the whole upload."""
    # Must not raise, regardless of which keep-key is forged.
    ea_analyzer.cleanup_old_caches(keep_live_key=bad_key)
    ea_analyzer.cleanup_old_caches(keep_incubation_key=bad_key)
