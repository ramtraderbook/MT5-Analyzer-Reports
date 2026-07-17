import copy
import json
import logging
import os

logger = logging.getLogger(__name__)


def _example_path(path: str) -> str:
    root, ext = os.path.splitext(path)
    return f"{root}.example{ext}"


def load_local_json(path: str, default):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            # Never clobber a previously preserved snapshot: an earlier
            # corruption is still the only copy of that data.
            corrupt_path = f"{path}.corrupt"
            suffix = 1
            while os.path.exists(corrupt_path):
                corrupt_path = f"{path}.corrupt.{suffix}"
                suffix += 1
            preserved = False
            try:
                os.replace(path, corrupt_path)
                preserved = True
            except OSError:
                pass
            if preserved:
                logger.warning(
                    "local_json: %s is corrupt (%s); preserved as %s",
                    path, exc, corrupt_path,
                )
            else:
                logger.warning(
                    "local_json: %s is corrupt (%s) and could not be preserved",
                    path, exc,
                )
                # `path` still holds the corrupt bytes -- never let the
                # .example fallback below overwrite it.
                return copy.deepcopy(default)

    example_path = _example_path(path)
    if os.path.exists(example_path):
        try:
            with open(example_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return copy.deepcopy(default)
        save_local_json(path, data)
        return data

    return copy.deepcopy(default)


def save_local_json(path: str, data) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    tmp_path = f"{path}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
