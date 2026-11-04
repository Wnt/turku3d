#!/usr/bin/env python3
"""Read the in-game object editor's edit sessions (~/turku3d/edits/<session>/, format: EDIT-FORMAT.md).

    edit_sessions.py list                      sessions with their counts
    edit_sessions.py effective [SESSION...]    the final edit of every object (JSON lines), undone edits removed
    edit_sessions.py clusters [SESSION...]     effective edits grouped by category, type, dataset, action and reason
    edit_sessions.py show SESSION...           the close summary (summary.txt), the folder and its counts
    edit_sessions.py pull SESSION...           copy sessions from the city.lab share (osgallery-dev) into the root
    edit_sessions.py import FILE|-             a close dialog's session JSON (city.lab: local only) into the root

No session argument means every session under the root (TURKU3D_EDITS or ~/turku3d/edits). A session id from the
close dialog resolves under the root, then under the city.lab share's store when it is on this machine
(~/turku3d-local/city/edits on osgallery-dev); on the Mac, `pull` copies a city.lab session into the root first.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path


def root() -> Path:
    return Path(os.environ.get("TURKU3D_EDITS", Path.home() / "turku3d/edits"))


# The city.lab share's store (tools/share/units/city-web.service `--edits`), and the host it lives on.
SHARE_HOST = os.environ.get("TURKU3D_SHARE_SSH", "osgallery-dev")
SHARE_EDITS = "turku3d-local/city/edits"


def find(name: str) -> Path:
    """A session folder by id: the root first, then the share's store on this machine."""
    for base in (root(), Path.home() / SHARE_EDITS):
        if (base / name / "session.json").exists():
            return base / name
    return root() / name


def rows(session: Path) -> list[dict]:
    """The session's rows in seq order; torn lines are skipped (the server writes whole files atomically)."""
    out = []
    try:
        text = (session / "edits.jsonl").read_text()
    except FileNotFoundError:
        return out
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("format") == "turku3d-edit" and row.get("version") == 1:
            out.append(row)
    return sorted(out, key=lambda r: (r.get("seq", 0), r.get("id", "")))


NOT_EDITS = ("undo", "tag")


def effective(session_rows: list[dict]) -> list[dict]:
    """Edits not undone, in order, with the newest tag row of each folded in (`reason`)."""
    undone = {r["undoes"] for r in session_rows if r.get("action") == "undo"}
    tags = {r["edit"]: r for r in session_rows if r.get("action") == "tag"}  # rows are in seq order: newest wins
    out = []
    for r in session_rows:
        if r.get("action") in NOT_EDITS or r["id"] in undone:
            continue
        tag = tags.get(r["id"])
        out.append({**r, "reason": tag["reason"]} if tag else r)
    return out


def final(session_rows: list[dict]) -> dict[str, dict]:
    """The last effective edit per object key: its `after` against `original` is the object's net change."""
    out: dict[str, dict] = {}
    for edit in effective(session_rows):
        out[edit["object"]["key"]] = edit
    return out


def sessions(names: list[str]) -> list[Path]:
    base = root()
    if names:
        return [find(n) for n in names]
    return sorted(p for p in base.iterdir() if (p / "session.json").exists()) if base.exists() else []


def dataset(edit: dict) -> str:
    return str(edit["object"].get("provenance", {}).get("dataset") or edit["object"]["object_id"].split(":", 1)[0])


def summary(edits: list[dict]) -> dict:
    """Counts and medians a rule author starts from: moves, turns and the original surroundings."""
    moves = [e["delta"]["horizontal_m"] for e in edits if e.get("delta")]
    turns = [abs(e["delta"]["yaw_deg"]) for e in edits if e.get("delta") and e["changes"].get("rotated")]
    surfaces: dict[str, int] = defaultdict(int)
    road_edges = []
    for e in edits:
        before = (e.get("context") or {}).get("before") or {}
        surfaces[str(before.get("surface"))] += 1
        road = before.get("road") or {}
        if road.get("edge_m") is not None:
            road_edges.append(road["edge_m"])
    med = lambda xs: round(statistics.median(xs), 2) if xs else None  # noqa: E731
    return {"count": len(edits), "median_move_m": med(moves), "median_turn_deg": med(turns),
            "original_surface": dict(sorted(surfaces.items(), key=lambda kv: -kv[1])),
            "median_original_road_edge_m": med(road_edges),
            "objects": [e["object"]["key"] for e in edits]}


