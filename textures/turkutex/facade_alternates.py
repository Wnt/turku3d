"""Select a different panorama for rejected street facades."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .facade_batch import write_json
from .facade_direct import best_view
from .jobs import _views
from .streetview.facades import load_facades


def alternate_view(
    original: dict | None, views: list[dict], *, older_than_newest: bool = False
) -> dict | None:
    full = [view for view in views if view.get("part") == "full"]
    if older_than_newest and full:
        newest = max(view.get("date") or "" for view in full)
        full = [view for view in full if (view.get("date") or "") < newest]
    if original:
        full = [view for view in full if view["pano_id"] != original["pano_id"]]
    if not full:
        return None
    return max(
        full,
        key=lambda view: (
            view["source"] != original["source"] if original else True,
            view.get("score") or 0,
            view.get("px_per_m") or 0,
        ),
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("facades", type=Path)
    ap.add_argument("--original-refs", type=Path, required=True)
    ap.add_argument("--alternate-refs", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--older-than-newest", action="store_true")
    a = ap.parse_args(argv)
    data = json.loads(a.facades.read_text())
    by_id = {f.id: f for f in load_facades(a.facades)}
    selected = []
    for feature in data["features"]:
        f = by_id[f"{feature['properties']['building_id']}_{feature['properties']['facade_index']}"]
        try:
            original = best_view(f, a.original_refs)
        except ValueError:
            original = None
        refs_json = a.alternate_refs / f.id / "refs.json"
        views = _views(refs_json.parent) if refs_json.is_file() else []
        choice = alternate_view(original, views, older_than_newest=a.older_than_newest)
        if choice is None:
            continue
        write_json(a.alternate_refs / f.id / "preferred.json", {"view_id": choice["view_id"]})
        selected.append(feature)
    data["features"] = selected
    write_json(a.output, data)
    print(json.dumps({"selected": len(selected), "total": len(by_id)}))


if __name__ == "__main__":
    main()
