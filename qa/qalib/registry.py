"""views.jsonl: the reference view registry (one JSON object per line, see views/view.schema.json)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

from .paths import QA_DIR, resolve

SCHEMA_PATH = QA_DIR / "views" / "view.schema.json"


def default_registry() -> Path:
    """The committed registry. Images it points to live in data/qa/refs/ (not committed) and can be
    re-created from the rows' source fields with `views/sv_views.py refetch`."""
    return QA_DIR / "views" / "views.jsonl"


def _schema():
    return json.loads(SCHEMA_PATH.read_text())


def validate(view: dict) -> None:
    jsonschema.validate(view, _schema())


def load(path: str | Path | None = None, check_images: bool = False) -> list[dict]:
    path = Path(path or default_registry())
    if not path.exists():
        return []
    out = []
    for ln, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        v = json.loads(line)
        try:
            validate(v)
        except jsonschema.ValidationError as e:
            raise ValueError(f"{path}:{ln}: {e.message}") from e
        if check_images and not resolve(v["image"]).exists():
            raise FileNotFoundError(f"{path}:{ln}: missing image {v['image']}")
        out.append(v)
    ids = [v["id"] for v in out]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise ValueError(f"{path}: duplicate ids {sorted(dup)}")
    return out


def upsert(view: dict, path: str | Path | None = None) -> Path:
    """Insert or replace (by id) one view, keeping file order."""
    view.setdefault("created", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    validate(view)
    path = Path(path or default_registry())
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = load(path) if path.exists() else []
    rows = [r for r in rows if r["id"] != view["id"]]
    rows.append(view)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    return path


def get(view_id: str, path: str | Path | None = None) -> dict:
    for v in load(path):
        if v["id"] == view_id:
            return v
    raise KeyError(view_id)
