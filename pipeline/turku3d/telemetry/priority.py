"""Rank buildings and missing ground tiles by what the player saw and where they went.

Reads the play sessions that tools/dev/serve-dist.mjs appends (``session-<YYYYMMDD>.jsonl``, one JSON sample per
second, game/src/telemetry/README.md) and writes ``priority.json`` plus a ``priority.md`` summary
(pipeline/turku3d/telemetry/README.md has the schema).

* Wall score: sum over samples of ``screen_fraction * clamp(60 / distance_m, 0.2, 3)``; a building is the sum of its
  walls.
* Missing tile score: seconds the player stood in a tile outside the built area, plus 0.5 per second it was one of
  the two tiles ahead along the view heading.
* Building status from the facade approval assembly: ``generated`` (its own generated style), ``derived-only``
  (derived maps without an own style), ``neighbour`` (only a neighbour's style) or ``unbound`` (no map).

    python -m turku3d.telemetry.priority [--sessions DIR] [--out FILE] [--hours N] [--assembly DIR]
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from turku3d.config import ORIGIN_E, ORIGIN_N, VISUAL_TILE_SIZE, data_root, work_root

TILE_ID = re.compile(r"^t0_(-?\d+)_(-?\d+)$")
TOP_BUILDINGS = 20
LOOK_WEIGHT = 0.5


def wall_weight(screen_fraction: float, distance_m: float) -> float:
    return screen_fraction * min(3.0, max(0.2, 60.0 / max(distance_m, 1e-6)))


def tile_bbox_en(tile: str) -> list[float]:
    """[e0, n0, e1, n1] of a level-0 visual tile (pipeline/turku3d/config.py grid)."""
    m = TILE_ID.match(tile)
    if not m:
        raise ValueError(f"not a level-0 visual tile id: {tile}")
    i, j = int(m[1]), int(m[2])
    s = VISUAL_TILE_SIZE
    return [ORIGIN_E + i * s, ORIGIN_N - (j + 1) * s, ORIGIN_E + (i + 1) * s, ORIGIN_N - j * s]


def read_sessions(sessions: Path, since: float | None) -> tuple[list[str], list[dict]]:
    """Samples from every session file (oldest first), optionally only those at or after unix time `since`."""
    names, samples = [], []
    for path in sorted(sessions.glob("session-*.jsonl")):
        used = False
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                sample = json.loads(line)
            except json.JSONDecodeError:
                continue  # a torn last line while the server writes
            if not isinstance(sample, dict) or not isinstance(sample.get("t"), (int, float)):
                continue
            if since is not None and sample["t"] < since:
                continue
            samples.append(sample)
            used = True
        if used:
            names.append(path.name)
    return names, samples


def building_status(assembly: Path) -> dict[str, str]:
    """Status per building with any bound map; buildings missing here are unbound."""
    manifest = assembly / "MANIFEST.json"
    bound = json.loads(manifest.read_text())["bound"] if manifest.exists() else []
    modes: dict[str, set[str]] = {}
    for facade_id in bound:
        building, index = facade_id.rsplit("_", 1)
        sidecar = assembly / "facades" / building / f"{index}.json"
        mode = json.loads(sidecar.read_text()).get("source_mode") if sidecar.exists() else None
        modes.setdefault(building, set()).add(mode or "generate")
    status = {}
    for building, found in modes.items():
        own_style = (assembly / "facades" / building / "style.json").exists()
        if own_style or "generate" in found:
            status[building] = "generated"
        elif "derived" in found:
            status[building] = "derived-only"
        else:
            status[building] = "neighbour"
    return status


def prioritize(
    names: list[str], samples: list[dict], status: dict[str, str], generated_at: str
) -> dict:
    walls: dict[str, dict] = {}
    tiles: dict[str, dict] = {}
    for sample in samples:
        for view in sample.get("facades") or []:
            try:
                facade_id = str(view["facade_id"])
                fraction, distance = float(view["screen_fraction"]), float(view["distance_m"])
            except (KeyError, TypeError, ValueError):
                continue
            if "_" not in facade_id or not math.isfinite(fraction) or not math.isfinite(distance):
                continue
            w = walls.setdefault(facade_id, {"score": 0.0, "seen": 0, "distance": 0.0, "max": 0.0})
            w["score"] += wall_weight(fraction, distance)
            w["seen"] += 1
            w["distance"] += distance
            w["max"] = max(w["max"], fraction)
        outside = sample.get("outside_tiles")
        if isinstance(outside, dict):
            if TILE_ID.match(str(outside.get("stand"))):
                tiles.setdefault(outside["stand"], {"stood": 0, "looked": 0})["stood"] += 1
            for tile in outside.get("ahead") or []:
                if TILE_ID.match(str(tile)):
                    tiles.setdefault(tile, {"stood": 0, "looked": 0})["looked"] += 1

    buildings: dict[str, list[dict]] = {}
    for facade_id, w in walls.items():
        buildings.setdefault(facade_id.rsplit("_", 1)[0], []).append(
            {
                "facade_id": facade_id,
                "score": round(w["score"], 4),
                "seen_seconds": w["seen"],
                "mean_distance_m": round(w["distance"] / w["seen"], 1),
                "max_screen_fraction": round(w["max"], 5),
            }
        )
    ranked = []
    for building, items in buildings.items():
        items.sort(key=lambda w: (-w["score"], w["facade_id"]))
        ranked.append(
            {
                "building_id": building,
                "score": round(sum(w["score"] for w in items), 4),
                "status": status.get(building, "unbound"),
                "best_wall": items[0]["facade_id"],
                "walls": items,
            }
        )
    ranked.sort(key=lambda b: (-b["score"], b["building_id"]))
    missing = [
        {
            "tile": tile,
            "score": round(t["stood"] + LOOK_WEIGHT * t["looked"], 1),
            "stood_seconds": t["stood"],
            "looked_seconds": t["looked"],
            "bbox_en": tile_bbox_en(tile),
        }
        for tile, t in tiles.items()
    ]
    missing.sort(key=lambda t: (-t["score"], t["tile"]))
    return {
        "generated_at": generated_at,
        "sessions": names,
        "sample_count": len(samples),
        "buildings": ranked,
        "missing_tiles": missing,
    }


def markdown(priority: dict) -> str:
    out = [
        "# Content priority from play telemetry",
        "",
        f"Generated {priority['generated_at']} from {priority['sample_count']} samples in "
        f"{len(priority['sessions'])} session file(s): {', '.join(priority['sessions']) or 'none'}.",
        "",
        f"## Top {TOP_BUILDINGS} buildings",
        "",
        "| # | Building | Status | Score | Best wall | Walls seen | Seen s (best) | Mean distance m (best) |",
        "| ---: | --- | --- | ---: | --- | ---: | ---: | ---: |",
    ]
    for rank, b in enumerate(priority["buildings"][:TOP_BUILDINGS], 1):
        best = b["walls"][0]
        out.append(
            f"| {rank} | `{b['building_id']}` | {b['status']} | {b['score']:.3f} | `{b['best_wall']}` | "
            f"{len(b['walls'])} | {best['seen_seconds']} | {best['mean_distance_m']} |"
        )
    if not priority["buildings"]:
        out.append("| | none seen | | | | | | |")
    out += [
        "",
        "## Missing tiles",
        "",
        "| Tile | Score | Stood s | Looked s | bbox E0, N0, E1, N1 |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for t in priority["missing_tiles"]:
        bbox = ", ".join(f"{v:.0f}" for v in t["bbox_en"])
        out.append(
            f"| `{t['tile']}` | {t['score']} | {t['stood_seconds']} | {t['looked_seconds']} | {bbox} |"
        )
    if not priority["missing_tiles"]:
        out.append("| none | | | | |")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--sessions", type=Path, default=work_root() / "telemetry", help="session-*.jsonl directory"
    )
    ap.add_argument(
        "--out", type=Path, help="priority.json path (default: <sessions>/priority.json)"
    )
    ap.add_argument("--hours", type=float, help="only samples from the last N hours")
    ap.add_argument(
        "--assembly",
        type=Path,
        default=data_root() / "facade-approvals/assembly",
        help="facade approval assembly (MANIFEST.json, facades/<building>/)",
    )
    a = ap.parse_args(argv)
    if not a.sessions.is_dir():
        print(f"no session directory {a.sessions}", file=sys.stderr)
        return 2
    since = time.time() - a.hours * 3600 if a.hours is not None else None
    names, samples = read_sessions(a.sessions, since)
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    priority = prioritize(names, samples, building_status(a.assembly), now)
    out = a.out or a.sessions / "priority.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(priority, indent=1) + "\n")
    out.with_suffix(".md").write_text(markdown(priority))
    print(
        f"{out}: {priority['sample_count']} samples, {len(priority['buildings'])} buildings, "
        f"{len(priority['missing_tiles'])} missing tiles"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
