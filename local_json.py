import copy
import json
import os


def _example_path(path: str) -> str:
    root, ext = os.path.splitext(path)
    return f"{root}.example{ext}"


def load_local_json(path: str, default):
    candidates = [path, _example_path(path)]

    for candidate in candidates:
        if not os.path.exists(candidate):
            continue

        try:
            with open(candidate, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue

        if candidate != path:
            save_local_json(path, data)
        return data

    return copy.deepcopy(default)


def save_local_json(path: str, data) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