def import_export(text: str) -> Path:
    """Write a session export (closeDialog.ts: format turku3d-edit-session-export) as a session folder under the root:
    session.json, edits.jsonl (one line per row id, in seq order) and summary.txt. Existing rows are kept unless the
    export has the same id with a higher or equal `rev`."""
    data = json.loads(text)
    if data.get("format") != "turku3d-edit-session-export" or data.get("version") != 1:
        raise SystemExit("not a turku3d-edit-session-export v1 document")
    meta = data["session"]
    name = str(meta.get("id", ""))
    if not name or not name.replace("-", "").replace("_", "").isalnum():
        raise SystemExit(f"bad session id: {name!r}")
    folder = root() / name
    folder.mkdir(parents=True, exist_ok=True)
    by_id = {r["id"]: r for r in rows(folder)}
    for r in data.get("rows", []):
        old = by_id.get(r.get("id"))
        if r.get("session") == name and (old is None or r.get("rev", 0) >= old.get("rev", 0)):
            by_id[r["id"]] = r
    ordered = sorted(by_id.values(), key=lambda r: (r.get("seq", 0), r.get("id", "")))
    (folder / "edits.jsonl").write_text("".join(json.dumps(r) + "\n" for r in ordered))
    (folder / "session.json").write_text(json.dumps({**meta, "imported_from": "export"}, indent=1) + "\n")
    if data.get("summary"):
        (folder / "summary.txt").write_text(data["summary"])
    return folder


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["list", "effective", "clusters", "show", "pull", "import"])
    parser.add_argument("sessions", nargs="*")
    args = parser.parse_args(argv)
    if args.command in ("show", "pull") and not args.sessions:
        parser.error(f"{args.command} needs a session id")
    if args.command == "import":
        for name in args.sessions or ["-"]:
            print(import_export(sys.stdin.read() if name == "-" else Path(name).read_text()))
        return 0
    if args.command == "pull":
        for name in args.sessions:
            if not name.replace("-", "").replace("_", "").isalnum():
                parser.error(f"not a session id: {name}")
            subprocess.run(["rsync", "-a", f"{SHARE_HOST}:{SHARE_EDITS}/{name}/", str(root() / name) + "/"], check=True)
            print(root() / name)
        return 0
    found = sessions(args.sessions)
    if args.command == "show":
        for s in found:
            if not (s / "session.json").exists():
                print(f"{s.name}: not found under {root()} or ~/{SHARE_EDITS}; a city.lab session: "
                      f"edit_sessions.py pull {s.name}", file=sys.stderr)
                return 1
            meta = json.loads((s / "session.json").read_text())
            r = rows(s)
            summary_file = s / "summary.txt"
            text = summary_file.read_text() if summary_file.exists() else meta.get("summary")
            print(text.rstrip() if text else "(no summary: the session closed before summaries, or it is still open)")
            print(f"folder: {s}")
            print(json.dumps({"session": s.name, "started": meta.get("started"), "ended": meta.get("ended"),
                              "player": meta.get("player"), "build": (meta.get("build") or {}).get("url"),
                              "rows": len(r), "effective": len(effective(r)), "objects": len(final(r))}))
            print(f"full rows: edit_sessions.py effective {s.name} · clusters: edit_sessions.py clusters {s.name}")
        return 0
    if args.command == "list":
        for s in found:
            meta = json.loads((s / "session.json").read_text())
            r = rows(s)
            print(json.dumps({"session": s.name, "started": meta.get("started"), "ended": meta.get("ended"),
                              "rows": len(r), "effective": len(effective(r)), "objects": len(final(r))}))
        return 0
    edits = [e for s in found for e in final(rows(s)).values()]
    if args.command == "effective":
        for e in edits:
            print(json.dumps(e, separators=(",", ":")))
        return 0
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for e in edits:
        groups[(e["object"]["category"], e["object"]["type"], dataset(e), e["action"], e.get("reason"))].append(e)
    for key, group in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        category, kind, source, action, reason = key
        print(json.dumps({"category": category, "type": kind, "dataset": source, "action": action, "reason": reason,
                          **summary(group)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
