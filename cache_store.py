"""
cache_store.py - Disk-backed storage for parsed trade datasets.

Pure of Flask and session state: every function takes the directories, cache
key and prefix it operates on. ea_analyzer binds these to its module config
(CACHE_DIR / APP_DIR and the live/incubation prefixes) and to the session,
keeping this module a testable storage leaf.

Behavior is pinned by tests/test_cache_store.py.
"""

import copy
import glob
import json
import logging
import os
import time
import uuid
from datetime import datetime

logger = logging.getLogger(__name__)

DEFAULT_CACHE_TTL = 7200  # seconds; files older than this are eligible for reaping


def serialize_parsed_data(data):
    """Convert datetime objects to ISO strings for JSON serialization.

    Deep-copies first, so the caller's in-memory data is never mutated."""
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


def _is_safe_cache_key(cache_key):
    """A cache key must be a single bare filename component.

    Real keys are UUIDs (see save_cache). Anything carrying a path separator or
    a parent-directory reference ("..") is rejected at the storage boundary so a
    tampered/forged key can never make `{prefix}{cache_key}.json` escape the
    cache directory (path-injection defense-in-depth §11).
    """
    if not isinstance(cache_key, str) or not cache_key:
        return False
    if "/" in cache_key or "\\" in cache_key or ".." in cache_key:
        return False
    if os.sep in cache_key or (os.altsep and os.altsep in cache_key):
        return False
    # Must not resolve to anything other than itself as a filename component.
    return cache_key == os.path.basename(cache_key)


def cache_file_path(cache_dir, cache_key, prefix):
    if not _is_safe_cache_key(cache_key):
        raise ValueError(f"unsafe cache key: {cache_key!r}")
    return os.path.join(cache_dir, f"{prefix}{cache_key}.json")


def legacy_cache_file_path(app_dir, cache_key, prefix):
    if not _is_safe_cache_key(cache_key):
        raise ValueError(f"unsafe cache key: {cache_key!r}")
    return os.path.join(app_dir, f"{prefix}{cache_key}.json")


def resolve_cache_path(cache_dir, app_dir, cache_key, prefix):
    """Return the canonical cache path, migrating a legacy (app-dir) file into
    the canonical cache dir on the way if that is where the data still lives."""
    # An unsafe/forged key resolves to nothing rather than raising into the
    # request handler — there is no such cached file to serve.
    if not _is_safe_cache_key(cache_key):
        return None

    cache_path = cache_file_path(cache_dir, cache_key, prefix)
    if os.path.exists(cache_path):
        return cache_path

    legacy_path = legacy_cache_file_path(app_dir, cache_key, prefix)
    if not os.path.exists(legacy_path):
        return cache_path

    try:
        os.replace(legacy_path, cache_path)
        return cache_path
    except OSError:
        return legacy_path


def delete_cache_file(cache_dir, app_dir, cache_key, prefix):
    # Reject forged keys at the boundary: nothing safe to delete, and we must
    # never let a separator/".." key steer os.remove outside the cache dir.
    if not _is_safe_cache_key(cache_key):
        return

    for cache_path in {
        cache_file_path(cache_dir, cache_key, prefix),
        legacy_cache_file_path(app_dir, cache_key, prefix),
    }:
        try:
            if os.path.exists(cache_path):
                os.remove(cache_path)
        except OSError:
            pass


def atomic_write_json(path, data):
    """Write JSON to `path` without ever leaving a truncated file on disk."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def save_cache(cache_dir, data, prefix):
    """Save parsed data to a cache file under `prefix`. Returns the cache key."""
    cache_key = str(uuid.uuid4())
    cache_path = cache_file_path(cache_dir, cache_key, prefix)
    atomic_write_json(cache_path, serialize_parsed_data(data))
    return cache_key


def load_cache(cache_dir, app_dir, cache_key, prefix):
    """Load cached parsed data under `prefix`. Returns dict or None."""
    if not cache_key:
        return None
    cache_path = resolve_cache_path(cache_dir, app_dir, cache_key, prefix)
    # An unsafe/forged key resolves to None (see resolve_cache_path). Treat it
    # as a clean cache miss rather than calling os.path.exists(None).
    if cache_path is None:
        return None
    if not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("load_cache: %s is corrupt (%s)", cache_path, exc)
        return None
    # Mark this file as actively used so cleanup_old_caches() never reaps a
    # dataset that is still being read, regardless of when it was written.
    try:
        os.utime(cache_path, None)
    except OSError:
        pass
    return data


def cleanup_old_caches(cache_dir, app_dir, prefixes, protected_paths, ttl=DEFAULT_CACHE_TTL):
    """Delete cache files older than `ttl` seconds across the canonical and
    legacy dirs for every prefix, except the explicitly protected paths (the
    files backing the current session, which must survive regardless of age)."""
    patterns = []
    for prefix in prefixes:
        patterns.append(os.path.join(cache_dir, f"{prefix}*.json"))
        patterns.append(os.path.join(app_dir, f"{prefix}*.json"))

    for pattern in patterns:
        for f in glob.glob(pattern):
            if f in protected_paths:
                continue
            try:
                if time.time() - os.path.getmtime(f) > ttl:
                    os.remove(f)
            except OSError:
                pass
