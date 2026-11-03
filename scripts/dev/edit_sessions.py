#!/usr/bin/env python3
"""Read the in-game object editor's edit sessions (~/turku3d/edits/<session>/, format: EDIT-FORMAT.md).

    edit_sessions.py list                      sessions with their counts
    edit_sessions.py effective [SESSION...]    the final edit of every object (JSON lines), undone edits removed
    edit_sessions.py clusters [SESSION...]     effective edits grouped by category, type, dataset, action and reason

No session argument means every session under the root (TURKU3D_EDITS or ~/turku3d/edits).
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def root() -> Path:
    return Path(os.environ.get("TURKU3D_EDITS", Path.home() / "turku3d/edits"))


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


def effective(session_rows: list[dict]) -> list[dict]:
    """Edits not undone, in order."""
    undone = {r["undoes"] for r in session_rows if r.get("action") == "undo"}
    return [r for r in session_rows if r.get("action") != "undo" and r["id"] not in undone]


def final(session_rows: list[dict]) -> dict[str, dict]:
    """The last effective edit per object key: its `after` against `original` is the object's net change."""
    out: dict[str, dict] = {}
    for edit in effective(session_rows):
        out[edit["object"]["key"]] = edit
    return out


def sessions(names: list[str]) -> list[Path]:
    base = root()
    if names:
        return [base / n for n in names]
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["list", "effective", "clusters"])
    parser.add_argument("sessions", nargs="*")
    args = parser.parse_args(argv)
    found = sessions(args.sessions)
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
