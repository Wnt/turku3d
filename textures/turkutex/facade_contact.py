"""Build a contact sheet from the selected references and per-facade results."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from .building_jobs import reference_matches
from .facade_batch import asset_path
from .facade_direct import contact_sheet
from .jobs import _views
from .post.pipeline import sha
from .streetview.facades import load_facades


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("facades", type=Path)
    ap.add_argument("--assembly", type=Path, required=True)
    ap.add_argument("--refs", type=Path, nargs="+", required=True)
    ap.add_argument("--staging", type=Path, nargs="+", required=True)
    ap.add_argument("--labels", type=Path, nargs="*", default=[])
    ap.add_argument("--results", type=Path, nargs="*", default=[])
    ap.add_argument("--contact", type=Path, required=True)
    ap.add_argument("--radius", type=float, default=500)
    a = ap.parse_args(argv)
    work = a.contact.parent / "facade-contact-links"
    shutil.rmtree(work, ignore_errors=True)
    refs_root, staging_root = work / "refs", work / "staging"
    refs_root.mkdir(parents=True)
    (staging_root / "post").mkdir(parents=True)
    reasons = {}
    for labels_path in a.labels:
        if labels_path.is_file():
            for facade_id, verdict in json.loads(labels_path.read_text()).items():
                if verdict.get("usable") is False:
                    reasons[facade_id] = "Visual label rejected reference: " + verdict["problem"]
    for log in a.results:
        if log.is_file():
            for line in log.read_text().splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and "facade_id" in row:
                    reasons[row["facade_id"]] = row.get("reason")
    for f in load_facades(a.facades):
        meta_path = asset_path(f, a.assembly).with_suffix(".json")
        metadata = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
        bound_view = next(
            (ref.get("view_id") for ref in metadata.get("references", []) if ref.get("view_id")),
            None,
        )
        choices = []
        for root in a.refs:
            if not reference_matches(f, root):
                continue
            directory = root / f.id
            for view in _views(directory):
                if view.get("part") == "full":
                    choices.append((root, view))
        if choices:
            matching = [row for row in choices if row[1]["view_id"] == bound_view]
            source = (matching or choices)[0][0]
            (refs_root / f.id).symlink_to(source / f.id, target_is_directory=True)
        preview_hash = metadata.get("preview_sha256")
        previews = [root / "post" / f.id for root in a.staging]
        matching = [
            path
            for path in previews
            if (path / "albedo.png").is_file()
            and preview_hash
            and sha(path / "albedo.png") == preview_hash
        ]
        existing = [path for path in previews if (path / "albedo.png").is_file()]
        if matching or existing:
            (staging_root / "post" / f.id).symlink_to(
                (matching or existing)[0], target_is_directory=True
            )
        if f.id not in reasons:
            acquired = any((root / f.id / "refs.json").is_file() for root in a.refs)
            if not acquired:
                reasons[f.id] = "Reference acquisition incomplete at timebox"
            elif not choices:
                reasons[f.id] = "No full-wall panorama view found"
            else:
                reasons[f.id] = "Not processed within timebox"
    rows = [{"facade_id": facade_id, "reason": reason} for facade_id, reason in reasons.items()]
    contact_sheet(a.facades, refs_root, staging_root, a.assembly, rows, a.contact, radius=a.radius)


if __name__ == "__main__":
    main()
